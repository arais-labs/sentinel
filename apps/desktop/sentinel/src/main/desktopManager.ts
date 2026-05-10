import { app, shell } from 'electron';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { copyFile, cp, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash, pbkdf2Sync, randomBytes, randomUUID } from 'node:crypto';
import readline from 'node:readline';
import type { CreateInstanceRequest, DesktopStatus, InstanceSummary, LogEntry, RestoreInstanceRequest } from '../shared/ipc.js';
import { appSupportRoot, backendPath, frontendDistPath, instanceEnvPath, instanceRoot, qemuResourcePath } from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, commandSearchPath, execFileText } from './shell.js';
import { parseEnv, randomSecret, readEnvFile, serializeEnv, writeEnvFile } from './env.js';
import { ProcessSupervisor } from './supervisor.js';
import { LocalServer } from './localServer.js';

interface Ports {
  app: number;
  backend: number;
  postgres: number;
  qemuBridge: number;
  qemuSsh: number;
  qemuVnc: number;
  qemuCdp: number;
}

interface PostgresProcessInfo {
  pid: number;
  port: number;
}

const AUTH_USERNAME_KEY = 'sentinel.auth.username';
const AUTH_PASSWORD_HASH_KEY = 'sentinel.auth.password_hash';
const AUTH_PASSWORD_HASH_ROUNDS = 240_000;
const BACKUP_FORMAT = 'sentinel.instance.backup';
const BACKUP_VERSION = 1;

interface BackupManifest {
  format: typeof BACKUP_FORMAT;
  version: typeof BACKUP_VERSION;
  instanceName: string;
  databaseName: string;
  createdAt: string;
  source: 'desktop' | 'cli';
  workspacesIncluded: boolean;
}

export class DesktopManager {
  private readonly supervisor = new ProcessSupervisor();
  private readonly localServer = new LocalServer();
  private activeInstance?: string;
  private ports?: Ports;
  private statusListeners = new Set<(status: DesktopStatus) => void>();
  private logListeners = new Set<(entry: LogEntry) => void>();

  constructor() {
    this.supervisor.on('status', () => void this.emitStatus());
    this.supervisor.on('log', (entry: LogEntry) => {
      for (const listener of this.logListeners) listener(entry);
    });
  }

  async initialize(): Promise<void> {
    await mkdir(this.instancesRoot(), { recursive: true });
    await mkdir(this.runtimeOutputDir(), { recursive: true });
    await mkdir(this.runtimeCacheDir(), { recursive: true });
    await mkdir(this.runtimeBuildDir(), { recursive: true });
    await mkdir(this.runtimeRunDir(), { recursive: true });
    await mkdir(this.postgresDataDir(), { recursive: true });
    await this.migrateLegacyRuntimeOutput();
    this.ports = {
      app: await findFreePort(5050),
      backend: await findFreePort(18000),
      postgres: await findFreePort(15432),
      qemuBridge: await findFreePort(47481),
      qemuSsh: await findFreePort(2227),
      qemuVnc: await findFreePort(16081),
      qemuCdp: await findFreePort(19224),
    };
    await this.emitStatus();
  }

  onStatus(listener: (status: DesktopStatus) => void): () => void {
    this.statusListeners.add(listener);
    void this.getStatus().then(listener);
    return () => this.statusListeners.delete(listener);
  }

  onLog(listener: (entry: LogEntry) => void): () => void {
    this.logListeners.add(listener);
    return () => this.logListeners.delete(listener);
  }

  async getStatus(): Promise<DesktopStatus> {
    const qemuSystemPath = await commandExists('qemu-system-aarch64');
    const qemuImgPath = await commandExists('qemu-img');
    const imagePath = this.runtimeImagePath();
    const keyPath = this.runtimeKeyPath();
    const instances = await this.listInstances();
    return {
      appUrl: this.activeInstance && this.ports ? `http://127.0.0.1:${this.ports.app}/` : undefined,
      appSupportPath: appSupportRoot(),
      activeInstance: this.activeInstance,
      qemu: {
        installed: Boolean(qemuSystemPath && qemuImgPath),
        qemuSystemPath,
        qemuImgPath,
        message: qemuSystemPath && qemuImgPath ? 'QEMU detected' : 'Install QEMU with Homebrew: brew install qemu',
      },
      runtimeImage: {
        imagePath,
        keyPath,
        present: existsSync(imagePath) && existsSync(keyPath),
      },
      services: this.supervisor.status(),
      instances,
    };
  }

  logs(): LogEntry[] {
    return this.supervisor.allLogs();
  }

