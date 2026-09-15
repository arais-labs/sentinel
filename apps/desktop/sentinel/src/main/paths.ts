import { app } from 'electron';
import { existsSync } from 'node:fs';
import path from 'node:path';

export function repoRoot(): string {
  if (process.env.SENTINEL_REPO_ROOT) {
    return process.env.SENTINEL_REPO_ROOT;
  }
  return path.resolve(import.meta.dirname, '../../../../..');
}

export function resourceRoot(): string {
  if (!app.isPackaged) {
    return repoRoot();
  }
  return process.resourcesPath;
}

export function hostStateRoot(): string {
  return path.join(app.getPath('userData'), 'state');
}

export function desktopAppRoot(): string {
  return path.resolve(import.meta.dirname, '../..');
}

// The updatable app payload lives in writable userData (not read-only
// Resources) so it can be replaced without touching the signed .app shell.
export function payloadRoot(): string {
  return path.join(hostStateRoot(), 'payload');
}

// Extraction target for a pending install; swapped over payloadRoot() once the
// tarball is fully extracted and validated.
export function payloadStagingRoot(): string {
  return path.join(hostStateRoot(), 'payload.next');
}

export function payloadBackendDir(): string {
  return path.join(payloadRoot(), 'backend');
}

export function payloadSitePackagesDir(): string {
  return path.join(payloadRoot(), 'site-packages');
}

export function payloadFrontendDistDir(): string {
  return path.join(payloadRoot(), 'frontend/dist');
}

export function payloadManifestPath(): string {
  return path.join(payloadRoot(), 'manifest.json');
}

export function frontendDistPath(): string {
  if (!app.isPackaged) {
    return path.join(desktopAppRoot(), 'dist/renderer');
  }
  const installed = payloadFrontendDistDir();
  return existsSync(path.join(installed, 'index.html'))
    ? installed
    : path.join(desktopAppRoot(), 'dist/renderer');
}

export function backendPath(): string {
  if (!app.isPackaged) {
    return path.join(repoRoot(), 'apps/backend/sentinel');
  }
  return payloadBackendDir();
}
