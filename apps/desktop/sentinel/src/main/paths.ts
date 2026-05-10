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
  return path.join(resourceRoot(), 'frontend');
}

export function backendPath(): string {
  if (!app.isPackaged) {
    return path.join(repoRoot(), 'apps/backend/sentinel');
  }
  return path.join(resourceRoot(), 'backend');
}

export function qemuResourcePath(): string {
  if (!app.isPackaged) {
    return path.join(repoRoot(), 'infra/runtime/qemu');
  }
  return path.join(resourceRoot(), 'runtime/qemu');
}

export function instanceRoot(name: string): string {
  return path.join(appSupportRoot(), 'instances', name);
}

export function instanceEnvPath(name: string): string {
  return path.join(instanceRoot(name), '.env');
}
