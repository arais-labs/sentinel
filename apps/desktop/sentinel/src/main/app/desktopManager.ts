import { publishWorkspaceNotification } from '../workspace/workspaceNotifications.js';
import { NotificationCenter } from './notifications.js';
import { app, safeStorage, shell } from 'electron';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type {
  DesktopStatus,
  LogEntry,
  PayloadFailure,
  PayloadInfo,
  PayloadPhase,
  PayloadProgress,
  PayloadUpdate,
  ReleaseChannel,
  ShellUpdate,
} from '../../shared/ipc.js';
import * as payload from './payloadManager.js';
import { assertShellCompatible } from './version.js';
import {
  hostStateRoot,
  backendPath,
  resourceRoot,
} from '../paths.js';
import { execFileText } from './shell.js';
import { ProcessSupervisor } from './supervisor.js';
import { APP_URL, LocalTransport } from '../transport/localTransport.js';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import {
  type DesktopSecrets,
  buildBackendEnv,
  desktopRunRoot,
  shellPythonBinary,
} from './desktopConfig.js';
import { DailyLogWriter } from './logWriter.js';
import { WorkspaceLifecycle } from '../workspace/workspaceLifecycle.js';
import { WorkspaceGraphics } from '../workspace/workspaceGraphics.js';
import { WorkspaceRuntime } from '../workspace/workspaceRuntime.js';
import { WorkspaceKernel, type KernelManifest } from '../workspace/workspaceKernel.js';
import { openWorkspaceRuntimeBridge } from '../transport/workspaceRuntimeBridge.js';

interface DesktopOwnerFile {
  pid?: number;
  resourceRoot?: string;
  startedAt?: string;
}

interface DesktopProcessEntry {
  pid: number;
  command: string;
}

type DesktopPidService = 'backend';

export class DesktopManager {
  readonly notifications = new NotificationCenter(path.join(app.getPath('userData'), 'notifications.json'));
  private readonly supervisor = new ProcessSupervisor();

  log(line: string): void { this.supervisor.appendManagerLog(line); }
  transport?: LocalTransport;
  private socketDirectory?: string;
  private logWriter?: DailyLogWriter;
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

  private servicesReady = false;
  private serviceQueue: Promise<unknown> = Promise.resolve();
  private operation?: 'starting' | 'stopping';

  private queueServices(action: () => Promise<DesktopStatus>): Promise<DesktopStatus> {
    const next = this.serviceQueue.then(action, action);
    this.serviceQueue = next;
    return next;
  }
  private startupError?: string;
  private workspaceLifecycle?: WorkspaceLifecycle;
  private workspaceGraphics?: WorkspaceGraphics;
  private workspaceRuntime?: WorkspaceRuntime;
  private workspaceRuntimeBridge?: Awaited<ReturnType<typeof openWorkspaceRuntimeBridge>>;

  async prepareUI(): Promise<string> {
    await this.ensureInitialized();
    return this.appUrl();
  }

  private preparation?: Promise<DesktopStatus>;
  private preparing = true;
  private payloadProgress?: PayloadProgress;
  private shellUpdate: ShellUpdate | null = null;

  initialize(): Promise<DesktopStatus> {
    if (this.preparation) return this.preparation;
    this.preparation = this.prepareApp().finally(() => { this.preparation = undefined; });
    return this.preparation;
  }

  private async prepareApp(): Promise<DesktopStatus> {
    this.preparing = true;
    this.startupError = undefined;
    this.payloadProgress = undefined;
    try {
      await this.emitStatus();
      const status = await this.startServices();
      if (app.isPackaged && !status.payload.installed) {
        if (!await this.autoInstallLatest()) throw new Error('Could not find a Sentinel release. Check your connection and retry.');
      }
    } catch (error) {
      this.startupError = error instanceof Error ? error.message : String(error);
      this.supervisor.appendManagerLog(this.startupError);
    } finally {
      this.preparing = false;
    }
    return this.emitStatus();
  }

  // Packaged builds with no installed payload have nothing to run yet — the
  // user must load one from file (or download an update). Dev builds always run
  // against the repo source.
  private hasRunnablePayload(): boolean {
    return !app.isPackaged || payload.isInstalled();
  }