  async createInstance(request: CreateInstanceRequest): Promise<DesktopStatus> {
    const name = this.sanitizeInstanceName(request.name);
    if (!name) throw new Error('Instance name cannot be empty');
    const username = this.normalizeAuthUsername(request.username || '');
    const password = (request.password || '').trim();
    if (!username) throw new Error('Admin username cannot be empty');
    if (!password) throw new Error('Admin password cannot be empty');
    const root = instanceRoot(name);
    const envPath = instanceEnvPath(name);
    if (existsSync(root)) throw new Error(`Instance already exists: ${name}`);
    await mkdir(root, { recursive: true });
    await mkdir(path.join(root, 'workspaces'), { recursive: true });
    await mkdir(path.join(root, 'qemu-run'), { recursive: true });
    const existing = await readEnvFile(envPath);
    const values = {
      ...existing,
      STACK_PORT: String(request.stackPort || 5050),
      POSTGRES_DB: existing.POSTGRES_DB || this.instanceDatabaseName(name),
      POSTGRES_USER: existing.POSTGRES_USER || 'sentinel',
      POSTGRES_PASSWORD: existing.POSTGRES_PASSWORD || randomSecret(24),
      JWT_SECRET_KEY: existing.JWT_SECRET_KEY || randomSecret(48),
      JWT_ALGORITHM: existing.JWT_ALGORITHM || 'HS256',
      SENTINEL_AUTH_USERNAME: username,
      SENTINEL_AUTH_PASSWORD: password,
      RUNTIME_EXEC_BACKEND: 'qemu',
      RUNTIME_WORKSPACES_HOST_DIR: path.join(root, 'workspaces'),
      RUNTIME_QEMU_IMAGE: this.runtimeImagePath(),
      RUNTIME_QEMU_SSH_KEY_PATH: this.runtimeKeyPath(),
      RUNTIME_QEMU_WORKSPACE_ROOT: path.join(root, 'workspaces'),
      RUNTIME_QEMU_RUN_ROOT: path.join(root, 'qemu-run'),
    };
    await this.writeInstanceEnv(name, values);
    this.supervisor.appendManagerLog(`Created instance ${name}`);
    return this.emitStatus();
  }

  async deleteInstance(name: string): Promise<DesktopStatus> {
    const safe = this.sanitizeInstanceName(name);
    if (this.activeInstance === safe) {
      await this.stopInstance();
    }
    await this.startPostgres();
    await this.dropDatabase((await readEnvFile(instanceEnvPath(safe))).POSTGRES_DB || this.instanceDatabaseName(safe));
    await rm(instanceRoot(safe), { recursive: true, force: true });
    this.supervisor.appendManagerLog(`Deleted instance ${safe}`);
    return this.emitStatus();
  }

  async startInstance(name: string): Promise<DesktopStatus> {
    const instance = this.sanitizeInstanceName(name);
    const envPath = instanceEnvPath(instance);
    if (!existsSync(envPath)) {
      throw new Error(`Instance not found: ${instance}`);
    }
    if (!this.ports) {
      await this.initialize();
    }
    if (!existsSync(this.runtimeImagePath()) || !existsSync(this.runtimeKeyPath())) {
      throw new Error('QEMU runtime image is missing. Build the QEMU image before starting the instance.');
    }

    await this.startPostgres();
    await this.ensureInstanceDatabase(instance);
    await this.startQemuBridge();
    await this.startBackend(instance);
    await this.waitForBackend();
    await this.localServer.start({
      frontendDir: frontendDistPath(),
      backendPort: this.ports!.backend,
      listenPort: this.ports!.app,
    });
    this.supervisor.setVirtualStatus({
      name: 'frontend',
      state: 'running',
      port: this.ports!.app,
      message: frontendDistPath(),
      startedAt: new Date().toISOString(),
    });
    this.activeInstance = instance;
    this.supervisor.appendManagerLog(`Started instance ${instance}`);
    return this.emitStatus();
  }

  async stopInstance(): Promise<DesktopStatus> {
    const instance = this.activeInstance;
    await this.localServer.stop();
    this.supervisor.setVirtualStatus({
      name: 'frontend',
      state: 'stopped',
      exitedAt: new Date().toISOString(),
    });
    await this.supervisor.stopAndWait('backend');
    if (instance) {
      await this.stopQemuVm(instance);
    }
    await this.supervisor.stopAndWait('qemuBridge');
    this.activeInstance = undefined;
    this.supervisor.appendManagerLog('Stopped active instance');
    return this.emitStatus();
  }

  async restartInstance(name: string): Promise<DesktopStatus> {
    await this.stopInstance();
    return this.startInstance(name);
  }

