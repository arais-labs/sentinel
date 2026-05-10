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

function rendererIndexPath(): string {
  return path.resolve(__dirname, '../../src/renderer/index.html');
}

function preloadPath(): string {
  return path.resolve(__dirname, '../preload/preload.js');
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
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

  manager.onStatus((status) => mainWindow?.webContents.send(IPC.statusChanged, status));
  manager.onLog((entry) => mainWindow?.webContents.send(IPC.logEntry, entry));
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSentinelUrl(url)) return { action: 'allow' };
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isInternalAppUrl(url)) return;
    event.preventDefault();
    void shell.openExternal(url);
  });
  await mainWindow.loadFile(rendererIndexPath());
}

async function showControlCenter(): Promise<void> {
  activeSentinelOrigin = undefined;
  await ensureWindow();
  await mainWindow!.loadFile(rendererIndexPath());
}

async function showSentinel(status?: DesktopStatus): Promise<DesktopStatus> {
  const nextStatus = status || await manager.getStatus();
  if (!nextStatus.appUrl) {
    throw new Error('No running Sentinel instance. Start an instance first.');
  }
  activeSentinelOrigin = new URL(nextStatus.appUrl).origin;
  await ensureWindow();
  await mainWindow!.loadURL(nextStatus.appUrl);
  return nextStatus;
}

function showSentinelAfterIpc(status: DesktopStatus): DesktopStatus {
  setTimeout(() => {
    void showSentinel(status).catch((error) => {
      dialog.showErrorBox('Sentinel open failed', String(error?.stack || error));
    });
  }, 0);
  return status;
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
  ipcMain.handle(IPC.createInstance, (_event, request) => manager.createInstance(request));
  ipcMain.handle(IPC.deleteInstance, (_event, name) => manager.deleteInstance(name));
  ipcMain.handle(IPC.startInstance, async (_event, name) => showSentinelAfterIpc(await manager.startInstance(name)));
  ipcMain.handle(IPC.stopInstance, () => manager.stopInstance());
  ipcMain.handle(IPC.restartInstance, async (_event, name) => showSentinelAfterIpc(await manager.restartInstance(name)));
  ipcMain.handle(IPC.renameInstance, (_event, name, newName) => manager.renameInstance(name, newName));
  ipcMain.handle(IPC.resetAuth, (_event, name, username, password) => manager.resetAuth(name, username, password));
  ipcMain.handle(IPC.backupInstance, (_event, name) => manager.backupInstance(name));
  ipcMain.handle(IPC.restoreInstance, (_event, request) => manager.restoreInstance(request));
  ipcMain.handle(IPC.buildQemuImage, () => manager.buildQemuImage());
  ipcMain.handle(IPC.validateQemuImage, () => manager.validateQemuImage());
  ipcMain.handle(IPC.openSentinel, () => showSentinel());
  ipcMain.handle(IPC.showControlCenter, () => showControlCenter());
  ipcMain.handle(IPC.revealAppSupport, () => manager.revealAppSupport());
  ipcMain.handle(IPC.getLogs, () => manager.logs());
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  manager.shutdown();
});

app.whenReady()
  .then(async () => {
    registerIpc();
    installMenu();
    await createWindow();
    void manager.initialize().catch((error) => {
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
