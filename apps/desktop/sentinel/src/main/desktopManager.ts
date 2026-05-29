import { app, safeStorage, shell } from 'electron';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type {
  DesktopStatus,
  FactoryResetScopes,
  LogEntry,
  PayloadFailure,
  PayloadInfo,
  PayloadPhase,
  PayloadProgress,
  PayloadUpdate,
  ReleaseChannel,
} from '../shared/ipc.js';
import * as payload from './payloadManager.js';
import {
  hostStateRoot,
  backendPath,
  frontendDistPath,
  payloadRoot,
  payloadStagingRoot,
  resourceRoot,
} from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, execFileText } from './shell.js';
import { ProcessSupervisor } from './supervisor.js';
import { LocalServer } from './localServer.js';
import {
  type DesktopSecrets,
  type DesktopPorts,
  buildBackendEnv,
  desktopRunRoot,
  desktopWorkspaceRoot,
  postgresDataDir,
  postgresBinaryPath,
  postgresSharePath,
  runtimeCommandPath,
  shellPythonBinary,
} from './desktopConfig.js';
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

type DesktopPidService = 'backend' | 'postgres';

export class DesktopManager {
  private readonly supervisor = new ProcessSupervisor();
  private readonly localServer = new LocalServer();
  private logWriter?: DailyLogWriter;
  private ports?: DesktopPorts;
  private secrets?: DesktopSecrets;
  private statusListeners = new Set<(status: DesktopStatus) => void>();
  private logListeners = new Set<(entry: LogEntry) => void>();
  private payloadProgressListeners = new Set<(progress: PayloadProgress) => void>();
  private payloadInstalledListeners = new Set<(info: PayloadInfo) => void>();
  private payloadFailedListeners = new Set<(failure: PayloadFailure) => void>();

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

  // Packaged builds with no installed payload have nothing to run yet — the
  // user must load one from file (or download an update). Dev builds always run
  // against the repo source.
  private hasRunnablePayload(): boolean {
    return !app.isPackaged || payload.isInstalled();
  }