  async renameInstance(name: string, newName: string): Promise<DesktopStatus> {
    const oldSafe = this.sanitizeInstanceName(name);
    const newSafe = this.sanitizeInstanceName(newName);
    if (!oldSafe || !newSafe) throw new Error('Instance name cannot be empty');
    if (oldSafe === newSafe) return this.emitStatus();
    if (this.activeInstance === oldSafe) {
      await this.stopInstance();
    }
    const oldRoot = instanceRoot(oldSafe);
    const newRoot = instanceRoot(newSafe);
    if (!existsSync(oldRoot)) throw new Error(`Instance not found: ${oldSafe}`);
    if (existsSync(newRoot)) throw new Error(`Instance already exists: ${newSafe}`);
    const oldEnv = await readEnvFile(instanceEnvPath(oldSafe));
    const oldDb = oldEnv.POSTGRES_DB || this.instanceDatabaseName(oldSafe);
    const newDb = this.instanceDatabaseName(newSafe);
    await this.startPostgres();
    await this.renameDatabaseIfExists(oldDb, newDb);
    await rename(oldRoot, newRoot);
    const nextEnv = await readEnvFile(instanceEnvPath(newSafe));
    nextEnv.POSTGRES_DB = newDb;
    await this.writeInstanceEnv(newSafe, nextEnv);
    this.supervisor.appendManagerLog(`Renamed instance ${oldSafe} to ${newSafe}`);
    return this.emitStatus();
  }

  async resetAuth(name: string, username: string, password: string): Promise<DesktopStatus> {
    const normalizedUsername = this.normalizeAuthUsername(username);
    const normalizedPassword = password.trim();
    if (!normalizedUsername) throw new Error('Admin username cannot be empty');
    if (!normalizedPassword) throw new Error('Admin password cannot be empty');
    const envPath = instanceEnvPath(this.sanitizeInstanceName(name));
    const env = await readEnvFile(envPath);
    env.SENTINEL_AUTH_USERNAME = normalizedUsername;
    env.SENTINEL_AUTH_PASSWORD = normalizedPassword;
    await writeEnvFile(envPath, env);

    await this.startPostgres();
    await this.ensureInstanceDatabase(this.sanitizeInstanceName(name));
    try {
      await this.upsertAuthSettings(env.POSTGRES_DB || this.instanceDatabaseName(this.sanitizeInstanceName(name)), normalizedUsername, normalizedPassword);
    } catch (error) {
      const message = String((error as Error).message || error);
      if (!message.includes('system_settings')) throw error;
      this.supervisor.appendManagerLog('Auth database settings table is not initialized yet; env seed was updated');
    }
    this.supervisor.appendManagerLog(`Reset admin credentials for ${name}`);
    return this.emitStatus();
  }

  async backupInstance(name: string): Promise<string> {
    const safe = this.sanitizeInstanceName(name);
    const env = await readEnvFile(instanceEnvPath(safe));
    const dbName = env.POSTGRES_DB || this.instanceDatabaseName(safe);
    const backupPath = path.join(appSupportRoot(), 'backups', `${safe}-${Date.now()}.sentinel-backup.tar.gz`);
    const tmp = await mkdtemp(path.join(appSupportRoot(), 'backup-build-'));
    try {
      await mkdir(path.dirname(backupPath), { recursive: true });
      await this.startPostgres();
      await this.ensureInstanceDatabase(safe);
      await writeFile(path.join(tmp, 'sentinel-backup.json'), JSON.stringify({
        format: BACKUP_FORMAT,
        version: BACKUP_VERSION,
        instanceName: safe,
        databaseName: dbName,
        createdAt: new Date().toISOString(),
        source: 'desktop',
        workspacesIncluded: true,
      } satisfies BackupManifest, null, 2));
      await writeFile(path.join(tmp, 'instance.env'), serializeEnv(env));
      await this.dumpDatabase(dbName, path.join(tmp, 'database.sql'));
      await cp(path.join(instanceRoot(safe), 'workspaces'), path.join(tmp, 'workspaces'), { recursive: true });
      await execFileText('tar', ['-czf', backupPath, '-C', tmp, '.']);
    } finally {
      await rm(tmp, { recursive: true, force: true });
    }
    this.supervisor.appendManagerLog(`Created backup ${backupPath}`);
    return backupPath;
  }

