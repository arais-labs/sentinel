import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DesktopManager } from './desktopManager.js';
import { IPC } from '../shared/ipc.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let mainWindow: BrowserWindow | undefined;
const manager = new DesktopManager();

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
  await mainWindow.loadFile(rendererIndexPath());
}

function registerIpc(): void {
  ipcMain.handle(IPC.getStatus, () => manager.getStatus());
  ipcMain.handle(IPC.createInstance, (_event, request) => manager.createInstance(request));
  ipcMain.handle(IPC.deleteInstance, (_event, name) => manager.deleteInstance(name));
  ipcMain.handle(IPC.startInstance, (_event, name) => manager.startInstance(name));
  ipcMain.handle(IPC.stopInstance, () => manager.stopInstance());
  ipcMain.handle(IPC.restartInstance, (_event, name) => manager.restartInstance(name));
  ipcMain.handle(IPC.resetAuth, (_event, name, username, password) => manager.resetAuth(name, username, password));
  ipcMain.handle(IPC.backupInstance, (_event, name) => manager.backupInstance(name));
  ipcMain.handle(IPC.restoreInstance, (_event, name, backupPath) => manager.restoreInstance(name, backupPath));
  ipcMain.handle(IPC.buildQemuImage, () => manager.buildQemuImage());
  ipcMain.handle(IPC.validateQemuImage, () => manager.validateQemuImage());
  ipcMain.handle(IPC.openSentinel, () => manager.openSentinel());
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