  async startServices(): Promise<DesktopStatus> {
    await this.ensureInitialized();
    if (!this.hasRunnablePayload()) {
      this.supervisor.appendManagerLog('No app payload installed; load one to start Sentinel.');
      return this.emitStatus();
    }
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

  // sirv snapshots dist/ at start; bounce it after a payload swap.
  private async restartLocalServerIfRunning(): Promise<void> {
    if (!this.localServer.running || !this.ports) return;
    await this.localServer.start({
      frontendDir: frontendDistPath(),
      backendPort: this.ports.backend,
      listenPort: this.ports.app,
    });
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
    await this.rotateJwtSecret();
    this.secrets = await this.loadDesktopSecrets();
    await this.supervisor.stopAndWait('backend');
    await this.startBackend();
    await this.waitForBackend();
    this.supervisor.appendManagerLog('Reset desktop auth; open Sentinel to create a new admin account.');
    return this.emitStatus();
  }

  async factoryReset(scopes: FactoryResetScopes | undefined): Promise<DesktopStatus> {
    const resetScopes: FactoryResetScopes = {
      db: Boolean(scopes?.db),
      runtimeData: Boolean(scopes?.runtimeData),
      appRuntime: Boolean(scopes?.appRuntime),
      logs: Boolean(scopes?.logs),
    };
    if (!resetScopes.db && !resetScopes.runtimeData && !resetScopes.appRuntime && !resetScopes.logs) {
      throw new Error('Select at least one factory reset scope.');
    }
    await this.stopServices();
    await this.releaseDesktopOwnership();
    if (resetScopes.db) {
      await rm(path.join(hostStateRoot(), 'postgres'), { recursive: true, force: true });
    }
    if (resetScopes.runtimeData) {
      await rm(desktopWorkspaceRoot(), { recursive: true, force: true });
      await rm(desktopRunRoot(), { recursive: true, force: true });
    }
    if (resetScopes.appRuntime) {
      // Drop the installed payload (and any half-applied swap state). The app
      // returns to the no-payload state; the user reinstalls from file/update.
      await rm(payloadRoot(), { recursive: true, force: true });
      await rm(payloadStagingRoot(), { recursive: true, force: true });
      await rm(path.join(hostStateRoot(), 'payload.old'), { recursive: true, force: true });
    }
    if (resetScopes.logs) {
      await this.logWriter?.flush();
      this.logWriter = undefined;
      await rm(app.getPath('logs'), { recursive: true, force: true });
    }
    this.ports = undefined;
    this.secrets = undefined;
    if (!resetScopes.logs) {
      this.supervisor.appendManagerLog(
        `Factory reset complete; removed ${[
          resetScopes.db ? 'db' : undefined,
          resetScopes.runtimeData ? 'runtime data' : undefined,
          resetScopes.appRuntime ? 'app runtime' : undefined,
          resetScopes.logs ? 'logs' : undefined,
        ]
          .filter(Boolean)
          .join(', ')}.`,
      );
    }
    return this.emitStatus();
  }

  private async ensureInitialized(): Promise<void> {
    if (this.ports) return;
    this.logWriter = this.logWriter || new DailyLogWriter(app.getPath('logs'));
    await mkdir(desktopRunRoot(), { recursive: true });
    await mkdir(desktopWorkspaceRoot(), { recursive: true });
    await mkdir(postgresDataDir(), { recursive: true });
    await this.acquireDesktopOwnership();
    await this.reapStaleDesktopProcesses();
    this.secrets = await this.loadDesktopSecrets();
    this.supervisor.appendManagerLog(`Desktop logs: ${app.getPath('logs')}`);
    this.ports = {
      app: await findFreePort(5070),
      backend: await findFreePort(18020),
      postgres: await findFreePort(15452),
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

  onPayloadProgress(listener: (progress: PayloadProgress) => void): () => void {
    this.payloadProgressListeners.add(listener);
    return () => this.payloadProgressListeners.delete(listener);
  }

  onPayloadInstalled(listener: (info: PayloadInfo) => void): () => void {
    this.payloadInstalledListeners.add(listener);
    return () => this.payloadInstalledListeners.delete(listener);
  }

  onPayloadFailed(listener: (failure: PayloadFailure) => void): () => void {
    this.payloadFailedListeners.add(listener);
    return () => this.payloadFailedListeners.delete(listener);
  }

  private emitPayloadProgress(phase: PayloadPhase, message: string, fractionComplete?: number): void {
    const progress: PayloadProgress = { phase, message, fractionComplete };
    for (const listener of this.payloadProgressListeners) listener(progress);
    this.supervisor.appendManagerLog(`[payload:${phase}] ${message}`);
  }

  async getStatus(): Promise<DesktopStatus> {
    const appUrl = this.ports && this.localServer.running ? this.appUrl() : undefined;
    return {
      appUrl,
      appSupportPath: hostStateRoot(),
      payload: await payload.readPayloadInfo(),
      services: this.supervisor.status(),
    };
  }

  logs(): LogEntry[] {
    return this.supervisor.allLogs();
  }

  async getPayload(): Promise<PayloadInfo> {
    return payload.readPayloadInfo();
  }

  async checkForUpdate(channel?: ReleaseChannel): Promise<PayloadUpdate | null> {
    const installed = await payload.readPayloadInfo();
    const target = channel ?? installed.channel ?? 'stable';
    return payload.checkForUpdate(target);
  }

  // Downloads, verifies, and installs a payload update, then restarts services.
  async applyUpdate(update: PayloadUpdate): Promise<void> {
    const scratch = payload.downloadScratchPath();
    try {
      this.emitPayloadProgress('download', `Downloading ${update.version}…`);
      await payload.downloadTarball(update.url, scratch);
      this.emitPayloadProgress('verify', 'Verifying download…');
      await payload.verifySha256(scratch, update.sha256);
      await this.applyPayloadFromTarball(scratch);
    } finally {
      await rm(scratch, { force: true });
    }
  }

  // First-launch bootstrap for a fresh shell with no payload: download and
  // install the latest published release, preferring stable and falling back
  // to beta. Progress is emitted so the renderer shows the install overlay.
  // Returns true once a payload is installed.
  async autoInstallLatest(): Promise<boolean> {
    if (!app.isPackaged || payload.isInstalled()) return false;
    const channels: ReleaseChannel[] = ['stable', 'beta'];
    for (const channel of channels) {
      let update: PayloadUpdate | null = null;
      try {
        update = await payload.checkForUpdate(channel);
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        this.supervisor.appendManagerLog(`Auto-install: ${channel} channel check failed: ${reason}`);
        continue;
      }
      if (!update) continue;
      this.supervisor.appendManagerLog(
        `Auto-install: installing latest ${channel} release (${update.version}).`,
      );
      await this.applyUpdate(update);
      return true;
    }
    this.supervisor.appendManagerLog('Auto-install: no published release found on stable or beta.');
    return false;
  }

  // Installs a payload tarball already on disk (the "Install from file" path).
  async installPayloadFromFile(tarPath: string): Promise<void> {
    await this.applyPayloadFromTarball(tarPath);
  }

  private async applyPayloadFromTarball(tarPath: string): Promise<void> {
    let phase: PayloadPhase = 'extract';
    try {
      if (this.supervisor.isRunning('backend')) {
        this.emitPayloadProgress('swap', 'Stopping backend…');
        await this.supervisor.stopAndWait('backend');
      }
      this.emitPayloadProgress('extract', 'Installing app files…');
      await payload.installFromTarball(tarPath);

      phase = 'restart';
      this.emitPayloadProgress('restart', 'Starting Sentinel…');
      await this.startServices();
      await this.restartLocalServerIfRunning();

      const info = await payload.readPayloadInfo();
      for (const listener of this.payloadInstalledListeners) listener(info);
      this.emitPayloadProgress('done', `Installed ${info.version ?? 'app'}.`);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      const failure: PayloadFailure = { phase, reason };
      for (const listener of this.payloadFailedListeners) listener(failure);
      throw error;
    }
  }

  async revealAppSupport(): Promise<void> {
    await shell.openPath(hostStateRoot());
  }

  async openLogFolder(): Promise<void> {
    await shell.openPath(app.getPath('logs'));
  }

  async shutdown(): Promise<void> {
    await this.localServer.stop();
    await this.supervisor.stopAll();
    await this.stopExistingPostgres();
    await this.clearPidFile('backend');
    await this.clearPidFile('postgres');
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
      await execFileText(
        initdbBin,
        ['-D', dataDir, '-U', 'sentinel', '--encoding=UTF8', '-L', postgresSharePath()],
        { env },
      );
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
      // `process.kill(pid, 0)` only proves the PID is allocated to *some*
      // process — macOS recycles PIDs aggressively, so a long-dead postgres
      // may share a PID with an unrelated process. Also verify the process's
      // command line names postgres + our data dir so we trust the reuse.
      if (!(await this.isLivePostgresFor(pid))) {
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

  private async isLivePostgresFor(pid: number): Promise<boolean> {
    try {
      process.kill(pid, 0);
    } catch {
      return false;
    }
    const cmd = await execFileText('/bin/ps', ['-p', String(pid), '-o', 'command=']).catch(() => '');
    if (!cmd) return false;
    const looksLikePostgres = cmd.includes('/postgres') || cmd.includes(' postgres ') || cmd.startsWith('postgres');
    const matchesDataDir = cmd.includes(`-D ${postgresDataDir()}`) || cmd.includes(`-D${postgresDataDir()}`);
    return looksLikePostgres && matchesDataDir;
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
    if (app.isPackaged) {
      const python = shellPythonBinary();
      if (!existsSync(python)) {
        throw new Error(`Bundled Python missing at ${python}. Rebuild the desktop package.`);
      }
      if (!payload.isInstalled()) {
        throw new Error('No app payload installed. Load one from file or apply an update.');
      }
      return {
        command: python,
        args: ['-m', 'app.desktop_entry', '--host', '127.0.0.1', '--port', String(this.ports!.backend)],
        cwd: backendPath(),
      };
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
    const dataDir = postgresDataDir();
    const stale = entries.filter((entry) => {
      if (entry.pid === process.pid) return false;
      const command = entry.command;
      // Any postgres bound to our data dir that isn't actively supervised by
      // *us* right now is by definition stale — kill it so the new startup
      // can rebind cleanly. The data-dir match is enough: only postgres
      // processes will be passed `-D <dataDir>`.
      if (command.includes(`-D ${dataDir}`) || command.includes(`-D${dataDir}`)) {
        return true;
      }
      // A backend launched from our payload (cwd under hostStateRoot).
      if (command.includes('app.desktop_entry') && command.includes(hostStateRoot())) {
        return true;
      }
      return false;
    });
    for (const entry of stale) {
      this.supervisor.appendManagerLog(`Stopping stale desktop process pid=${entry.pid}: ${entry.command.slice(0, 180)}`);
      await terminateProcess(entry.pid);
    }
    // If we killed any postgres, the lockfile is also stale.
    if (stale.some((e) => e.command.includes(`-D ${dataDir}`) || e.command.includes(`-D${dataDir}`))) {
      await rm(path.join(dataDir, 'postmaster.pid'), { force: true });
    }
    await this.reapPidFile('backend');
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

  private jwtSecretPath(): string {
    return path.join(hostStateRoot(), 'config', 'secrets.json');
  }

  private dataEncryptionKeyPath(): string {
    return path.join(hostStateRoot(), 'config', 'data-encryption-key.bin');
  }

  private async loadDesktopSecrets(): Promise<DesktopSecrets> {
    return {
      jwtSecretKey: await this.loadOrCreateJwtSecret(),
      dataEncryptionKey: await this.loadOrCreateDataEncryptionKey(),
    };
  }

  private async loadOrCreateJwtSecret(): Promise<string> {
    const existing = await readJsonFile<DesktopSecretsFile>(this.jwtSecretPath());
    if (existing?.jwtSecretKey && existing.jwtSecretKey.trim()) {
      return existing.jwtSecretKey.trim();
    }
    return this.rotateJwtSecret();
  }

  private async rotateJwtSecret(): Promise<string> {
    const filePath = this.jwtSecretPath();
    const jwtSecretKey = randomBytes(32).toString('hex');
    await mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
    await writeFile(filePath, `${JSON.stringify({ jwtSecretKey }, null, 2)}\n`, { mode: 0o600 });
    return jwtSecretKey;
  }

  private async loadOrCreateDataEncryptionKey(): Promise<string> {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error('OS keychain is unavailable; cannot load the data encryption key.');
    }
    const filePath = this.dataEncryptionKeyPath();
    if (existsSync(filePath)) {
      return safeStorage.decryptString(await readFile(filePath));
    }
    const key = randomBytes(32).toString('hex');
    await mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
    await writeFile(filePath, safeStorage.encryptString(key), { mode: 0o600 });
    return key;
  }

  private async emitStatus(): Promise<DesktopStatus> {
    const status = await this.getStatus();
    for (const listener of this.statusListeners) listener(status);
    return status;
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
