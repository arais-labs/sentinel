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
  qemu: {
    installed: boolean;
    qemuSystemPath?: string;
    qemuImgPath?: string;
    message?: string;
  };
  runtimeImage: {
    imagePath: string;
    keyPath: string;
    present: boolean;
  };
  services: ManagedServiceStatus[];
}

export interface LogEntry {
  service: ServiceName | 'manager';
  line: string;
  at: string;
}

export interface DesktopApi {
  getStatus(): Promise<DesktopStatus>;
  stopServices(): Promise<DesktopStatus>;
  resetAuth(): Promise<DesktopStatus>;
  factoryReset(): Promise<DesktopStatus>;
  openSentinel(): Promise<DesktopStatus>;
  showControlCenter(): Promise<void>;
  revealAppSupport(): Promise<void>;
  openLogFolder(): Promise<void>;
  getLogs(): Promise<LogEntry[]>;
  onStatus(listener: (status: DesktopStatus) => void): () => void;
  onLog(listener: (entry: LogEntry) => void): () => void;
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
  statusChanged: 'desktop:statusChanged',
  logEntry: 'desktop:logEntry',
} as const;
