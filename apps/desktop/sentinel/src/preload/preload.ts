import { contextBridge, ipcRenderer } from 'electron';
import type {
  BootstrapProgress,
  DesktopApi,
  DesktopStatus,
  LogEntry,
  ReleaseChannel,
  RuntimeVersion,
  UpdateAvailable,
  UpdateFailure,
  UpdateProgress,
} from '../shared/ipc.js';
import { IPC } from '../shared/ipc.js';

function subscribe<T>(
  channel: string,
  listener: (payload: T) => void,
): () => void {
  const handler = (_event: Electron.IpcRendererEvent, payload: T) => listener(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.off(channel, handler);
}

const api: DesktopApi = {
  getStatus: () => ipcRenderer.invoke(IPC.getStatus),
  stopServices: () => ipcRenderer.invoke(IPC.stopServices),
  resetAuth: () => ipcRenderer.invoke(IPC.resetAuth),
  factoryReset: (scopes) => ipcRenderer.invoke(IPC.factoryReset, scopes),
  openSentinel: () => ipcRenderer.invoke(IPC.openSentinel),
  showControlCenter: () => ipcRenderer.invoke(IPC.showControlCenter),
  revealAppSupport: () => ipcRenderer.invoke(IPC.revealAppSupport),
  openLogFolder: () => ipcRenderer.invoke(IPC.openLogFolder),
  getLogs: () => ipcRenderer.invoke(IPC.getLogs),
  getVersion: () => ipcRenderer.invoke(IPC.getVersion),
  checkForUpdates: (channel?: ReleaseChannel) => ipcRenderer.invoke(IPC.checkForUpdates, channel),
  applyUpdate: (targetCommit: string) => ipcRenderer.invoke(IPC.applyUpdate, targetCommit),
  switchChannel: (channel: ReleaseChannel) => ipcRenderer.invoke(IPC.switchChannel, channel),
  onStatus: (listener: (status: DesktopStatus) => void) => subscribe(IPC.statusChanged, listener),
  onLog: (listener: (entry: LogEntry) => void) => subscribe(IPC.logEntry, listener),
  onBootstrapProgress: (listener: (progress: BootstrapProgress) => void) =>
    subscribe(IPC.bootstrapProgress, listener),
  onUpdateAvailable: (listener: (info: UpdateAvailable) => void) =>
    subscribe(IPC.updateAvailable, listener),
  onUpdateProgress: (listener: (progress: UpdateProgress) => void) =>
    subscribe(IPC.updateProgress, listener),
  onUpdateApplied: (listener: (version: RuntimeVersion) => void) =>
    subscribe(IPC.updateApplied, listener),
  onUpdateFailed: (listener: (failure: UpdateFailure) => void) =>
    subscribe(IPC.updateFailed, listener),
};

contextBridge.exposeInMainWorld('sentinelDesktop', api);