  startServices(): Promise<DesktopStatus> {
    return this.queueServices(() => this.startServicesNow());
  }

  private async startServicesNow(): Promise<DesktopStatus> {
    this.operation = 'starting';
    this.startupError = undefined;
    try {
      await this.prepareUI();
      await this.emitStatus();
      try { await this.openWorkspaceServices(); }
      catch (error) { this.supervisor.appendManagerLog(`Workspace services unavailable: ${String(error)}`); }
      if (!this.hasRunnablePayload()) {
        this.operation = undefined;
        return this.emitStatus();
      }
      await this.startBackend();
      await this.waitForBackend();
      this.servicesReady = true;
      this.supervisor.setVirtualStatus({ name: 'frontend', state: 'running' });
    } catch (error) {
      this.servicesReady = false;
      this.startupError = error instanceof Error ? error.message : String(error);
      this.supervisor.appendManagerLog(this.startupError);
    }
    this.operation = undefined;
    return this.emitStatus();
  }

  stopServices(): Promise<DesktopStatus> {
    return this.queueServices(() => this.stopServicesNow());
  }

  private async stopServicesNow(): Promise<DesktopStatus> {
    this.operation = 'stopping';
    this.servicesReady = false;
    await this.emitStatus();
    await this.supervisor.stopAll();
    await this.workspaceRuntimeBridge?.close();
    this.workspaceRuntimeBridge = undefined;
    await this.workspaceLifecycle?.close();
    this.workspaceLifecycle = undefined;
    this.workspaceGraphics = undefined;
    this.workspaceRuntime = undefined;
    await this.clearPidFile('backend');
    this.operation = undefined;
    this.supervisor.appendManagerLog('Stopped local Sentinel services');
    return this.emitStatus();
  }

  private async ensureInitialized(): Promise<void> {
    if (this.transport) return;
    this.logWriter = this.logWriter || new DailyLogWriter(app.getPath('logs'));
    await mkdir(desktopRunRoot(), { recursive: true });
    await this.acquireDesktopOwnership();
    await this.reapStaleDesktopProcesses();
    this.secrets = await this.loadDesktopSecrets();
    this.socketDirectory = await mkdtemp(path.join(tmpdir(), 'sentinel-'));
    this.transport = new LocalTransport(path.join(this.socketDirectory, 'backend.sock'), this.secrets.desktopToken);
    this.supervisor.appendManagerLog(`Desktop logs: ${app.getPath('logs')}`);
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
    this.payloadProgress = progress;
    void this.emitStatus();
    for (const listener of this.payloadProgressListeners) listener(progress);
    this.supervisor.appendManagerLog(`[payload:${phase}] ${message}`);
  }

