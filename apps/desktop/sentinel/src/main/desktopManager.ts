import { app, shell } from 'electron';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { DesktopStatus, LogEntry } from '../shared/ipc.js';
import { appSupportRoot, backendPath, frontendDistPath, resourceRoot } from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, execFileText } from './shell.js';
import { ProcessSupervisor } from './supervisor.js';
import { LocalServer } from './localServer.js';
import {
  type DesktopSecrets,
  type DesktopPorts,
  buildBackendEnv,
  backendBinaryPath,
  desktopRunRoot,
  desktopWorkspaceRoot,
  postgresDataDir,
  postgresBinaryPath,
  qemuBinaryPath,
  qemuRunRoot,
  runtimeCommandPath,
  runtimeImagePath,
  runtimeKeyPath,
  runtimeOutputDir,
} from './runtimeConfig.js';
import { DailyLogWriter } from './logWriter.js';

interface PostgresProcessInfo {
  pid: number;
  port: number;
}

interface DesktopSecretsFile {
  jwtSecretKey?: string;
}

interface DesktopOwnerFile {
  pid?: number;
  resourceRoot?: string;
  startedAt?: string;
}

interface DesktopProcessEntry {
  pid: number;
  command: string;
}

type DesktopPidService = 'backend' | 'postgres' | 'qemu-build' | 'qemu-vm';

export class DesktopManager {
  private readonly supervisor = new ProcessSupervisor();
  private readonly localServer = new LocalServer();
  private logWriter?: DailyLogWriter;
  private ports?: DesktopPorts;
  private secrets?: DesktopSecrets;
  private statusListeners = new Set<(status: DesktopStatus) => void>();
  private logListeners = new Set<(entry: LogEntry) => void>();

  constructor() {
    this.supervisor.on('status', () => void this.emitStatus());
    this.supervisor.on('log', (entry: LogEntry) => {
      this.logWriter?.write(entry);
      for (const listener of this.logListeners) listener(entry);
    });
  }

  async initialize(): Promise<DesktopStatus> {
    await this.ensureInitialized();
    return this.startServices();
  }

  async startServices(): Promise<DesktopStatus> {
    await this.ensureInitialized();
    await this.startPostgres();
    await this.startBackend();
    await this.waitForBackend();
    if (!this.localServer.running) {
      await this.localServer.start({
        frontendDir: frontendDistPath(),
        backendPort: this.ports!.backend,
        listenPort: this.ports!.app,
      });
    }
    this.supervisor.setVirtualStatus({
      name: 'frontend',
      state: 'running',
      port: this.ports!.app,
      message: frontendDistPath(),
      startedAt: new Date().toISOString(),
    });
    return this.emitStatus();
  }

  async stopServices(): Promise<DesktopStatus> {
    await this.localServer.stop();
    this.supervisor.setVirtualStatus({
      name: 'frontend',
      state: 'stopped',
      port: this.ports?.app,
      exitedAt: new Date().toISOString(),
    });
    await this.supervisor.stopAll();
    await this.stopExistingPostgres();
    await this.clearPidFile('backend');
    await this.clearPidFile('postgres');
    await this.clearPidFile('qemu-build');
    await this.clearPidFile('qemu-vm');
    this.supervisor.setVirtualStatus({
      name: 'postgres',
      state: 'stopped',
      port: this.ports?.postgres,
      exitedAt: new Date().toISOString(),
    });
    this.supervisor.appendManagerLog('Stopped local Sentinel services');
    return this.emitStatus();
  }

  async resetAuth(): Promise<DesktopStatus> {
    await this.ensureInitialized();
    await this.startPostgres();
    const psql = await this.resolvePostgresBinary('psql');
    await execFileText(
      psql,
      [
        '-h',
        '127.0.0.1',
        '-p',
        String(this.ports!.postgres),
        '-U',
        'sentinel',
        '-d',
        'sentinel_manager',
        '-v',
        'ON_ERROR_STOP=1',
        '-c',
        [
          "DELETE FROM manager_settings WHERE key IN ('sentinel.auth.username', 'sentinel.auth.password_hash');",
          'DELETE FROM manager_revoked_tokens;',
        ].join(' '),
      ],
      { env: this.postgresEnv() },
    );
    this.secrets = await this.createDesktopSecrets();
    await this.supervisor.stopAndWait('backend');
    await this.startBackend();
    await this.waitForBackend();
    this.supervisor.appendManagerLog('Reset desktop auth; open Sentinel to create a new admin account.');
    return this.emitStatus();
  }

