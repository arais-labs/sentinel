import path from 'node:path';
import { hostStateRoot, payloadSitePackagesDir, resourceRoot } from '../paths.js';
import { commandSearchPath } from './shell.js';

export interface DesktopSecrets {
  desktopToken: string;
  dataEncryptionKey: string;
}

export function desktopRunRoot(): string {
  return path.join(hostStateRoot(), 'run');
}

// Version-independent tools bundled in the read-only .app Resources. These
// never change with an app update — only the payload does.
export function runtimeSeedRoot(): string {
  return path.join(resourceRoot(), 'runtime-seed');
}

// The interpreter that runs the frozen payload. Run straight from Resources;
// no copy into userData. python-build-standalone tolerates a read-only prefix
// (it just skips writing .pyc for the stdlib).
export function shellPythonBinary(): string {
  return path.join(runtimeSeedRoot(), 'python/bin/python3');
}

export function bundledGitBinary(): string {
  return path.join(runtimeSeedRoot(), 'git/bin/git');
}

export function bundledGhBinary(): string {
  return path.join(runtimeSeedRoot(), 'gh/bin/gh');
}

export function runtimeCommandPath(pathValue = process.env.PATH || ''): string {
  return commandSearchPath(
    [
      path.join(runtimeSeedRoot(), 'python/bin'),
      path.dirname(bundledGitBinary()),
      path.dirname(bundledGhBinary()),
      pathValue,
    ].join(':'),
  );
}

export function buildBackendEnv(secrets: DesktopSecrets): NodeJS.ProcessEnv {
  return {
    PATH: runtimeCommandPath(''),
    // Frozen dependencies live alongside the payload; the backend source is on
    // cwd (payload/backend), so `app.*` resolves without an editable install.
    PYTHONPATH: payloadSitePackagesDir(),
    LANG: 'C',
    LC_ALL: 'C',
    LC_CTYPE: 'C',
    APP_ENV: 'desktop',
    SENTINEL_STORAGE_ROOT: path.join(hostStateRoot(), 'storage'),
    SENTINEL_DESKTOP_TOKEN: secrets.desktopToken,
    DATA_ENCRYPTION_KEY: secrets.dataEncryptionKey,
  };
}