  async getStatus(): Promise<DesktopStatus> {
    const appUrl = this.transport ? this.appUrl() : undefined;
    return {
      appUrl,
      development: !app.isPackaged,
      shellVersion: app.getVersion(),
      shellUpdate: this.shellUpdate,
      operation: this.operation,
      preparing: this.preparing,
      payloadProgress: this.payloadProgress,
      ready: this.servicesReady && this.supervisor.isRunning('backend'),
      error: this.startupError,
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
    const update = await payload.checkForUpdate(target, app.getVersion());
    if (!channel || channel === installed.channel) {
      this.shellUpdate = update?.shellUpdate ?? null;
      void this.emitStatus();
    }
    return update;
  }

  // One launch-time check on the installed channel; a payload that needs a newer
  // shell is announced once per required version.
  async notifyShellUpdate(): Promise<void> {
    let update: PayloadUpdate | null = null;
    try { update = await this.checkForUpdate(); }
    catch (error) {
      this.supervisor.appendManagerLog(`Update check failed: ${error instanceof Error ? error.message : String(error)}`);
      return;
    }
    const required = update?.shellUpdate;
    if (!required) return;
    const marker = path.join(app.getPath('userData'), 'shell-update.json');
    let notified: string | null = null;
    try { notified = (JSON.parse(await readFile(marker, 'utf8')) as { notified?: string }).notified ?? null; } catch { /* first time */ }
    if (notified === required.version) return;
    this.notifications.publish({
      source: 'updates', key: `shell-update:${required.version}`, severity: 'warning',
      title: `Sentinel ${required.version} needs a new app version`,
      message: `This update requires app version ${required.minShellVersion} or newer (you have ${app.getVersion()}). Download the installer from ${required.url} to update.`,
    });
    try { await writeFile(marker, JSON.stringify({ notified: required.version }), { mode: 0o600 }); }
    catch { /* the notice repeats next launch at worst */ }
  }

  // Downloads, verifies, and installs a payload update, then restarts services.
  async applyUpdate(update: PayloadUpdate): Promise<void> {
    assertShellCompatible(update);
    const scratch = payload.downloadScratchPath();
    try {
      this.emitPayloadProgress('download', `Downloading ${update.version}…`);
      await payload.downloadTarball(update.url, scratch, fraction => {
        this.payloadProgress = { phase: 'download', message: 'Downloading your app…', fractionComplete: fraction };
        void this.emitStatus();
      });
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
        update = await payload.checkForUpdate(channel, app.getVersion());
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        this.supervisor.appendManagerLog(`Auto-install: ${channel} channel check failed: ${reason}`);
        continue;
      }
      if (!update) continue;
      if (update.shellUpdate) {
        this.supervisor.appendManagerLog(
          `Auto-install: ${channel} ${update.version} needs app ${update.shellUpdate.minShellVersion}, skipping.`,
        );
        continue;
      }
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
      const status = await this.startServices();
      if (!status.ready) throw new Error(status.error || 'Services did not start after installation.');


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
    await this.serviceQueue.catch(() => {});
    await this.supervisor.stopAll();
    await this.workspaceRuntimeBridge?.close();
    this.workspaceRuntimeBridge = undefined;
    await this.workspaceLifecycle?.close();
    this.workspaceLifecycle = undefined;
    this.workspaceGraphics = undefined;
    this.workspaceRuntime = undefined;
    if (this.socketDirectory) await rm(this.socketDirectory, { recursive: true, force: true });
    await this.clearPidFile('backend');
    await this.releaseDesktopOwnership();
    await this.logWriter?.flush();
  }

  private async openWorkspaceServices(): Promise<void> {
    // Development uses the same privately owned helper as the packaged app.
    if (process.platform !== 'darwin') return;
    if (!this.workspaceRuntime) {
      const resources = app.isPackaged ? path.join(resourceRoot(), 'workspace-runtime')
        : path.join(resourceRoot(), 'apps/desktop/sentinel/build/macos-arm64/runtime/workspace-runtime');
      const manifest = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8')) as KernelManifest & {
        protocol: number; initImage: string;
      };
      if (manifest.protocol !== 1 || !manifest.initImage) {
        throw new Error('The bundled workspace runtime is incomplete. Reinstall Sentinel.');
      }
      const kernel = new WorkspaceKernel(resources, manifest,
        message => this.supervisor.appendManagerLog(message));
      // Verify the bundled kernel without starting a VM or downloading anything.
      void kernel.ensure().catch(error => this.supervisor.appendManagerLog(`Workspace kernel unavailable: ${String(error)}`));
      this.workspaceRuntime = new WorkspaceRuntime({
        prepare: () => kernel.ensure(),
        cancelPreparation: () => kernel.cancel(),
        command: path.join(resources, 'sentinel-workspace-runtime'),
        args: [path.join(hostStateRoot(), 'workspace-runtime'), kernel.file, manifest.initImage],
        onProgress: message => this.supervisor.appendManagerLog(message),
        onFailure: message => this.supervisor.appendManagerLog(message),
        log: message => this.supervisor.appendManagerLog(message),
      });
    }
    if (!this.workspaceLifecycle) {
      this.workspaceGraphics = new WorkspaceGraphics(this.workspaceRuntime);
      this.workspaceLifecycle = new WorkspaceLifecycle(this.workspaceRuntime, path.join(hostStateRoot(), 'workspace-runtime'), this.workspaceGraphics);
      this.workspaceLifecycle.events.on('changed', (id, entry) => {
        try { publishWorkspaceNotification(this.notifications, id, entry); }
        catch (error) { this.supervisor.appendManagerLog(`Notification could not be saved: ${error}`); }
      });
      this.workspaceLifecycle.events.on('removed', id => {
        const item = this.notifications.list().find(item => item.source === 'workspaces' && item.key === `preparation:${id}`);
        if (item) {
          try { this.notifications.update(item.id, 'dismiss'); }
          catch (error) { this.supervisor.appendManagerLog(`Notification could not be saved: ${error}`); }
        }
      });
      await this.workspaceLifecycle.load();
    }
    this.workspaceRuntimeBridge ??= await openWorkspaceRuntimeBridge(
      this.workspaceRuntime, path.join(this.socketDirectory!, 'workspaces.sock'), this.secrets!.desktopToken, this.workspaceLifecycle, this.notifications,
    );
  }

