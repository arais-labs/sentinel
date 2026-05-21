export type ServiceName = 'postgres' | 'backend' | 'frontend';

export type ServiceState = 'stopped' | 'starting' | 'running' | 'stopping' | 'failed';

export interface ManagedServiceStatus {
  name: ServiceName;
  state: ServiceState;
  pid?: number;
  port?: number;
  message?: string;
  startedAt?: string;
  exitedAt?: string;
  exitCode?: number | null;
}

export interface DesktopStatus {
  appUrl?: string;
  appSupportPath: string;
  runtime: {
    provider: 'ssh';
    configured: boolean;
    host?: string;
    port?: number;
    username?: string;
    workspacesDir: string;
    authMethod: 'key' | 'password' | 'none';
    message?: string;
  };
  services: ManagedServiceStatus[];
}

export interface LogEntry {
  service: ServiceName | 'manager';
  line: string;
  at: string;
}

export interface FactoryResetScopes {
  db: boolean;
  runtimeData: boolean;
  appRuntime: boolean;
  logs: boolean;
}

export type ReleaseChannel = 'stable' | 'beta';

export interface RuntimeVersion {
  commit: string | null;
  channel: ReleaseChannel | 'dev';
}

export interface UpdateAvailable {
  channel: ReleaseChannel;
  currentCommit: string;
  targetCommit: string;
  subject: string;
  hasNewMigrations: boolean;
}

export type UpdatePhase =
  | 'snapshot'
  | 'fetch'
  | 'checkout'
  | 'uv-sync'
  | 'npm-ci'
  | 'npm-build'
  | 'restart'
  | 'health-check'
  | 'done'
  | 'rollback-checkout'
  | 'rollback-uv-sync'
  | 'rollback-npm-build'
  | 'rollback-restart'
  | 'rollback-failed'
  | 'rollback';

export interface UpdateProgress {
  phase: UpdatePhase;
  message: string;
}

export type BootstrapPhase =
  | 'extract-python'
  | 'extract-node'
  | 'extract-source'
  | 'extract-node-modules'
  | 'uv-sync'
  | 'npm-build'
  | 'done';

export interface BootstrapProgress {
  phase: BootstrapPhase;
  message: string;
  fractionComplete?: number;
}

export interface UpdateFailure {
  phase: UpdatePhase;
  reason: string;
  rolledBackTo?: string;
}

export interface DesktopApi {
  getStatus(): Promise<DesktopStatus>;
  stopServices(): Promise<DesktopStatus>;
  resetAuth(): Promise<DesktopStatus>;
  factoryReset(scopes: FactoryResetScopes): Promise<DesktopStatus>;
  openSentinel(): Promise<DesktopStatus>;
  showControlCenter(): Promise<void>;
  revealAppSupport(): Promise<void>;
  openLogFolder(): Promise<void>;
  getLogs(): Promise<LogEntry[]>;
  getVersion(): Promise<RuntimeVersion>;
  checkForUpdates(channel?: ReleaseChannel): Promise<UpdateAvailable | null>;
  applyUpdate(targetCommit: string): Promise<void>;
  switchChannel(channel: ReleaseChannel): Promise<void>;
  onStatus(listener: (status: DesktopStatus) => void): () => void;
  onLog(listener: (entry: LogEntry) => void): () => void;
  onBootstrapProgress(listener: (progress: BootstrapProgress) => void): () => void;
  onUpdateAvailable(listener: (info: UpdateAvailable) => void): () => void;
  onUpdateProgress(listener: (progress: UpdateProgress) => void): () => void;
  onUpdateApplied(listener: (version: RuntimeVersion) => void): () => void;
  onUpdateFailed(listener: (failure: UpdateFailure) => void): () => void;
}

export const IPC = {
  getStatus: 'desktop:getStatus',
  stopServices: 'desktop:stopServices',
  resetAuth: 'desktop:resetAuth',
  factoryReset: 'desktop:factoryReset',
  openSentinel: 'desktop:openSentinel',
  showControlCenter: 'desktop:showControlCenter',
  revealAppSupport: 'desktop:revealAppSupport',
  openLogFolder: 'desktop:openLogFolder',
  getLogs: 'desktop:getLogs',
  getVersion: 'desktop:getVersion',
  checkForUpdates: 'desktop:checkForUpdates',
  applyUpdate: 'desktop:applyUpdate',
  switchChannel: 'desktop:switchChannel',
  statusChanged: 'desktop:statusChanged',
  logEntry: 'desktop:logEntry',
  bootstrapProgress: 'desktop:bootstrapProgress',
  updateAvailable: 'desktop:updateAvailable',
  updateProgress: 'desktop:updateProgress',
  updateApplied: 'desktop:updateApplied',
  updateFailed: 'desktop:updateFailed',
} as const;