  async restoreInstance(request: RestoreInstanceRequest): Promise<DesktopStatus> {
    const safe = this.sanitizeInstanceName(request.name);
    const backupPath = request.backupPath.trim();
    if (!safe) throw new Error('Instance name cannot be empty');
    if (!backupPath) throw new Error('Backup path cannot be empty');
    if (existsSync(instanceRoot(safe))) throw new Error(`Instance already exists: ${safe}`);
    const dbName = this.instanceDatabaseName(safe);
    const tmp = await mkdtemp(path.join(appSupportRoot(), 'backup-restore-'));
    let databaseCreated = false;
    let restored = false;
    try {
      await execFileText('tar', ['-xzf', backupPath, '-C', tmp]);
      const manifest = JSON.parse(await readFile(path.join(tmp, 'sentinel-backup.json'), 'utf8')) as BackupManifest;
      if (manifest.format !== BACKUP_FORMAT || manifest.version !== BACKUP_VERSION) {
        throw new Error('Unsupported Sentinel backup format');
      }
      const env = this.normalizeRestoredInstanceEnv(parseEnv(await readFile(path.join(tmp, 'instance.env'), 'utf8')));
      await mkdir(instanceRoot(safe), { recursive: true });
      await mkdir(path.join(instanceRoot(safe), 'qemu-run'), { recursive: true });
      await cp(path.join(tmp, 'workspaces'), path.join(instanceRoot(safe), 'workspaces'), { recursive: true });
      await this.writeInstanceEnv(safe, {
        ...env,
        POSTGRES_DB: dbName,
        POSTGRES_USER: 'sentinel',
        POSTGRES_PASSWORD: this.desktopPostgresPassword(),
      });
      await this.startPostgres();
      if (await this.databaseExists(dbName)) {
        throw new Error(`Database already exists for restored instance: ${dbName}`);
      }
      await this.createEmptyDatabase(dbName);
      databaseCreated = true;
      await this.restoreDatabase(dbName, path.join(tmp, 'database.sql'));
      restored = true;
    } finally {
      await rm(tmp, { recursive: true, force: true });
      if (!restored) {
        await rm(instanceRoot(safe), { recursive: true, force: true });
        if (databaseCreated) {
          await this.dropDatabase(dbName).catch(() => undefined);
        }
      }
    }
    this.supervisor.appendManagerLog(`Restored instance ${safe} from ${backupPath}`);
    return this.emitStatus();
  }

  async buildQemuImage(): Promise<void> {
    await mkdir(this.runtimeOutputDir(), { recursive: true });
    await mkdir(this.runtimeCacheDir(), { recursive: true });
    await mkdir(this.runtimeBuildDir(), { recursive: true });
    await mkdir(this.runtimeRunDir(), { recursive: true });
    await this.runScript('manager', path.join(qemuResourcePath(), 'build-base-image.sh'), [], {
      SENTINEL_QEMU_OUTPUT_IMAGE_NAME: path.basename(this.runtimeImagePath()),
      SENTINEL_QEMU_OUTPUT_DIR: this.runtimeOutputDir(),
      SENTINEL_QEMU_CACHE_DIR: this.runtimeCacheDir(),
      SENTINEL_QEMU_BUILD_ROOT: this.runtimeBuildDir(),
      SENTINEL_QEMU_RUN_DIR: this.runtimeRunDir(),
    });
    await this.emitStatus();
  }

  async validateQemuImage(): Promise<void> {
    await mkdir(this.runtimeValidateRunDir(), { recursive: true });
    await this.runScript('manager', path.join(qemuResourcePath(), 'validate-base-image.sh'), [], {
      SENTINEL_QEMU_VALIDATE_IMAGE: this.runtimeImagePath(),
      SENTINEL_QEMU_VALIDATE_KEY: this.runtimeKeyPath(),
      SENTINEL_QEMU_VALIDATE_RUN_DIR: this.runtimeValidateRunDir(),
    });
    await this.emitStatus();
  }

  async revealAppSupport(): Promise<void> {
    await shell.openPath(appSupportRoot());
  }

  async shutdown(): Promise<void> {
    await this.localServer.stop();
    if (this.activeInstance) {
      await this.stopQemuVm(this.activeInstance);
    }
    await this.supervisor.stopAll();
    await this.stopExistingPostgres();
  }

  private async startPostgres(): Promise<void> {
    if (this.supervisor.isRunning('postgres')) return;
    const postgresBin = await this.resolvePostgresBinary('postgres');
    const initdbBin = await this.resolvePostgresBinary('initdb');
    const dataDir = this.postgresDataDir();
    const env = this.postgresEnv();
    if (!existsSync(path.join(dataDir, 'PG_VERSION'))) {
      await execFileText(initdbBin, ['-D', dataDir, '-U', 'sentinel', '--encoding=UTF8'], { env });
    }
    const existingPostgres = await this.existingPostgresProcess();
    if (existingPostgres !== undefined) {
      this.ports!.postgres = existingPostgres.port;
      this.supervisor.setVirtualStatus({
        name: 'postgres',
        state: 'running',
        pid: existingPostgres.pid,
        port: existingPostgres.port,
        message: dataDir,
        startedAt: new Date().toISOString(),
      });
      await this.waitForPostgres();
      return;
    }
    this.supervisor.start({
      name: 'postgres',
      command: postgresBin,
      args: ['-D', dataDir, '-p', String(this.ports!.postgres), '-h', '127.0.0.1'],
      env,
      port: this.ports!.postgres,
    });
    await this.waitForPostgres();
  }

