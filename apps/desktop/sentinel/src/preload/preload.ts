import { contextBridge, ipcRenderer } from 'electron';
import type {
  DesktopApi,
  DesktopStatus,
  LogEntry,
  PayloadFailure,
  PayloadInfo,
  PayloadProgress,
  ReleaseChannel,
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
  getPayload: () => ipcRenderer.invoke(IPC.getPayload),
  installPayloadFromFile: () => ipcRenderer.invoke(IPC.installPayloadFromFile),
  getDevMode: () => ipcRenderer.invoke(IPC.getDevMode),
  checkForUpdate: (channel?: ReleaseChannel) => ipcRenderer.invoke(IPC.checkForUpdate, channel),
  applyUpdate: (update) => ipcRenderer.invoke(IPC.applyUpdate, update),
  onStatus: (listener: (status: DesktopStatus) => void) => subscribe(IPC.statusChanged, listener),
  onDevModeChanged: (listener: (devMode: boolean) => void) =>
    subscribe(IPC.devModeChanged, listener),
  onLog: (listener: (entry: LogEntry) => void) => subscribe(IPC.logEntry, listener),
  onPayloadProgress: (listener: (progress: PayloadProgress) => void) =>
    subscribe(IPC.payloadProgress, listener),
  onPayloadInstalled: (listener: (info: PayloadInfo) => void) =>
    subscribe(IPC.payloadInstalled, listener),
  onPayloadFailed: (listener: (failure: PayloadFailure) => void) =>
    subscribe(IPC.payloadFailed, listener),
};

contextBridge.exposeInMainWorld('sentinelDesktop', api);
