import { app, shell } from 'electron';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import type { DesktopStatus, LogEntry } from '../shared/ipc.js';
import { appSupportRoot, backendPath, frontendDistPath } from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, execFileText } from './shell.js';
import { ProcessSupervisor } from './supervisor.js';
import { LocalServer } from './localServer.js';
import {
  type DesktopPorts,
  buildBackendEnv,
  desktopWorkspaceRoot,
  postgresDataDir,
  postgresBinaryPath,
  pythonBinaryPath,
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

export class DesktopManager {
  private readonly supervisor = new ProcessSupervisor();
  private readonly localServer = new LocalServer();
  private logWriter?: DailyLogWriter;
  private ports?: DesktopPorts;
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
    this.supervisor.setVirtualStatus({
      name: 'postgres',
      state: 'stopped',
      port: this.ports?.postgres,
      exitedAt: new Date().toISOString(),
    });
    this.supervisor.appendManagerLog('Stopped local Sentinel services');
    return this.emitStatus();
  }

  private async ensureInitialized(): Promise<void> {
    if (this.ports) return;
    this.logWriter = this.logWriter || new DailyLogWriter(app.getPath('logs'));
    await mkdir(runtimeOutputDir(), { recursive: true });
    await mkdir(qemuRunRoot(), { recursive: true });
    await mkdir(desktopWorkspaceRoot(), { recursive: true });
    await mkdir(postgresDataDir(), { recursive: true });
    await this.reapOrphanedQemuVms();
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
      await this.waitForPostgres();
      return;
    }
    await this.supervisor.start({
      name: 'postgres',
      command: postgresBin,
      args: ['-D', dataDir, '-p', String(this.ports!.postgres), '-h', '127.0.0.1'],
      env,
      port: this.ports!.postgres,
    });
    await this.waitForPostgres();
  }

  private async startBackend(): Promise<void> {
    if (this.supervisor.isRunning('backend')) return;
    const python = await this.resolvePythonBinary();
    await this.supervisor.start({
      name: 'backend',
      command: python,
      args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(this.ports!.backend)],
      cwd: backendPath(),
      env: buildBackendEnv(this.ports!),
      port: this.ports!.backend,
    });
  }

  private async existingPostgresProcess(): Promise<PostgresProcessInfo | undefined> {
    const pidPath = path.join(postgresDataDir(), 'postmaster.pid');
    if (!existsSync(pidPath)) return undefined;
    try {
      const raw = await readFile(pidPath, 'utf8');
      const lines = raw.split(/\r?\n/);
      const pid = Number(lines[0]);
      if (!Number.isInteger(pid) || pid <= 0) return undefined;
      process.kill(pid, 0);
      const pidDataDir = lines[1]?.trim();
      if (pidDataDir && path.resolve(pidDataDir) !== path.resolve(postgresDataDir())) {
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

  private async resolvePythonBinary(): Promise<string> {
    const bundled = pythonBinaryPath();
    if (existsSync(bundled)) return bundled;
    if (app.isPackaged) {
      throw new Error('Missing bundled Python runtime. Rebuild the desktop package.');
    }
    const fromPath = await commandExists('python3');
    if (fromPath) return fromPath;
    throw new Error('Missing Python runtime.');
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
  }
}

async function readFileSafe(filePath: string): Promise<string | undefined> {
  try {
    return await readFile(filePath, 'utf8');
  } catch {
    return undefined;
  }
}