  private async ensureInitialized(): Promise<void> {
    if (this.ports) return;
    this.logWriter = this.logWriter || new DailyLogWriter(app.getPath('logs'));
    await mkdir(runtimeOutputDir(), { recursive: true });
    await mkdir(qemuRunRoot(), { recursive: true });
    await mkdir(desktopRunRoot(), { recursive: true });
    await mkdir(desktopWorkspaceRoot(), { recursive: true });
    await mkdir(postgresDataDir(), { recursive: true });
    await this.acquireDesktopOwnership();
    await this.reapStaleDesktopProcesses();
    await this.reapOrphanedQemuBuild();
    await this.reapOrphanedQemuVms();
    this.secrets = await this.loadDesktopSecrets();
    this.supervisor.appendManagerLog(`Desktop logs: ${app.getPath('logs')}`);
    this.ports = {
      app: await findFreePort(5070),
      backend: await findFreePort(18020),
      postgres: await findFreePort(15452),
      qemuSsh: await findFreePort(2247),
      qemuVnc: await findFreePort(16101),
      qemuCdp: await findFreePort(19244),
    };
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
    const qemuSystemPath = await this.resolveQemuStatusPath('qemu-system-aarch64');
    const qemuImgPath = await this.resolveQemuStatusPath('qemu-img');
    const qemuPresent = Boolean(qemuSystemPath && qemuImgPath);
    const imagePath = runtimeImagePath();
    const keyPath = runtimeKeyPath();
    const appUrl = this.ports && this.localServer.running ? this.appUrl() : undefined;
    return {
      appUrl,
      appSupportPath: appSupportRoot(),
      qemu: {
        installed: qemuPresent,
        qemuSystemPath: qemuSystemPath || qemuBinaryPath('qemu-system-aarch64'),
        qemuImgPath: qemuImgPath || qemuBinaryPath('qemu-img'),
        message: qemuPresent
          ? app.isPackaged ? 'Bundled QEMU runtime present' : 'QEMU detected on PATH'
          : app.isPackaged ? 'Bundled QEMU runtime missing. Rebuild the desktop package.' : 'QEMU is not available on PATH',
      },
      runtimeImage: {
        imagePath,
        keyPath,
        present: existsSync(imagePath) && existsSync(keyPath),
      },
      services: this.supervisor.status(),
    };
  }

  logs(): LogEntry[] {
    return this.supervisor.allLogs();
  }

  async revealAppSupport(): Promise<void> {
    await shell.openPath(appSupportRoot());
  }

  async shutdown(): Promise<void> {
    await this.localServer.stop();
    await this.supervisor.stopAll();
    await this.stopExistingPostgres();
    await this.clearPidFile('backend');
    await this.clearPidFile('postgres');
    await this.clearPidFile('qemu-build');
    await this.clearPidFile('qemu-vm');
    await this.releaseDesktopOwnership();
    await this.logWriter?.flush();
  }

