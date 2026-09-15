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
  getNotifications: () => ipcRenderer.invoke(IPC.getNotifications),
  publishNotification: input => ipcRenderer.invoke(IPC.publishNotification, input),
  onNotificationPublished: listener => subscribe(IPC.notificationPublished, listener),
  updateNotification: (id, action) => ipcRenderer.invoke(IPC.updateNotification, id, action),
  onNotifications: listener => subscribe(IPC.notificationsChanged, listener),
  openPreview: href => ipcRenderer.invoke(IPC.openPreview, href),
  syncForms: (instanceName, forms, viewedSessionId) => ipcRenderer.invoke(IPC.syncForms, instanceName, forms, viewedSessionId),
  onCompletionSound: listener => subscribe(IPC.completionSound, listener),
  onNotificationSettings: listener => subscribe(IPC.notificationSettingsChanged, listener),
  getNotificationSettings: () => ipcRenderer.invoke(IPC.getNotificationSettings),
  setNotificationSettings: settings => ipcRenderer.invoke(IPC.setNotificationSettings, settings),
  syncCompletions: (instanceName, sessions) => ipcRenderer.invoke(IPC.syncCompletions, instanceName, sessions),
  resizeFormWindow: height => ipcRenderer.invoke(IPC.resizeFormWindow, height),
  finishFormWindow: waitForTurn => ipcRenderer.invoke(IPC.finishFormWindow, waitForTurn),
  onFormCloseRequested: listener => subscribe(IPC.formCloseRequested, listener),
  socketOpen: (id, url) => ipcRenderer.invoke(IPC.socketOpen, id, url),
  socketSend: (id, data) => ipcRenderer.send(IPC.socketSend, id, data),
  socketClose: (id, code, reason) => ipcRenderer.send(IPC.socketClose, id, code, reason),
  onSocketEvent: listener => subscribe(IPC.socketEvent, listener),
  getStatus: () => ipcRenderer.invoke(IPC.getStatus),
  stopServices: () => ipcRenderer.invoke(IPC.stopServices),
  factoryReset: (scopes) => ipcRenderer.invoke(IPC.factoryReset, scopes),
  startServices: () => ipcRenderer.invoke(IPC.startServices),
  onGuidedTour: (listener) => subscribe(IPC.guidedTour, listener),
  onNavigate: (listener) => subscribe(IPC.navigate, listener),
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
