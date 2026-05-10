import { app, shell } from 'electron';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import readline from 'node:readline';
import type { CreateInstanceRequest, DesktopStatus, InstanceSummary, LogEntry } from '../shared/ipc.js';
import { appSupportRoot, backendPath, frontendDistPath, instanceEnvPath, instanceRoot, qemuResourcePath } from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, execFileText } from './shell.js';
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
    await mkdir(this.postgresDataDir(), { recursive: true });
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
    const root = instanceRoot(name);
    const envPath = instanceEnvPath(name);
    await mkdir(root, { recursive: true });
    await mkdir(path.join(root, 'workspaces'), { recursive: true });
    await mkdir(path.join(root, 'qemu-run'), { recursive: true });
    const existing = await readEnvFile(envPath);
    const values = {
      ...existing,
      STACK_PORT: String(request.stackPort || 5050),
      POSTGRES_DB: existing.POSTGRES_DB || 'sentinel',
      POSTGRES_USER: existing.POSTGRES_USER || 'sentinel',
      POSTGRES_PASSWORD: existing.POSTGRES_PASSWORD || randomSecret(24),
      JWT_SECRET_KEY: existing.JWT_SECRET_KEY || randomSecret(48),
      JWT_ALGORITHM: existing.JWT_ALGORITHM || 'HS256',
      RUNTIME_EXEC_BACKEND: 'qemu',
      RUNTIME_WORKSPACES_HOST_DIR: path.join(root, 'workspaces'),
      RUNTIME_QEMU_IMAGE: this.runtimeImagePath(),
      RUNTIME_QEMU_SSH_KEY_PATH: this.runtimeKeyPath(),
      RUNTIME_QEMU_WORKSPACE_ROOT: path.join(root, 'workspaces'),
      RUNTIME_QEMU_RUN_ROOT: path.join(root, 'qemu-run'),
    };
    await writeEnvFile(envPath, values);
    this.supervisor.appendManagerLog(`Created instance ${name}`);
    return this.emitStatus();
  }

  async deleteInstance(name: string): Promise<DesktopStatus> {
    if (this.activeInstance === name) {
      await this.stopInstance();
    }
    await rm(instanceRoot(this.sanitizeInstanceName(name)), { recursive: true, force: true });
    this.supervisor.appendManagerLog(`Deleted instance ${name}`);
    return this.emitStatus();
  }

  async startInstance(name: string): Promise<DesktopStatus> {
    const instance = this.sanitizeInstanceName(name);
    const envPath = instanceEnvPath(instance);
    if (!existsSync(envPath)) {
      await this.createInstance({ name: instance });
    }
    if (!this.ports) {
      await this.initialize();
    }
    if (!existsSync(this.runtimeImagePath()) || !existsSync(this.runtimeKeyPath())) {
      throw new Error('QEMU runtime image is missing. Build the QEMU image before starting the instance.');
    }

    await this.startPostgres();
    await this.startQemuBridge();
    await this.startBackend(instance);
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
    await this.localServer.stop();
    this.supervisor.setVirtualStatus({
      name: 'frontend',
      state: 'stopped',
      exitedAt: new Date().toISOString(),
    });
    this.supervisor.stop('backend');
    this.supervisor.stop('qemuBridge');
    this.activeInstance = undefined;
    this.supervisor.appendManagerLog('Stopped active instance');
    return this.emitStatus();
  }

  async restartInstance(name: string): Promise<DesktopStatus> {
    await this.stopInstance();
    return this.startInstance(name);
  }

  async resetAuth(name: string, username: string, password: string): Promise<DesktopStatus> {
    const envPath = instanceEnvPath(this.sanitizeInstanceName(name));
    const env = await readEnvFile(envPath);
    env.SENTINEL_AUTH_USERNAME = username;
    env.SENTINEL_AUTH_PASSWORD = password;
    await writeEnvFile(envPath, env);
    this.supervisor.appendManagerLog(`Queued auth reset for ${name}; restart required`);
    return this.emitStatus();
  }

  async backupInstance(name: string): Promise<string> {
    const safe = this.sanitizeInstanceName(name);
    const backupPath = path.join(appSupportRoot(), 'backups', `${safe}-${Date.now()}.tar.gz`);
    await mkdir(path.dirname(backupPath), { recursive: true });
    await execFileText('tar', ['-czf', backupPath, '-C', this.instancesRoot(), safe]);
    this.supervisor.appendManagerLog(`Created backup ${backupPath}`);
    return backupPath;
  }

  async restoreInstance(name: string, backupPath: string): Promise<DesktopStatus> {
    const safe = this.sanitizeInstanceName(name);
    await rm(instanceRoot(safe), { recursive: true, force: true });
    await mkdir(this.instancesRoot(), { recursive: true });
    await execFileText('tar', ['-xzf', backupPath, '-C', this.instancesRoot()]);
    this.supervisor.appendManagerLog(`Restored ${safe} from ${backupPath}`);
    return this.emitStatus();
  }

  async buildQemuImage(): Promise<void> {
    await mkdir(this.runtimeOutputDir(), { recursive: true });
    await this.runScript('manager', path.join(qemuResourcePath(), 'build-base-image.sh'), [], {
      SENTINEL_QEMU_OUTPUT_IMAGE_NAME: path.basename(this.runtimeImagePath()),
      SENTINEL_QEMU_OUTPUT_DIR: this.runtimeOutputDir(),
    });
    await this.emitStatus();
  }

  async validateQemuImage(): Promise<void> {
    await this.runScript('manager', path.join(qemuResourcePath(), 'validate-base-image.sh'), [], {
      SENTINEL_QEMU_VALIDATE_IMAGE: this.runtimeImagePath(),
      SENTINEL_QEMU_VALIDATE_KEY: this.runtimeKeyPath(),
    });
    await this.emitStatus();
  }

  async openSentinel(): Promise<void> {
    const url = (await this.getStatus()).appUrl;
    if (url) await shell.openExternal(url);
  }

  async revealAppSupport(): Promise<void> {
    await shell.openPath(appSupportRoot());
  }

  shutdown(): void {
    void this.localServer.stop();
    this.supervisor.stopAll();
  }

  private async startPostgres(): Promise<void> {
    if (this.supervisor.isRunning('postgres')) return;
    const postgresBin = await this.resolvePostgresBinary('postgres');
    const initdbBin = await this.resolvePostgresBinary('initdb');
    const dataDir = this.postgresDataDir();
    if (!existsSync(path.join(dataDir, 'PG_VERSION'))) {
      await execFileText(initdbBin, ['-D', dataDir, '-U', 'sentinel', '--encoding=UTF8']);
    }
    this.supervisor.start({
      name: 'postgres',
      command: postgresBin,
      args: ['-D', dataDir, '-p', String(this.ports!.postgres), '-h', '127.0.0.1'],
      port: this.ports!.postgres,
    });
    await this.waitForPostgres();
    await this.ensurePostgresDatabase();
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
      port: this.ports!.qemuBridge,
    });
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
        await execFileText(pgIsReady, ['-h', '127.0.0.1', '-p', String(this.ports!.postgres), '-U', 'sentinel']);
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    throw new Error('Postgres did not become ready');
  }

  private async ensurePostgresDatabase(): Promise<void> {
    const createdb = await this.resolvePostgresBinary('createdb');
    const psql = await this.resolvePostgresBinary('psql');
    const baseArgs = ['-h', '127.0.0.1', '-p', String(this.ports!.postgres), '-U', 'sentinel'];
    await execFileText(createdb, [...baseArgs, 'sentinel']).catch(() => '');
    await execFileText(psql, [...baseArgs, '-d', 'sentinel', '-c', 'CREATE EXTENSION IF NOT EXISTS vector;']);
  }

  private sanitizeInstanceName(name: string): string {
    return name.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
  }

  private instancesRoot(): string {
    return path.join(appSupportRoot(), 'instances');
  }

  private runtimeOutputDir(): string {
    return path.join(appSupportRoot(), 'runtime/qemu/output');
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
