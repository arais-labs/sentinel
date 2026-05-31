import { app, BrowserWindow, Menu, dialog, ipcMain, shell } from 'electron';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DesktopManager } from './desktopManager.js';
import { IPC, type DesktopStatus, type PayloadUpdate, type ReleaseChannel } from '../shared/ipc.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let mainWindow: BrowserWindow | undefined;
const manager = new DesktopManager();
let activeSentinelOrigin: string | undefined;
let isQuitting = false;
const singleInstanceLock = app.requestSingleInstanceLock();
app.setAppLogsPath();

// Developer Mode is a UI-only preference (toggled from the OS menu) that reveals
// the per-service detail, state-folder path, and payload-from-file install. It
// lives in a small settings file in userData so it survives restarts.
let devMode = loadDevMode();

function devSettingsPath(): string {
  return path.join(app.getPath('userData'), 'desktop-settings.json');
}

function loadDevMode(): boolean {
  try {
    return JSON.parse(readFileSync(devSettingsPath(), 'utf8')).devMode === true;
  } catch {
    return false;
  }
}

function setDevMode(value: boolean): void {
  devMode = value;
  try {
    writeFileSync(devSettingsPath(), JSON.stringify({ devMode: value }));
  } catch {
    // A failed write only means the preference won't persist; keep the session value.
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(IPC.devModeChanged, value);
  }
}

function rendererIndexPath(): string {
  return path.resolve(__dirname, '../../src/renderer/index.html');
}

function preloadPath(): string {
  return path.resolve(__dirname, '../preload/preload.js');
}

async function createWindow(): Promise<void> {
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 980,
    minHeight: 680,
    title: 'Sentinel',
    backgroundColor: '#09090b',
    webPreferences: {
      preload: preloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow = window;

  const sendToWindow = (channel: string, payload: unknown) => {
    if (isQuitting || window.isDestroyed() || window.webContents.isDestroyed()) return;
    window.webContents.send(channel, payload);
  };
  const unsubscribeStatus = manager.onStatus((status) => sendToWindow(IPC.statusChanged, status));
  const unsubscribeLog = manager.onLog((entry) => sendToWindow(IPC.logEntry, entry));
  const unsubscribePayloadProgress = manager.onPayloadProgress((progress) =>
    sendToWindow(IPC.payloadProgress, progress),
  );
  const unsubscribePayloadInstalled = manager.onPayloadInstalled((info) =>
    sendToWindow(IPC.payloadInstalled, info),
  );
  const unsubscribePayloadFailed = manager.onPayloadFailed((failure) =>
    sendToWindow(IPC.payloadFailed, failure),
  );
  window.once('closed', () => {
    unsubscribeStatus();
    unsubscribeLog();
    unsubscribePayloadProgress();
    unsubscribePayloadInstalled();
    unsubscribePayloadFailed();
    if (mainWindow === window) mainWindow = undefined;
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isSentinelUrl(url)) return { action: 'allow' };
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (isInternalAppUrl(url)) return;
    event.preventDefault();
    void shell.openExternal(url);
  });
  await window.loadFile(rendererIndexPath());
}

async function showControlCenter(): Promise<void> {
  activeSentinelOrigin = undefined;
  await ensureWindow();
  await mainWindow!.loadFile(rendererIndexPath());
}

async function showSentinel(status?: DesktopStatus): Promise<DesktopStatus> {
  const nextStatus = status?.appUrl ? status : await manager.startServices();
  // No payload installed yet (fresh shell): there is nothing to open, so fall
  // back to the Control Center where the user can install one from file.
  if (!nextStatus.appUrl) {
    await showControlCenter();
    return nextStatus;
  }
  activeSentinelOrigin = new URL(nextStatus.appUrl).origin;
  await ensureWindow();
  await mainWindow!.loadURL(nextStatus.appUrl);
  return nextStatus;
}

