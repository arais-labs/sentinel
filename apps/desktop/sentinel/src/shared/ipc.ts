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

export type ReleaseChannel = 'stable' | 'beta';

// Describes the app payload currently installed in userData. The shell DMG
// ships no payload, so a fresh install reports installed=false until one is
// loaded from file or downloaded.
export interface PayloadInfo {
  installed: boolean;
  version: string | null;
  channel: ReleaseChannel | null;
  commit: string | null;
  builtAt: string | null;
}

export interface DesktopStatus {
  appUrl?: string;
  appSupportPath: string;
  payload: PayloadInfo;
  runtime: {
    provider: 'ssh';
    configured: boolean;
    authMethod: 'db';
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

// A newer payload available on a release channel, discovered by comparing the
// installed manifest against the channel's release index.
export interface PayloadUpdate {
  channel: ReleaseChannel;
  version: string;
  commit: string;
  url: string;
  sha256: string;
  hasNewMigrations: boolean;
}

export type PayloadPhase =
  | 'download'
  | 'verify'
  | 'extract'
  | 'swap'
  | 'restart'
  | 'health-check'
  | 'done';

export interface PayloadProgress {
  phase: PayloadPhase;
  message: string;
  fractionComplete?: number;
}

export interface PayloadFailure {
  phase: PayloadPhase;
  reason: string;
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
  getPayload(): Promise<PayloadInfo>;
  // Opens a native file picker in the main process and installs the chosen
  // payload tarball. Resolves false if the user cancels the picker.
  installFromFile(): Promise<boolean>;
  checkForUpdate(channel?: ReleaseChannel): Promise<PayloadUpdate | null>;
  applyUpdate(update: PayloadUpdate): Promise<void>;
  onStatus(listener: (status: DesktopStatus) => void): () => void;
  onLog(listener: (entry: LogEntry) => void): () => void;
  onPayloadProgress(listener: (progress: PayloadProgress) => void): () => void;
  onPayloadInstalled(listener: (info: PayloadInfo) => void): () => void;
  onPayloadFailed(listener: (failure: PayloadFailure) => void): () => void;
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
  getPayload: 'desktop:getPayload',
  installFromFile: 'desktop:installFromFile',
  checkForUpdate: 'desktop:checkForUpdate',
  applyUpdate: 'desktop:applyUpdate',
  statusChanged: 'desktop:statusChanged',
  logEntry: 'desktop:logEntry',
  payloadProgress: 'desktop:payloadProgress',
  payloadInstalled: 'desktop:payloadInstalled',
  payloadFailed: 'desktop:payloadFailed',
} as const;
