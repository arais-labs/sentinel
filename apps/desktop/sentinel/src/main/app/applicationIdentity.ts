import { mkdirSync } from 'node:fs';
import path from 'node:path';

type IdentityApp = Pick<Electron.App, 'isPackaged' | 'isReady' | 'setName' | 'getPath' | 'setPath' | 'setAppLogsPath'>;

/** Set identity before Electron initializes its OS credential store. */
export function configureApplicationIdentity(app: IdentityApp): void {
  if (app.isReady()) throw new Error('Application identity must be configured before Electron is ready.');
  if (!app.isPackaged) {
    // userData isolation alone does not isolate safeStorage: macOS derives the
    // Keychain service from the application name. Keep this name stable.
    app.setName('Sentinel Dev');
    const developmentData = path.join(app.getPath('appData'), 'Sentinel Dev');
    mkdirSync(developmentData, { recursive: true });
    app.setPath('userData', developmentData);
  }
  // Preserve the packaged application's existing identity and credential store.
  app.setAppLogsPath(app.isPackaged ? undefined : path.join(app.getPath('userData'), 'logs'));
}