// Opens a native picker for a locally-built payload tarball and installs it.
// Returns false when the user cancels.
async function installPayloadFromFile(): Promise<boolean> {
  const result = await dialog.showOpenDialog({
    title: 'Install Sentinel Payload',
    properties: ['openFile'],
    filters: [{ name: 'Payload archive', extensions: ['gz', 'tgz', 'tar.gz'] }],
  });
  if (result.canceled || result.filePaths.length === 0) return false;
  await manager.installPayloadFromFile(result.filePaths[0]);
  return true;
}

async function ensureWindow(): Promise<void> {
  if (!mainWindow || mainWindow.isDestroyed()) {
    await createWindow();
  }
}

function isInternalAppUrl(url: string): boolean {
  return url.startsWith('file://') || isSentinelUrl(url);
}

function isSentinelUrl(url: string): boolean {
  if (!activeSentinelOrigin) return false;
  try {
    return new URL(url).origin === activeSentinelOrigin;
  } catch {
    return false;
  }
}

function installMenu(): void {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Sentinel',
      submenu: [
        { label: 'Control Center', click: () => void showControlCenter() },
        { label: 'Open Sentinel', click: () => void showSentinel() },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
    {
      label: 'Developer',
      submenu: [
        { role: 'toggleDevTools' },
        { type: 'separator' },
        {
          label: 'Developer Mode',
          type: 'checkbox',
          checked: devMode,
          click: (item) => setDevMode(item.checked),
        },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
  ]));
}

function registerIpc(): void {
  ipcMain.handle(IPC.getStatus, () => manager.getStatus());
  ipcMain.handle(IPC.stopServices, () => manager.stopServices());
  ipcMain.handle(IPC.resetAuth, () => manager.resetAuth());
  ipcMain.handle(IPC.factoryReset, async (_event, scopes) => {
    const status = await manager.factoryReset(scopes);
    app.relaunch();
    app.exit(0);
    return status;
  });
  ipcMain.handle(IPC.openSentinel, () => showSentinel());
  ipcMain.handle(IPC.showControlCenter, () => showControlCenter());
  ipcMain.handle(IPC.revealAppSupport, () => manager.revealAppSupport());
  ipcMain.handle(IPC.openLogFolder, () => manager.openLogFolder());
  ipcMain.handle(IPC.getLogs, () => manager.logs());
  ipcMain.handle(IPC.getPayload, () => manager.getPayload());
  ipcMain.handle(IPC.installPayloadFromFile, () => installPayloadFromFile());
  ipcMain.handle(IPC.getDevMode, () => devMode);
  ipcMain.handle(IPC.checkForUpdate, async (_event, channel?: ReleaseChannel) =>
    manager.checkForUpdate(channel),
  );
  ipcMain.handle(IPC.applyUpdate, async (_event, update: PayloadUpdate) =>
    manager.applyUpdate(update),
  );
}

if (!singleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      void createWindow();
      return;
    }
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });

  app.on('before-quit', (event) => {
    if (isQuitting) return;
    event.preventDefault();
    isQuitting = true;
    void manager.shutdown().finally(() => app.exit(0));
  });

  app.whenReady()
    .then(async () => {
      registerIpc();
      installMenu();
      await createWindow();
      void manager.initialize()
        .then(async (status) => {
          // Fresh shell with no payload: pull and install the latest release
          // automatically (stable, then beta) before opening Sentinel.
          if (!status.appUrl && !status.payload.installed) {
            const installed = await manager.autoInstallLatest();
            if (installed) return showSentinel();
          }
          return showSentinel(status);
        })
        .catch((error) => {
          const message = String(error?.stack || error);
          console.error(message);
          dialog.showErrorBox('Sentinel startup failed', message);
        });
      app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
          void createWindow();
        }
      });
    })
    .catch((error) => {
      void shell.openExternal(`data:text/plain,${encodeURIComponent(String(error?.stack || error))}`);
      app.quit();
    });
}
