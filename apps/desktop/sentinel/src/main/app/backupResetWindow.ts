import { app, BrowserWindow, dialog, Menu } from 'electron';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { backupAndReset, type BackupRoot } from './backupReset.js';

export const BACKUP_RESET_ARGUMENT = '--sentinel-backup-reset=';

// A separate launch keeps Electron caches and every application service closed
// while the original data is copied. This process uses disposable user data.
export async function runBackupReset(folder: string, roots: BackupRoot[]): Promise<never> {
  if (!app.requestSingleInstanceLock({ backupReset: true })) {
    app.exit(1);
    return new Promise<never>(() => {});
  }
  const scratch = mkdtempSync(path.join(tmpdir(), 'sentinel-reset-'));
  app.setPath('userData', scratch);
  app.setPath('sessionData', scratch);
  app.setAppLogsPath(path.join(scratch, 'logs'));
  await app.whenReady();
  Menu.setApplicationMenu(null);
  const window = new BrowserWindow({ width: 520, height: 240, resizable: false, minimizable: false,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, partition: 'sentinel-reset' } });
  window.on('close', event => event.preventDefault());
  app.on('before-quit', event => event.preventDefault());
  await window.loadURL(`data:text/html,${encodeURIComponent('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;background:#111;color:#eee;padding:28px}p{color:#aaa}</style><h2>Backing up Sentinel</h2><p id="status">Preparing backup…</p><p>Keep Sentinel open until this finishes.</p>')}`);
  let lastProgress = 0;
  try {
    const backup = await backupAndReset(folder, roots, message => {
      if (Date.now() - lastProgress < 200) return;
      lastProgress = Date.now();
      void window.webContents.executeJavaScript(`document.getElementById('status').textContent = ${JSON.stringify(message)}`).catch(() => {});
    });
    await dialog.showMessageBox(window, { type: 'info', message: 'Backup complete. Sentinel will restart cleanly.',
      detail: `Your verified backup is saved at:\n${backup}\n\nExternal project folders and remote machines were not changed.`, buttons: ['Restart Sentinel'] });
  } catch (error) {
    await dialog.showMessageBox(window, { type: 'error', message: 'Backup and reset could not finish',
      detail: `${error instanceof Error ? error.message : String(error)}\n\nData is only removed after every backup has been verified.`, buttons: ['Reopen Sentinel'] });
  }
  app.relaunch({ args: process.argv.slice(1).filter(arg => !arg.startsWith(BACKUP_RESET_ARGUMENT)) });
  app.exit(0);
  return new Promise<never>(() => {});
}
