import { app } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export function repoRoot(): string {
  if (process.env.SENTINEL_REPO_ROOT) {
    return process.env.SENTINEL_REPO_ROOT;
  }
  return path.resolve(__dirname, '../../../../..');
}

export function resourceRoot(): string {
  if (!app.isPackaged) {
    return repoRoot();
  }
  return process.resourcesPath;
}

export function appSupportRoot(): string {
  return path.join(app.getPath('userData'), 'runtime');
}

export function desktopAppRoot(): string {
  return path.resolve(__dirname, '../..');
}

export function frontendDistPath(): string {
  if (!app.isPackaged) {
    return path.join(repoRoot(), 'apps/frontend/sentinel/dist');
  }
  // In packaged mode the frontend dist is built at first launch into the
  // userData source tree (see desktopManager.bootstrapRuntime).
  return path.join(appSupportRoot(), 'source/apps/frontend/sentinel/dist');
}

export function backendPath(): string {
  if (!app.isPackaged) {
    return path.join(repoRoot(), 'apps/backend/sentinel');
  }
  // In packaged mode the backend lives inside the userData source tree.
  return path.join(appSupportRoot(), 'source/apps/backend/sentinel');
}