  private async existingPostgresProcess(): Promise<PostgresProcessInfo | undefined> {
    const pidPath = path.join(this.postgresDataDir(), 'postmaster.pid');
    if (!existsSync(pidPath)) return undefined;
    try {
      const raw = await readFile(pidPath, 'utf8');
      const lines = raw.split(/\r?\n/);
      const [firstLine] = lines;
      const pid = Number(firstLine);
      if (!Number.isInteger(pid) || pid <= 0) return undefined;
      process.kill(pid, 0);
      const pidDataDir = lines[1]?.trim();
      if (pidDataDir && path.resolve(pidDataDir) !== path.resolve(this.postgresDataDir())) {
        return undefined;
      }
      const port = Number(lines[3]);
      return {
        pid,
        port: Number.isInteger(port) && port > 0 ? port : this.ports!.postgres,
      };
    } catch {
      return undefined;
    }
  }

  private async stopExistingPostgres(): Promise<void> {
    const info = await this.existingPostgresProcess();
    if (info === undefined) return;
    try {
      process.kill(info.pid, 'SIGTERM');
      for (let i = 0; i < 80; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        try {
          process.kill(info.pid, 0);
        } catch {
          return;
        }
      }
      process.kill(info.pid, 'SIGKILL');
    } catch {
      // Already stopped or not owned by this process.
    }
  }

