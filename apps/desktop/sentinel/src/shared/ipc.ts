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

export interface InstanceSummary {
  name: string;
  backend: 'qemu';
  stackPort?: number;
  state: 'stopped' | 'running' | 'partial' | 'failed';
  configPath: string;
  workspacePath: string;
  qemuRunPath: string;
  databaseName: string;
}

export interface DesktopStatus {
  appUrl?: string;
  appSupportPath: string;
  activeInstance?: string;
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
  instances: InstanceSummary[];
}

export interface LogEntry {
  service: ServiceName | 'manager';
  line: string;
  at: string;
}

export interface CreateInstanceRequest {
  name: string;
  stackPort?: number;
  username?: string;
  password?: string;
}

export interface RestoreInstanceRequest {
  name: string;
  backupPath: string;
}

export interface DesktopApi {
  getStatus(): Promise<DesktopStatus>;
  createInstance(request: CreateInstanceRequest): Promise<DesktopStatus>;
  deleteInstance(name: string): Promise<DesktopStatus>;
  startInstance(name: string): Promise<DesktopStatus>;
  stopInstance(): Promise<DesktopStatus>;
  restartInstance(name: string): Promise<DesktopStatus>;
  renameInstance(name: string, newName: string): Promise<DesktopStatus>;
  resetAuth(name: string, username: string, password: string): Promise<DesktopStatus>;
  backupInstance(name: string): Promise<string>;
  restoreInstance(request: RestoreInstanceRequest): Promise<DesktopStatus>;
  openSentinel(): Promise<DesktopStatus>;
  showControlCenter(): Promise<void>;
  revealAppSupport(): Promise<void>;
  getLogs(): Promise<LogEntry[]>;
  onStatus(listener: (status: DesktopStatus) => void): () => void;
  onLog(listener: (entry: LogEntry) => void): () => void;
}

export const IPC = {
  getStatus: 'desktop:getStatus',
  createInstance: 'desktop:createInstance',
  deleteInstance: 'desktop:deleteInstance',
  startInstance: 'desktop:startInstance',
  stopInstance: 'desktop:stopInstance',
  restartInstance: 'desktop:restartInstance',
  renameInstance: 'desktop:renameInstance',
  resetAuth: 'desktop:resetAuth',
  backupInstance: 'desktop:backupInstance',
  restoreInstance: 'desktop:restoreInstance',
  openSentinel: 'desktop:openSentinel',
  showControlCenter: 'desktop:showControlCenter',
  revealAppSupport: 'desktop:revealAppSupport',
  getLogs: 'desktop:getLogs',
  statusChanged: 'desktop:statusChanged',
  logEntry: 'desktop:logEntry',
} as const;
