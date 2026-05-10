import { contextBridge, ipcRenderer } from 'electron';
import type { CreateInstanceRequest, DesktopApi, DesktopStatus, LogEntry } from '../shared/ipc.js';
import { IPC } from '../shared/ipc.js';

const api: DesktopApi = {
  getStatus: () => ipcRenderer.invoke(IPC.getStatus),
  createInstance: (request: CreateInstanceRequest) => ipcRenderer.invoke(IPC.createInstance, request),
  deleteInstance: (name: string) => ipcRenderer.invoke(IPC.deleteInstance, name),
  startInstance: (name: string) => ipcRenderer.invoke(IPC.startInstance, name),
  stopInstance: () => ipcRenderer.invoke(IPC.stopInstance),
  restartInstance: (name: string) => ipcRenderer.invoke(IPC.restartInstance, name),
  resetAuth: (name: string, username: string, password: string) => ipcRenderer.invoke(IPC.resetAuth, name, username, password),
  backupInstance: (name: string) => ipcRenderer.invoke(IPC.backupInstance, name),
  restoreInstance: (name: string, backupPath: string) => ipcRenderer.invoke(IPC.restoreInstance, name, backupPath),
  buildQemuImage: () => ipcRenderer.invoke(IPC.buildQemuImage),
  validateQemuImage: () => ipcRenderer.invoke(IPC.validateQemuImage),
  openSentinel: () => ipcRenderer.invoke(IPC.openSentinel),
  showControlCenter: () => ipcRenderer.invoke(IPC.showControlCenter),
  revealAppSupport: () => ipcRenderer.invoke(IPC.revealAppSupport),
  getLogs: () => ipcRenderer.invoke(IPC.getLogs),
  onStatus: (listener: (status: DesktopStatus) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: DesktopStatus) => listener(status);
    ipcRenderer.on(IPC.statusChanged, handler);
    return () => ipcRenderer.off(IPC.statusChanged, handler);
  },
  onLog: (listener: (entry: LogEntry) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, entry: LogEntry) => listener(entry);
    ipcRenderer.on(IPC.logEntry, handler);
    return () => ipcRenderer.off(IPC.logEntry, handler);
  },
};

contextBridge.exposeInMainWorld('sentinelDesktop', api);