  private async startBackend(instance: string): Promise<void> {
    const env = await this.backendEnv(instance);
    const python = await this.resolvePythonBinary();
    this.supervisor.start({
      name: 'backend',
      command: python,
      args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(this.ports!.backend)],
      cwd: backendPath(),
      env,
      port: this.ports!.backend,
    });
  }

  private async startQemuBridge(): Promise<void> {
    if (this.supervisor.isRunning('qemuBridge')) return;
    const python = (await commandExists('python3')) || 'python3';
    const token = randomUUID();
    process.env.SENTINEL_DESKTOP_QEMU_BRIDGE_TOKEN = token;
    this.supervisor.start({
      name: 'qemuBridge',
      command: python,
      args: [path.join(qemuResourcePath(), 'bridge.py'), '--host', '127.0.0.1', '--port', String(this.ports!.qemuBridge), '--token', token],
      env: {
        ...process.env,
        PATH: commandSearchPath(),
      },
      port: this.ports!.qemuBridge,
    });
  }

  private async stopQemuVm(instance: string): Promise<void> {
    if (!this.ports || !this.supervisor.isRunning('qemuBridge')) return;
    const token = process.env.SENTINEL_DESKTOP_QEMU_BRIDGE_TOKEN || '';
    if (!token) return;
    try {
      await fetch(`http://127.0.0.1:${this.ports.qemuBridge}/v1/qemu/stop`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Sentinel-Bridge-Token': token,
        },
        body: JSON.stringify({ run_root: path.join(instanceRoot(instance), 'qemu-run') }),
      });
    } catch (error) {
      this.supervisor.appendManagerLog(`Could not stop QEMU VM for ${instance}: ${String((error as Error).message || error)}`);
    }
  }

  private async backendEnv(instance: string): Promise<NodeJS.ProcessEnv> {
    const envPath = instanceEnvPath(instance);
    const values = await readEnvFile(envPath);
    const password = values.POSTGRES_PASSWORD || 'sentinel';
    const db = values.POSTGRES_DB || 'sentinel';
    const user = values.POSTGRES_USER || 'sentinel';
    return {
      ...process.env,
      ...values,
      PATH: commandSearchPath(values.PATH || process.env.PATH || ''),
      LANG: values.LANG || process.env.LANG || 'C',
      LC_ALL: values.LC_ALL || process.env.LC_ALL || 'C',
      APP_ENV: 'desktop',
      DATABASE_URL: `postgresql+asyncpg://${user}:${password}@127.0.0.1:${this.ports!.postgres}/${db}`,
      RUNTIME_EXEC_BACKEND: 'qemu',
      RUNTIME_QEMU_IMAGE: this.runtimeImagePath(),
      RUNTIME_QEMU_SSH_KEY_PATH: this.runtimeKeyPath(),
      RUNTIME_QEMU_WORKSPACE_ROOT: path.join(instanceRoot(instance), 'workspaces'),
      RUNTIME_QEMU_RUN_ROOT: path.join(instanceRoot(instance), 'qemu-run'),
      RUNTIME_QEMU_BRIDGE_URL: `http://127.0.0.1:${this.ports!.qemuBridge}`,
      RUNTIME_QEMU_BRIDGE_TOKEN: process.env.SENTINEL_DESKTOP_QEMU_BRIDGE_TOKEN || '',
      RUNTIME_QEMU_HOST: '127.0.0.1',
      RUNTIME_QEMU_PUBLIC_HOST: '127.0.0.1',
      RUNTIME_QEMU_SSH_PORT: String(this.ports!.qemuSsh),
      RUNTIME_QEMU_VNC_PORT: String(this.ports!.qemuVnc),
      RUNTIME_QEMU_CDP_PORT: String(this.ports!.qemuCdp),
      RUNTIME_WORKSPACES_HOST_DIR: path.join(instanceRoot(instance), 'workspaces'),
      AUTH_COOKIE_SECURE: 'false',
      AUTH_COOKIE_SAMESITE: 'lax',
    };
  }

  private async listInstances(): Promise<InstanceSummary[]> {
    await mkdir(this.instancesRoot(), { recursive: true });
    const entries = await readdir(this.instancesRoot(), { withFileTypes: true });
    const result: InstanceSummary[] = [];
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const name = entry.name;
      const env = await readEnvFile(instanceEnvPath(name));
      const running = this.activeInstance === name && this.supervisor.isRunning('backend');
      result.push({
        name,
        backend: 'qemu',
        stackPort: Number(env.STACK_PORT || 5050),
        state: running ? 'running' : 'stopped',
        configPath: instanceEnvPath(name),
        workspacePath: path.join(instanceRoot(name), 'workspaces'),
        qemuRunPath: path.join(instanceRoot(name), 'qemu-run'),
        databaseName: env.POSTGRES_DB || this.instanceDatabaseName(name),
      });
    }
    return result.sort((a, b) => a.name.localeCompare(b.name));
  }

  private async emitStatus(): Promise<DesktopStatus> {
    const status = await this.getStatus();
    for (const listener of this.statusListeners) listener(status);
    return status;
  }

  private async runScript(service: 'manager', script: string, args: string[] = [], env: NodeJS.ProcessEnv = {}): Promise<void> {
    this.supervisor.appendManagerLog(`Running ${script} ${args.join(' ')}`);
    await new Promise<void>((resolve, reject) => {
      const child = spawn(script, args, {
        cwd: qemuResourcePath(),
        env: {
          ...process.env,
          ...env,
          PATH: commandSearchPath(env.PATH || process.env.PATH || ''),
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      const attach = (stream: NodeJS.ReadableStream) => {
        const rl = readline.createInterface({ input: stream });
        rl.on('line', (line) => this.supervisor.appendManagerLog(line));
      };
      attach(child.stdout);
      attach(child.stderr);
      child.once('error', reject);
      child.once('exit', (code) => {
        if (code === 0) {
          resolve();
          return;
        }
        reject(new Error(`${script} exited with code ${code ?? 'signal'}`));
      });
    });
    this.supervisor.appendManagerLog(`Completed ${script}`);
  }

  private async resolvePostgresBinary(name: string): Promise<string> {
    const bundled = path.join(process.resourcesPath || '', 'postgres/bin', name);
    if (existsSync(bundled)) return bundled;
    const fromPath = await commandExists(name);
    if (fromPath) return fromPath;
    throw new Error(`Missing Postgres binary '${name}'. Packaged builds must include resources/postgres/bin/${name}.`);
  }

  private async resolvePythonBinary(): Promise<string> {
    const bundled = path.join(process.resourcesPath || '', 'python/bin/python3');
    if (existsSync(bundled)) return bundled;
    const fromPath = await commandExists('python3');
    if (fromPath) return fromPath;
    throw new Error('Missing Python runtime. Packaged builds must include resources/python/bin/python3.');
  }

  private async waitForPostgres(): Promise<void> {
    const pgIsReady = await this.resolvePostgresBinary('pg_isready').catch(() => undefined);
    if (!pgIsReady) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      return;
    }
    for (let i = 0; i < 60; i += 1) {
      try {
        await execFileText(pgIsReady, ['-h', '127.0.0.1', '-p', String(this.ports!.postgres), '-U', 'sentinel', '-d', 'postgres'], {
          env: this.postgresEnv(),
        });
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    throw new Error('Postgres did not become ready');
  }

  private async waitForBackend(): Promise<void> {
    const url = `http://127.0.0.1:${this.ports!.backend}/health`;
    for (let i = 0; i < 120; i += 1) {
      try {
        const response = await fetch(url);
        if (response.ok) return;
      } catch {
        // Backend is still starting.
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error('Backend did not become ready');
  }

  private async ensureInstanceDatabase(instance: string): Promise<void> {
    const env = await readEnvFile(instanceEnvPath(instance));
    const dbName = env.POSTGRES_DB || this.instanceDatabaseName(instance);
    await this.createEmptyDatabase(dbName);
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(psql, [...this.databaseArgs(dbName), '-c', 'CREATE EXTENSION IF NOT EXISTS vector;'], { env: this.postgresEnv() });
  }

  private async createEmptyDatabase(dbName: string): Promise<void> {
    this.assertDatabaseName(dbName);
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(
      psql,
      [...this.maintenanceDatabaseArgs()],
      {
        env: this.postgresEnv(),
        input: `SELECT 'CREATE DATABASE ${dbName}' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${dbName}')\\gexec\n`,
      },
    );
  }

  private async dropDatabase(dbName: string): Promise<void> {
    this.assertDatabaseName(dbName);
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(psql, [...this.maintenanceDatabaseArgs()], {
      env: this.postgresEnv(),
      input: `
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${dbName}';
DROP DATABASE IF EXISTS ${dbName};
`,
    });
  }

  private async renameDatabaseIfExists(oldName: string, newName: string): Promise<void> {
    this.assertDatabaseName(oldName);
    this.assertDatabaseName(newName);
    if (!(await this.databaseExists(oldName))) return;
    if (await this.databaseExists(newName)) {
      throw new Error(`Database already exists for renamed instance: ${newName}`);
    }
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(psql, [...this.maintenanceDatabaseArgs()], {
      env: this.postgresEnv(),
      input: `
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${oldName}';
ALTER DATABASE ${oldName} RENAME TO ${newName};
`,
    });
  }

  private async databaseExists(dbName: string): Promise<boolean> {
    this.assertDatabaseName(dbName);
    const psql = await this.resolvePostgresBinary('psql');
    const output = await execFileText(psql, [...this.maintenanceDatabaseArgs(), '-tAc', `SELECT 1 FROM pg_database WHERE datname = '${dbName}'`], {
      env: this.postgresEnv(),
    });
    return output.trim() === '1';
  }

  private async dumpDatabase(dbName: string, outputPath: string): Promise<void> {
    this.assertDatabaseName(dbName);
    const pgDump = await this.resolvePostgresBinary('pg_dump');
    await execFileText(pgDump, [...this.databaseArgs(dbName), '--no-owner', '--no-privileges', '-f', outputPath], {
      env: this.postgresEnv(),
    });
  }

  private async restoreDatabase(dbName: string, inputPath: string): Promise<void> {
    this.assertDatabaseName(dbName);
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(psql, [...this.databaseArgs(dbName), '-f', inputPath], { env: this.postgresEnv() });
  }

  private maintenanceDatabaseArgs(): string[] {
    return ['-h', '127.0.0.1', '-p', String(this.ports!.postgres), '-U', 'sentinel', '-d', 'postgres'];
  }

  private databaseArgs(dbName: string): string[] {
    this.assertDatabaseName(dbName);
    return ['-h', '127.0.0.1', '-p', String(this.ports!.postgres), '-U', 'sentinel', '-d', dbName];
  }

  private postgresEnv(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      PATH: commandSearchPath(),
      LANG: 'C',
      LC_ALL: 'C',
      LC_CTYPE: 'C',
    };
  }

  private async upsertAuthSettings(dbName: string, username: string, password: string): Promise<void> {
    const psql = await this.resolvePostgresBinary('psql');
    const sql = `
INSERT INTO system_settings(key, value)
VALUES
  ('${AUTH_USERNAME_KEY}', :'username'),
  ('${AUTH_PASSWORD_HASH_KEY}', :'password_hash')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
`;
    await execFileText(
      psql,
      [...this.databaseArgs(dbName), '-v', `username=${username}`, '-v', `password_hash=${this.hashAuthPassword(password)}`],
      { env: this.postgresEnv(), input: sql },
    );
  }

  private normalizeAuthUsername(username: string): string {
    return username.trim().toLowerCase();
  }

  private hashAuthPassword(password: string): string {
    const salt = randomBytes(16).toString('hex');
    const digest = pbkdf2Sync(password, salt, AUTH_PASSWORD_HASH_ROUNDS, 32, 'sha256').toString('hex');
    return `${salt}$${digest}`;
  }

  private async migrateLegacyRuntimeOutput(): Promise<void> {
    const files = [
      'sentinel-runtime-base-arm64.qcow2',
      'sentinel-runtime-base-arm64.id_ed25519',
      'sentinel-runtime-base-arm64.id_ed25519.pub',
    ];
    for (const file of files) {
      const currentPath = path.join(this.runtimeOutputDir(), file);
      const legacyPath = path.join(this.legacyRuntimeOutputDir(), file);
      if (existsSync(currentPath) || !existsSync(legacyPath)) continue;
      await mkdir(this.runtimeOutputDir(), { recursive: true });
      await copyFile(legacyPath, currentPath);
      this.supervisor.appendManagerLog(`Migrated QEMU artifact ${file}`);
    }
  }

  private sanitizeInstanceName(name: string): string {
    return name.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  }

  private instanceDatabaseName(name: string): string {
    const safe = this.sanitizeInstanceName(name).replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'instance';
    const hash = createHash('sha256').update(name).digest('hex').slice(0, 8);
    return `sentinel_${safe.slice(0, 40)}_${hash}`;
  }

  private assertDatabaseName(dbName: string): void {
    if (!/^sentinel_[a-z0-9_]+_[a-f0-9]{8}$/.test(dbName)) {
      throw new Error(`Invalid Sentinel database name: ${dbName}`);
    }
  }

  private async writeInstanceEnv(name: string, values: Record<string, string>): Promise<void> {
    const root = instanceRoot(name);
    const env = {
      ...values,
      POSTGRES_DB: values.POSTGRES_DB || this.instanceDatabaseName(name),
      POSTGRES_USER: values.POSTGRES_USER || 'sentinel',
      POSTGRES_PASSWORD: values.POSTGRES_PASSWORD || randomSecret(24),
      JWT_SECRET_KEY: values.JWT_SECRET_KEY || randomSecret(48),
      JWT_ALGORITHM: values.JWT_ALGORITHM || 'HS256',
      RUNTIME_EXEC_BACKEND: 'qemu',
      RUNTIME_WORKSPACES_HOST_DIR: path.join(root, 'workspaces'),
      RUNTIME_QEMU_IMAGE: this.runtimeImagePath(),
      RUNTIME_QEMU_SSH_KEY_PATH: this.runtimeKeyPath(),
      RUNTIME_QEMU_WORKSPACE_ROOT: path.join(root, 'workspaces'),
      RUNTIME_QEMU_RUN_ROOT: path.join(root, 'qemu-run'),
    };
    await writeEnvFile(instanceEnvPath(name), env);
  }

  private normalizeRestoredInstanceEnv(values: Record<string, string>): Record<string, string> {
    const env = { ...values };
    for (const key of Object.keys(env)) {
      if (key.startsWith('RUNTIME_MULTIPASS_')) delete env[key];
      if (key === 'RUNTIME_QEMU_BRIDGE_PORT' || key === 'RUNTIME_QEMU_BRIDGE_TOKEN' || key === 'RUNTIME_QEMU_BRIDGE_URL') delete env[key];
    }
    delete env.DATABASE_URL;
    delete env.POSTGRES_USER;
    delete env.POSTGRES_PASSWORD;
    return env;
  }

  private desktopPostgresPassword(): string {
    return 'sentinel';
  }

  private instancesRoot(): string {
    return path.join(appSupportRoot(), 'instances');
  }

  private runtimeOutputDir(): string {
    return path.join(appSupportRoot(), 'qemu/output');
  }

  private legacyRuntimeOutputDir(): string {
    return path.join(appSupportRoot(), 'runtime/qemu/output');
  }

  private runtimeCacheDir(): string {
    return path.join(appSupportRoot(), 'qemu/cache');
  }

  private runtimeBuildDir(): string {
    return path.join(appSupportRoot(), 'qemu/build');
  }

  private runtimeRunDir(): string {
    return path.join(appSupportRoot(), 'qemu/run');
  }

  private runtimeValidateRunDir(): string {
    return path.join(this.runtimeRunDir(), 'validate');
  }

  private runtimeImagePath(): string {
    return path.join(this.runtimeOutputDir(), 'sentinel-runtime-base-arm64.qcow2');
  }

  private runtimeKeyPath(): string {
    return path.join(this.runtimeOutputDir(), 'sentinel-runtime-base-arm64.id_ed25519');
  }

  private postgresDataDir(): string {
    return path.join(appSupportRoot(), 'postgres/data');
  }
}
