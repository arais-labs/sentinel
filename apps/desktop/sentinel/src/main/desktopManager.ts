import { app, shell } from 'electron';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type {
  BootstrapPhase,
  BootstrapProgress,
  DesktopStatus,
  FactoryResetScopes,
  LogEntry,
  ReleaseChannel,
  RuntimeVersion,
  UpdateAvailable,
  UpdateFailure,
  UpdatePhase,
  UpdateProgress,
} from '../shared/ipc.js';
import * as updateManager from './updateManager.js';
import { appSupportRoot, backendPath, frontendDistPath, resourceRoot } from './paths.js';
import { findFreePort } from './ports.js';
import { commandExists, execFileText } from './shell.js';
import { ProcessSupervisor } from './supervisor.js';
import { LocalServer } from './localServer.js';
import {
  type DesktopSecrets,
  type DesktopPorts,
  backendSourceDir,
  buildBackendEnv,
  bundledGitBinary,
  bundledNodeModulesArchive,
  bundledPythonBinary,
  bundledSourceBareArchive,
  bundledWheelsDir,
  userDataBareSourceDir,
  desktopRunRoot,
  desktopWorkspaceRoot,
  frontendSourceDir,
  postgresDataDir,
  postgresBinaryPath,
  postgresSharePath,
  qemuBinaryPath,
  qemuRunRoot,
  runtimeChannelMarkerPath,
  runtimeCommandPath,
  runtimeCommitMarkerPath,
  runtimeImagePath,
  runtimeKeyPath,
  runtimeOutputDir,
  runtimeSeedRoot,
  seedNodeDir,
  seedPythonDir,
  nodeHome,
  pythonHome,
  sourceRoot,
  venvPython,
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
  private bootstrapListeners = new Set<(progress: BootstrapProgress) => void>();
  private updateAvailableListeners = new Set<(info: UpdateAvailable) => void>();
  private updateProgressListeners = new Set<(progress: UpdateProgress) => void>();
  private updateAppliedListeners = new Set<(version: RuntimeVersion) => void>();
  private updateFailedListeners = new Set<(failure: UpdateFailure) => void>();

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

  // sirv snapshots dist/ at start; bounce it after frontend rebuilds.
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
    // No ensureInitialized: appRuntime reset must work when bootstrap is broken.
    await this.stopServices();
    await this.reapOrphanedQemuBuild();
    await this.reapOrphanedQemuVms();
    await this.releaseDesktopOwnership();
    if (resetScopes.db) {
      await rm(path.join(appSupportRoot(), 'postgres'), { recursive: true, force: true });
    }
    if (resetScopes.runtimeData) {
      await rm(path.join(appSupportRoot(), 'qemu'), { recursive: true, force: true });
      await rm(desktopWorkspaceRoot(), { recursive: true, force: true });
      await rm(desktopRunRoot(), { recursive: true, force: true });
    }
    if (resetScopes.appRuntime) {
      // Drops the bootstrap-derived state. Next launch re-bootstraps from the
      // bundled runtime-seed (fresh python + node, fresh source clone, fresh
      // venv, fresh frontend build).
      await rm(sourceRoot(), { recursive: true, force: true });
      await rm(userDataBareSourceDir(), { recursive: true, force: true });
      await rm(pythonHome(), { recursive: true, force: true });
      await rm(nodeHome(), { recursive: true, force: true });
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
    await mkdir(runtimeOutputDir(), { recursive: true });
    await mkdir(qemuRunRoot(), { recursive: true });
    await mkdir(desktopRunRoot(), { recursive: true });
    await mkdir(desktopWorkspaceRoot(), { recursive: true });
    await mkdir(postgresDataDir(), { recursive: true });
    await this.acquireDesktopOwnership();
    await this.reapStaleDesktopProcesses();
    await this.reapOrphanedQemuBuild();
    await this.reapOrphanedQemuVms();
    if (app.isPackaged) {
      await this.bootstrapRuntime();
    }
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

  onBootstrapProgress(listener: (progress: BootstrapProgress) => void): () => void {
    this.bootstrapListeners.add(listener);
    return () => this.bootstrapListeners.delete(listener);
  }

  private emitBootstrap(phase: BootstrapPhase, message: string, fractionComplete?: number): void {
    const progress: BootstrapProgress = { phase, message, fractionComplete };
    for (const listener of this.bootstrapListeners) listener(progress);
    this.supervisor.appendManagerLog(`[bootstrap:${phase}] ${message}`);
  }

  private async bootstrapRuntime(): Promise<void> {
    // Gate on a sentinel file written at the very end so a partial bootstrap
    // (e.g. crash after extracting source but before creating the venv)
    // re-runs from scratch on the next launch.
    const sentinel = path.join(sourceRoot(), '.bootstrap-complete');
    const dmgChannelFile = path.join(runtimeSeedRoot(), 'default-channel');
    let dmgChannel: ReleaseChannel = 'stable';
    try {
      const raw = (await readFile(dmgChannelFile, 'utf8')).trim();
      if (raw === 'stable' || raw === 'beta') {
        dmgChannel = raw;
      } else if (raw) {
        throw new Error(`Unknown channel "${raw}" in ${dmgChannelFile}. Expected "stable" or "beta".`);
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes('Unknown channel')) {
        throw error;
      }
      // Missing stamp: fall back to stable.
    }

    // Marker is authoritative; DMG default-channel only seeds first launch.
    let userChannel: ReleaseChannel | null = null;
    try {
      const raw = (await readFile(runtimeChannelMarkerPath(), 'utf8')).trim();
      if (raw === 'stable' || raw === 'beta') userChannel = raw;
    } catch {
      // No marker yet.
    }
    const effectiveChannel: ReleaseChannel = userChannel ?? dmgChannel;
    const branch = updateManager.channelToBranch(effectiveChannel);

    if (existsSync(sentinel)) {
      // Re-bootstrap only on physical breakage. Channel drift between DMG
      // default and marker is intentional and must not trigger a wipe.
      const userPython = path.join(pythonHome(), 'bin/python3');
      const venvPy = venvPython();
      if (existsSync(userPython) && existsSync(venvPy)) {
        return;
      }
      const reasons = [
        !existsSync(userPython) ? `missing python at ${userPython}` : null,
        !existsSync(venvPy) ? `missing venv python at ${venvPy}` : null,
      ].filter(Boolean).join('; ');
      this.supervisor.appendManagerLog(`Re-bootstrapping: ${reasons}`);
    }
    const seedRoot = runtimeSeedRoot();
    if (!existsSync(seedRoot)) {
      throw new Error(`Missing bundled runtime seed at ${seedRoot}. Rebuild the desktop package.`);
    }
    // Put bundled node and python on PATH so subprocesses with `#!/usr/bin/env
    // node` (npm, npx) or `#!/usr/bin/env python3` resolve to our bundled
    // runtime. python/node live in userData (copied below); git stays in the
    // DMG (read-only).
    const bundledBinDirs = [
      path.join(nodeHome(), 'bin'),
      path.join(pythonHome(), 'bin'),
      path.join(runtimeSeedRoot(), 'git/bin'),
    ];
    const env = {
      ...process.env,
      LANG: 'C',
      LC_ALL: 'C',
      PATH: [...bundledBinDirs, process.env.PATH || ''].filter(Boolean).join(':'),
    };

    this.emitBootstrap('extract-python', 'Installing Python runtime...', 0.05);
    await mkdir(appSupportRoot(), { recursive: true });
    await rm(pythonHome(), { recursive: true, force: true });
    await rm(nodeHome(), { recursive: true, force: true });
    // ditto preserves symlinks + perms; using it (instead of fs.cp) avoids
    // breaking python's relative bin/python3 -> python3.12 symlink.
    await execFileText('/usr/bin/ditto', [seedPythonDir(), pythonHome()], { env });

    this.emitBootstrap('extract-node', 'Installing Node runtime...', 0.08);
    await execFileText('/usr/bin/ditto', [seedNodeDir(), nodeHome()], { env });

    this.emitBootstrap('extract-source', 'Unpacking Sentinel source...', 0.1);
    await rm(userDataBareSourceDir(), { recursive: true, force: true });
    await rm(sourceRoot(), { recursive: true, force: true });
    // The bare clone ships as a tar (electron-builder strips empty dirs from
    // extraResources; tar preserves them so git can recognize the repo).
    await execFileText(
      '/usr/bin/tar',
      ['-xf', bundledSourceBareArchive(), '-C', appSupportRoot()],
      { env },
    );
    await execFileText(
      bundledGitBinary(),
      [
        'clone',
        '--local',
        '--no-hardlinks',
        '--branch', branch,
        userDataBareSourceDir(),
        sourceRoot(),
      ],
      { env },
    );
    // Redirect origin to the canonical upstream (e.g. github) so future
    // `git fetch` actually pulls new commits instead of re-reading the
    // frozen bundled bare clone.
    try {
      const upstreamUrl = (await readFile(path.join(runtimeSeedRoot(), 'upstream-url'), 'utf8')).trim();
      if (upstreamUrl) {
        await execFileText(
          bundledGitBinary(),
          ['remote', 'set-url', 'origin', upstreamUrl],
          { cwd: sourceRoot(), env },
        );
      }
    } catch {
      // No stamp (older DMG); leave origin pointing at the bundled bare.
    }

    this.emitBootstrap('extract-node-modules', 'Restoring frontend packages...', 0.25);
    const nodeModulesArchive = bundledNodeModulesArchive();
    if (existsSync(nodeModulesArchive)) {
      await mkdir(frontendSourceDir(), { recursive: true });
      await execFileText(
        '/usr/bin/tar',
        ['-xzf', nodeModulesArchive, '-C', frontendSourceDir()],
        { env },
      );
    }

    this.emitBootstrap('uv-sync', 'Setting up Python environment...', 0.5);
    await execFileText(
      bundledPythonBinary(),
      ['-m', 'venv', '--copies', path.join(backendSourceDir(), '.venv')],
      { env },
    );
    const venvPip = path.join(backendSourceDir(), '.venv/bin/pip');
    await execFileText(
      venvPip,
      [
        'install',
        '--no-index',
        '--find-links', bundledWheelsDir(),
        '--no-cache-dir',
        '-r', path.join(bundledWheelsDir(), 'requirements.txt'),
      ],
      { env },
    );
    // No `pip install -e .` for the project itself: the supervisor spawns
    // `python -m app.desktop_entry` with cwd=<backend>, which puts cwd at the
    // head of sys.path. That's enough to resolve `app.*` imports without
    // needing setuptools build infrastructure on the client.

    this.emitBootstrap('npm-build', 'Building frontend...', 0.85);
    const npmBin = path.join(runtimeSeedRoot(), 'node/bin/npm');
    await execFileText(
      npmBin,
      ['run', 'build'],
      { cwd: frontendSourceDir(), env },
    );

    const head = (await execFileText(
      bundledGitBinary(),
      ['rev-parse', 'HEAD'],
      { cwd: sourceRoot(), env },
    )).trim();
    await writeFile(runtimeCommitMarkerPath(), `${head}\n`);
    await writeFile(runtimeChannelMarkerPath(), `${effectiveChannel}\n`);
    await writeFile(sentinel, `${new Date().toISOString()}\n`);

    this.emitBootstrap('done', 'Sentinel is ready.', 1);
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

  async getVersion(): Promise<RuntimeVersion> {
    // Read the marker files the supervisor wrote during bootstrap / update.
    // These represent the last commit the supervisor successfully installed
    // + health-checked, and they don't depend on the backend being up.
    let commit: string | null = null;
    let channel: RuntimeVersion['channel'] = 'dev';
    try {
      const raw = (await readFile(runtimeCommitMarkerPath(), 'utf8')).trim();
      if (raw) commit = raw;
    } catch {
      // Markers absent in dev mode; that's fine.
    }
    try {
      const raw = (await readFile(runtimeChannelMarkerPath(), 'utf8')).trim();
      if (raw === 'stable' || raw === 'beta') channel = raw;
    } catch {
      // Same as above.
    }
    return { commit, channel };
  }

  onUpdateAvailable(listener: (info: UpdateAvailable) => void): () => void {
    this.updateAvailableListeners.add(listener);
    return () => this.updateAvailableListeners.delete(listener);
  }

  onUpdateProgress(listener: (progress: UpdateProgress) => void): () => void {
    this.updateProgressListeners.add(listener);
    return () => this.updateProgressListeners.delete(listener);
  }

  onUpdateApplied(listener: (version: RuntimeVersion) => void): () => void {
    this.updateAppliedListeners.add(listener);
    return () => this.updateAppliedListeners.delete(listener);
  }

  onUpdateFailed(listener: (failure: UpdateFailure) => void): () => void {
    this.updateFailedListeners.add(listener);
    return () => this.updateFailedListeners.delete(listener);
  }

  private emitUpdateProgress(phase: UpdatePhase, message: string): void {
    const progress: UpdateProgress = { phase, message };
    for (const listener of this.updateProgressListeners) listener(progress);
    this.supervisor.appendManagerLog(`[update:${phase}] ${message}`);
  }

  async checkForUpdates(channel?: ReleaseChannel): Promise<UpdateAvailable | null> {
    if (!updateManager.isBootstrapped()) {
      throw new Error('Bootstrap has not completed; nothing to update yet.');
    }
    const target = channel ?? (await updateManager.currentChannel());
    if (!target) {
      throw new Error('No release channel recorded.');
    }
    const result = await updateManager.checkForUpdates(target);
    if (result) {
      for (const listener of this.updateAvailableListeners) listener(result);
    }
    return result;
  }

  async applyUpdate(targetCommit: string, opts?: { channel?: ReleaseChannel }): Promise<void> {
    if (!updateManager.isBootstrapped()) {
      throw new Error('Bootstrap has not completed; nothing to update yet.');
    }
    const stampedChannel = await updateManager.currentChannel();
    const channel = opts?.channel ?? stampedChannel ?? 'stable';
    // prevChannel: the channel rollback should restore (may differ from `channel`
    // when switchChannel passed an explicit target).
    const prevChannel = stampedChannel ?? channel;
    const prevCommit =
      (await updateManager.currentCommit()) || (await updateManager.resolveRef('HEAD'));

    let phase: UpdatePhase = 'checkout';
    try {
      this.emitUpdateProgress(phase, `Stopping backend...`);
      await this.supervisor.stopAndWait('backend');

      this.emitUpdateProgress(phase, `Checking out ${targetCommit.slice(0, 7)}...`);
      await updateManager.checkoutCommit(targetCommit);

      phase = 'uv-sync';
      this.emitUpdateProgress(phase, 'Syncing Python dependencies...');
      await updateManager.syncPythonDeps({ offline: false });

      phase = 'npm-ci';
      this.emitUpdateProgress(phase, 'Installing frontend packages...');
      await updateManager.installNodeDeps({ offline: false });

      phase = 'npm-build';
      this.emitUpdateProgress(phase, 'Building frontend...');
      await updateManager.buildFrontend();

      phase = 'restart';
      this.emitUpdateProgress(phase, 'Starting backend...');
      await this.startBackend();

      phase = 'health-check';
      this.emitUpdateProgress(phase, 'Verifying backend...');
      await this.waitForBackend();

      await this.restartLocalServerIfRunning();

      await updateManager.stampVersion(channel, targetCommit);

      const version: RuntimeVersion = { commit: targetCommit, channel };
      for (const listener of this.updateAppliedListeners) listener(version);
      this.emitUpdateProgress('done', `Updated to ${targetCommit.slice(0, 7)}.`);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      const failure: UpdateFailure = { phase, reason };
      for (const listener of this.updateFailedListeners) listener(failure);

      try {
        await this.supervisor.stopAndWait('backend');
      } catch {
        // already dead
      }

      // Code-only rollback: DB schema may be forward-migrated and unrecoverable
      // without Factory Reset.
      try {
        this.emitUpdateProgress('rollback-checkout', `Rolling back to ${prevCommit.slice(0, 7)}...`);
        await updateManager.checkoutCommit(prevCommit);

        this.emitUpdateProgress('rollback-uv-sync', 'Restoring Python dependencies...');
        await updateManager.syncPythonDeps({ offline: false });

        this.emitUpdateProgress('rollback-npm-build', 'Rebuilding frontend...');
        await updateManager.installNodeDeps({ offline: false });
        await updateManager.buildFrontend();

        this.emitUpdateProgress('rollback-restart', 'Restarting backend...');
        await this.startBackend();
        await this.waitForBackend();
        await this.restartLocalServerIfRunning();

        await updateManager.stampVersion(prevChannel, prevCommit);
        const restored: RuntimeVersion = { commit: prevCommit, channel: prevChannel };
        for (const listener of this.updateAppliedListeners) listener(restored);
      } catch (rollbackError) {
        const rollbackReason =
          rollbackError instanceof Error ? rollbackError.message : String(rollbackError);
        const rollbackFailure: UpdateFailure = {
          phase: 'rollback-failed',
          reason: `Rollback failed: ${rollbackReason}. Original failure: ${reason}`,
        };
        for (const listener of this.updateFailedListeners) listener(rollbackFailure);
      }

      throw error;
    }
  }

  async switchChannel(channel: ReleaseChannel): Promise<void> {
    const branch = channel === 'stable' ? 'main' : 'beta';
    await updateManager.fetchChannel(channel);
    const targetSha = await updateManager.resolveRef(`origin/${branch}`);
    await this.applyUpdate(targetSha, { channel });
  }

  async revealAppSupport(): Promise<void> {
    await shell.openPath(appSupportRoot());
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
      const python = venvPython();
      if (!existsSync(python)) {
        throw new Error(`Backend venv missing at ${python}. Bootstrap did not complete.`);
      }
      return {
        command: python,
        args: ['-m', 'app.desktop_entry', '--host', '127.0.0.1', '--port', String(this.ports!.backend)],
        cwd: backendSourceDir(),
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
      if (command.includes('sentinel-backend') && command.includes('Sentinel.app/Contents/Resources/backend')) {
        return true;
      }
      if (command.includes('app.desktop_entry') && command.includes(appSupportRoot())) {
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
    // If we killed any postgres, the lockfile is also stale.
    if (stale.some((e) => e.command.includes(`-D ${dataDir}`) || e.command.includes(`-D${dataDir}`))) {
      await rm(path.join(dataDir, 'postmaster.pid'), { force: true });
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
