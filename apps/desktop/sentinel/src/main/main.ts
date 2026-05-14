import { app, BrowserWindow, Menu, dialog, ipcMain, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DesktopManager } from './desktopManager.js';
import { IPC, type DesktopStatus } from '../shared/ipc.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let mainWindow: BrowserWindow | undefined;
const manager = new DesktopManager();
let activeSentinelOrigin: string | undefined;
let isQuitting = false;
const singleInstanceLock = app.requestSingleInstanceLock();
app.setAppLogsPath();

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
  window.once('closed', () => {
    unsubscribeStatus();
    unsubscribeLog();
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
  if (!nextStatus.appUrl) {
    throw new Error('Sentinel is not running yet.');
  }
  activeSentinelOrigin = new URL(nextStatus.appUrl).origin;
  await ensureWindow();
  await mainWindow!.loadURL(nextStatus.appUrl);
  return nextStatus;
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
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
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
  ipcMain.handle(IPC.openSentinel, () => showSentinel());
  ipcMain.handle(IPC.showControlCenter, () => showControlCenter());
  ipcMain.handle(IPC.revealAppSupport, () => manager.revealAppSupport());
  ipcMain.handle(IPC.openLogFolder, () => manager.openLogFolder());
  ipcMain.handle(IPC.getLogs, () => manager.logs());
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
        .then((status) => showSentinel(status))
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
