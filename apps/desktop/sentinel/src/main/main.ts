import { app, dialog } from 'electron';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { startDesktopApplication } from './desktopApplication.js';
import { BACKUP_RESET_ARGUMENT, runBackupReset } from './app/backupResetWindow.js';

if (!app.isPackaged) {
  // Keep development data separate from the installed application.
  const developmentData = path.join(app.getPath('appData'), 'Sentinel Dev');
  mkdirSync(developmentData, { recursive: true });
  app.setPath('userData', developmentData);
}
app.setAppLogsPath(app.isPackaged ? undefined : path.join(app.getPath('userData'), 'logs'));

const resetArgument = process.argv.find(arg => arg.startsWith(BACKUP_RESET_ARGUMENT));
if (resetArgument) {
  // Do not await readiness at module scope: Electron emits ready only after
  // its entry module finishes loading. The reset launch owns its own lifecycle.
  void runBackupReset(resetArgument.slice(BACKUP_RESET_ARGUMENT.length), [
    { name: 'app-data', path: app.getPath('userData') },
    { name: 'logs', path: app.getPath('logs') },
  ]).catch(error => {
    dialog.showErrorBox('Could not start backup and reset', String(error));
    app.exit(1);
  });
} else {
  startDesktopApplication();
}