  private async startBackend(): Promise<void> {
    if (this.supervisor.isRunning('backend')) return;
    const backend = await this.resolveBackendLaunch();
    await this.supervisor.start({
      name: 'backend',
      command: backend.command,
      args: backend.args,
      cwd: backend.cwd,
      env: {
        ...buildBackendEnv(this.secrets!),
        ...(this.workspaceRuntimeBridge ? { SENTINEL_WORKSPACE_RUNTIME_SOCKET: this.workspaceRuntimeBridge.socketPath } : {}),
      },
    });
    const pid = this.supervisor.pid('backend');
    if (pid) await this.writePidFile('backend', pid);
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
        args: ['-m', 'app.desktop_entry', '--uds', this.transport!.socketPath],
        cwd: backendPath(),
      };
    }
    const python = path.join(backendPath(), '.venv/bin/python');
    if (!existsSync(python)) {
      throw new Error('Missing development environment. Run make setup from the repository root.');
    }
    return {
      command: python,
      args: ['-m', 'uvicorn', 'app.main:app', '--uds', this.transport!.socketPath, '--reload', '--reload-dir', 'app', '--timeout-graceful-shutdown', '5'],
      cwd: backendPath(),
    };
  }

  private async waitForBackend(): Promise<void> {
    const url = `${APP_URL}health`;
    for (let i = 0; i < 120; i += 1) {
      const backend = this.supervisor.status().find((service) => service.name === 'backend');
      if (backend?.state === 'failed' || backend?.state === 'stopped') {
        throw new Error(`Backend exited before becoming ready.\n${this.backendStartupDetails()}`);
      }
      try {
        const response = await this.transport!.request(new Request(url));
        await response.body?.cancel();
        if (response.ok) return;
      } catch {
        // Backend is still starting.
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`Backend did not become ready.\n${this.backendStartupDetails()}`);
  }

  private appUrl(): string {
    return app.isPackaged ? APP_URL : process.env.ELECTRON_RENDERER_URL!;
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

  private async writePidFile(service: DesktopPidService, pid: number): Promise<void> {
    await mkdir(desktopRunRoot(), { recursive: true });
    await writeFile(
      this.pidPath(service),
      `${JSON.stringify({ pid, resourceRoot: resourceRoot(), startedAt: new Date().toISOString() }, null, 2)}\n`,
      { mode: 0o600 },
    );
  }

  private async clearPidFile(service: DesktopPidService): Promise<void> {
    await rm(this.pidPath(service), { force: true });
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

  private dataEncryptionKeyPath(): string {
    return path.join(hostStateRoot(), 'config', 'data-encryption-key.bin');
  }

  private async loadDesktopSecrets(): Promise<DesktopSecrets> {
    return {
      desktopToken: randomBytes(32).toString('hex'),
      dataEncryptionKey: await this.loadOrCreateDataEncryptionKey(),
    };
  }

  private async loadOrCreateDataEncryptionKey(): Promise<string> {
    return this.loadOrCreateKeychainSecret(this.dataEncryptionKeyPath());
  }

  // Provider credentials stay encrypted with an OS-keychain-protected key.
  private async loadOrCreateKeychainSecret(filePath: string): Promise<string> {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error('OS keychain is unavailable; cannot load encrypted secrets.');
    }
    if (existsSync(filePath)) {
      return safeStorage.decryptString(await readFile(filePath));
    }
    const value = randomBytes(32).toString('hex');
    await this.writeKeychainSecret(filePath, value);
    return value;
  }

  private async writeKeychainSecret(filePath: string, value: string): Promise<void> {
    await mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
    await writeFile(filePath, safeStorage.encryptString(value), { mode: 0o600 });
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
