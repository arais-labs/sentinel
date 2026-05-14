import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopApi, DesktopStatus, LogEntry } from '../shared/ipc.js';
import { IPC } from '../shared/ipc.js';

const api: DesktopApi = {
  getStatus: () => ipcRenderer.invoke(IPC.getStatus),
  stopServices: () => ipcRenderer.invoke(IPC.stopServices),
  resetAuth: () => ipcRenderer.invoke(IPC.resetAuth),
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