  private async startPostgres(): Promise<void> {
    if (this.supervisor.isRunning('postgres')) return;
    const postgresBin = await this.resolvePostgresBinary('postgres');
    const initdbBin = await this.resolvePostgresBinary('initdb');
    const dataDir = postgresDataDir();
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
      await this.writePidFile('postgres', existingPostgres.pid, existingPostgres.port);
      await this.waitForPostgres();
      await this.terminateStalePostgresClients();
      return;
    }
    await this.supervisor.start({
      name: 'postgres',
      command: postgresBin,
      args: ['-D', dataDir, '-p', String(this.ports!.postgres), '-h', '127.0.0.1'],
      env,
      port: this.ports!.postgres,
    });
    const pid = this.supervisor.pid('postgres');
    if (pid) await this.writePidFile('postgres', pid, this.ports!.postgres);
    await this.waitForPostgres();
  }

  private async startBackend(): Promise<void> {
    if (this.supervisor.isRunning('backend')) return;
    const backend = await this.resolveBackendLaunch();
    await this.supervisor.start({
      name: 'backend',
      command: backend.command,
      args: backend.args,
      cwd: backend.cwd,
      env: buildBackendEnv(this.ports!, this.secrets!),
      port: this.ports!.backend,
    });
    const pid = this.supervisor.pid('backend');
    if (pid) await this.writePidFile('backend', pid, this.ports!.backend);
  }

  private async existingPostgresProcess(): Promise<PostgresProcessInfo | undefined> {
    const pidPath = path.join(postgresDataDir(), 'postmaster.pid');
    if (!existsSync(pidPath)) return undefined;
    try {
      const raw = await readFile(pidPath, 'utf8');
      const lines = raw.split(/\r?\n/);
      const pid = Number(lines[0]);
      if (!Number.isInteger(pid) || pid <= 0) return undefined;
      const pidDataDir = lines[1]?.trim();
      if (pidDataDir && path.resolve(pidDataDir) !== path.resolve(postgresDataDir())) {
        return undefined;
      }
      try {
        process.kill(pid, 0);
      } catch {
        await rm(pidPath, { force: true });
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
    await this.clearPidFile('postgres');
  }

  private async resolvePostgresBinary(name: string): Promise<string> {
    const bundled = postgresBinaryPath(name);
    if (existsSync(bundled)) return bundled;
    if (app.isPackaged) {
      throw new Error(`Missing bundled Postgres binary '${name}'. Rebuild the desktop package.`);
    }
    const fromPath = await commandExists(name);
    if (fromPath) return fromPath;
    throw new Error(`Missing Postgres binary '${name}'.`);
  }

  private async resolveBackendLaunch(): Promise<{ command: string; args: string[]; cwd: string }> {
    const bundled = backendBinaryPath();
    if (existsSync(bundled)) {
      return {
        command: bundled,
        args: ['--host', '127.0.0.1', '--port', String(this.ports!.backend)],
        cwd: path.dirname(path.dirname(bundled)),
      };
    }
    if (app.isPackaged) {
      throw new Error('Missing bundled backend executable. Rebuild the desktop package.');
    }
    const fromPath = await commandExists('python3');
    if (fromPath) {
      return {
        command: fromPath,
        args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(this.ports!.backend)],
        cwd: backendPath(),
      };
    }
    throw new Error('Missing Python runtime for local desktop development.');
  }

  private async resolveQemuStatusPath(name: string): Promise<string | undefined> {
    const bundled = qemuBinaryPath(name);
    if (app.isPackaged) return existsSync(bundled) ? bundled : undefined;
    return commandExists(name);
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
      const backend = this.supervisor.status().find((service) => service.name === 'backend');
      if (backend?.state === 'failed' || backend?.state === 'stopped') {
        throw new Error(`Backend exited before becoming ready.\n${this.backendStartupDetails()}`);
      }
      try {
        const response = await fetch(url);
        if (response.ok) return;
      } catch {
        // Backend is still starting.
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`Backend did not become ready.\n${this.backendStartupDetails()}\n${await this.describePostgresActivity()}`);
  }

  private postgresEnv(): NodeJS.ProcessEnv {
    return {
      ...process.env,
      PATH: runtimeCommandPath(),
      LANG: 'C',
      LC_ALL: 'C',
      LC_CTYPE: 'C',
    };
  }

  private appUrl(): string {
    return `http://127.0.0.1:${this.ports!.app}/`;
  }

  private ownerPath(): string {
    return path.join(desktopRunRoot(), 'owner.json');
  }

  private pidPath(service: DesktopPidService): string {
    return path.join(desktopRunRoot(), `${service}.pid.json`);
  }

  private async acquireDesktopOwnership(): Promise<void> {
    const existing = await readJsonFile<DesktopOwnerFile>(this.ownerPath());
    if (existing?.pid && existing.pid !== process.pid && isProcessAlive(existing.pid)) {
      this.supervisor.appendManagerLog(`Taking ownership from stale desktop owner pid=${existing.pid}`);
      await terminateProcess(existing.pid);
    }
    await writeFile(
      this.ownerPath(),
      `${JSON.stringify({ pid: process.pid, resourceRoot: resourceRoot(), startedAt: new Date().toISOString() }, null, 2)}\n`,
      { mode: 0o600 },
    );
  }

  private async releaseDesktopOwnership(): Promise<void> {
    const existing = await readJsonFile<DesktopOwnerFile>(this.ownerPath());
    if (existing?.pid === process.pid) {
      await rm(this.ownerPath(), { force: true });
    }
  }

  private async reapStaleDesktopProcesses(): Promise<void> {
    const entries = await listProcesses();
    const stale = entries.filter((entry) => {
      if (entry.pid === process.pid) return false;
      const command = entry.command;
      if (command.includes(' -m uvicorn app.main:app') && command.includes('Sentinel.app/Contents/Resources/python')) {
        return true;
      }
      if (command.includes('build-base-image.sh') && command.includes('Sentinel.app/Contents/Resources/runtime/qemu')) {
        return true;
      }
      if (command.includes('sentinel-qemu-builder') && command.includes(appSupportRoot())) {
        return true;
      }
      if (command.includes('sentinel-qemu-runtime') && command.includes(appSupportRoot())) {
        return true;
      }
      return false;
    });
    for (const entry of stale) {
      this.supervisor.appendManagerLog(`Stopping stale desktop process pid=${entry.pid}: ${entry.command.slice(0, 180)}`);
      await terminateProcess(entry.pid);
    }
    await this.reapPidFile('backend');
    await this.reapPidFile('qemu-build');
    await this.reapPidFile('qemu-vm');
  }

  private async reapPidFile(service: DesktopPidService): Promise<void> {
    const existing = await readJsonFile<{ pid?: number }>(this.pidPath(service));
    if (!existing?.pid || existing.pid === process.pid) return;
    if (isProcessAlive(existing.pid)) {
      this.supervisor.appendManagerLog(`Stopping stale ${service} pid=${existing.pid}`);
      await terminateProcess(existing.pid);
    }
    await this.clearPidFile(service);
  }

  private async writePidFile(service: DesktopPidService, pid: number, port?: number): Promise<void> {
    await mkdir(desktopRunRoot(), { recursive: true });
    await writeFile(
      this.pidPath(service),
      `${JSON.stringify({ pid, port, resourceRoot: resourceRoot(), startedAt: new Date().toISOString() }, null, 2)}\n`,
      { mode: 0o600 },
    );
  }

  private async clearPidFile(service: DesktopPidService): Promise<void> {
    await rm(this.pidPath(service), { force: true });
  }

  private async reapOrphanedQemuBuild(): Promise<void> {
    const pidFile = path.join(qemuRunRoot(), 'build.pid');
    const raw = await readFileSafe(pidFile);
    const pid = raw ? Number(raw.trim()) : NaN;
    if (Number.isInteger(pid) && pid > 0 && isProcessAlive(pid)) {
      this.supervisor.appendManagerLog(`Reaped orphaned QEMU build pid=${pid}`);
      await terminateProcess(pid);
    }
    await rm(pidFile, { force: true });
    await this.clearPidFile('qemu-build');
  }

  private async terminateStalePostgresClients(): Promise<void> {
    const psql = await this.resolvePostgresBinary('psql').catch(() => undefined);
    if (!psql) return;
    try {
      await execFileText(
        psql,
        [
          '-h',
          '127.0.0.1',
          '-p',
          String(this.ports!.postgres),
          '-U',
          'sentinel',
          '-d',
          'postgres',
          '-v',
          'ON_ERROR_STOP=1',
          '-c',
          [
            'SELECT pg_terminate_backend(pid)',
            'FROM pg_stat_activity',
            "WHERE usename = 'sentinel'",
            'AND pid <> pg_backend_pid()',
            'AND datname IS NOT NULL',
            "AND (datname = 'sentinel_manager' OR datname LIKE 'sentinel\\_%' ESCAPE '\\');",
          ].join(' '),
        ],
        { env: this.postgresEnv() },
      );
    } catch (error) {
      this.supervisor.appendManagerLog(`Could not terminate stale Postgres clients: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  private backendStartupDetails(): string {
    const backend = this.supervisor.status().find((service) => service.name === 'backend');
    const statusLine = backend
      ? `Backend status: ${backend.state}${backend.pid ? ` pid=${backend.pid}` : ''}${backend.exitCode !== undefined ? ` exit=${backend.exitCode}` : ''}`
      : 'Backend status: not started';
    const lastLogs = this.supervisor
      .allLogs()
      .filter((entry) => entry.service === 'backend')
      .slice(-25)
      .map((entry) => entry.line)
      .join('\n');
    return lastLogs ? `${statusLine}\nLast backend logs:\n${lastLogs}` : statusLine;
  }

  private async describePostgresActivity(): Promise<string> {
    const psql = await this.resolvePostgresBinary('psql').catch(() => undefined);
    if (!psql || !this.ports) return '';
    try {
      const output = await execFileText(
        psql,
        [
          '-h',
          '127.0.0.1',
          '-p',
          String(this.ports.postgres),
          '-U',
          'sentinel',
          '-d',
          'postgres',
          '-A',
          '-t',
          '-F',
          ' | ',
          '-c',
          [
            'SELECT pid, datname, state, COALESCE(wait_event_type, \'\'), COALESCE(wait_event, \'\'), left(query, 140)',
            'FROM pg_stat_activity',
            "WHERE usename = 'sentinel'",
            'AND pid <> pg_backend_pid()',
            'AND datname IS NOT NULL',
            "AND (datname = 'sentinel_manager' OR datname LIKE 'sentinel\\_%' ESCAPE '\\')",
            'ORDER BY pid;',
          ].join(' '),
        ],
        { env: this.postgresEnv() },
      );
      const trimmed = output.trim();
      return trimmed ? `Postgres Sentinel activity:\n${trimmed}` : 'Postgres Sentinel activity: none';
    } catch (error) {
      return `Postgres activity unavailable: ${error instanceof Error ? error.message : String(error)}`;
    }
  }

  private async loadDesktopSecrets(): Promise<DesktopSecrets> {
    const filePath = path.join(appSupportRoot(), 'config', 'secrets.json');
    const existing = await readJsonFile<DesktopSecretsFile>(filePath);
    if (existing?.jwtSecretKey && existing.jwtSecretKey.trim()) {
      return { jwtSecretKey: existing.jwtSecretKey.trim() };
    }
    return this.createDesktopSecrets();
  }

  private async createDesktopSecrets(): Promise<DesktopSecrets> {
    const filePath = path.join(appSupportRoot(), 'config', 'secrets.json');
    const secrets: DesktopSecrets = {
      jwtSecretKey: randomBytes(32).toString('hex'),
    };
    await mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
    await writeFile(filePath, `${JSON.stringify(secrets, null, 2)}\n`, { mode: 0o600 });
    return secrets;
  }

  private async emitStatus(): Promise<DesktopStatus> {
    const status = await this.getStatus();
    for (const listener of this.statusListeners) listener(status);
    return status;
  }

  private async reapOrphanedQemuVms(): Promise<void> {
    const root = qemuRunRoot();
    if (!existsSync(root)) return;
    const entries = await readFileSafe(path.join(root, 'vm.pid'));
    if (entries === undefined) return;
    const pid = Number(entries.trim());
    if (!Number.isInteger(pid) || pid <= 0) return;
    try {
      process.kill(pid, 0);
      process.kill(pid, 'SIGTERM');
      this.supervisor.appendManagerLog(`Reaped orphaned QEMU pid=${pid}`);
    } catch {
      // Process is already gone.
    }
    await rm(path.join(root, 'vm.pid'), { force: true });
    await this.clearPidFile('qemu-vm');
  }
}

async function listProcesses(): Promise<DesktopProcessEntry[]> {
  const output = await execFileText('/bin/ps', ['-axo', 'pid=,command=']).catch(() => '');
  return output
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/^\s*(\d+)\s+(.+)$/u);
      if (!match) return undefined;
      return { pid: Number(match[1]), command: match[2] };
    })
    .filter((entry): entry is DesktopProcessEntry => Boolean(entry && Number.isInteger(entry.pid)));
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function terminateProcess(pid: number): Promise<void> {
  try {
    process.kill(pid, 'SIGTERM');
  } catch {
    return;
  }
  for (let i = 0; i < 80; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (!isProcessAlive(pid)) return;
  }
  try {
    process.kill(pid, 'SIGKILL');
  } catch {
    // Already stopped.
  }
}

async function readFileSafe(filePath: string): Promise<string | undefined> {
  try {
    return await readFile(filePath, 'utf8');
  } catch {
    return undefined;
  }
}

async function readJsonFile<T>(filePath: string): Promise<T | undefined> {
  const raw = await readFileSafe(filePath);
  if (raw === undefined) return undefined;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return undefined;
  }
}
