import path from 'node:path';
import { hostStateRoot, payloadSitePackagesDir, resourceRoot } from './paths.js';
import { commandSearchPath } from './shell.js';

export interface DesktopPorts {
  app: number;
  backend: number;
  postgres: number;
}

export interface DesktopSecrets {
  jwtSecretKey: string;
  dataEncryptionKey: string;
}

export function postgresDataDir(): string {
  return path.join(hostStateRoot(), 'postgres/data');
}

export function desktopRunRoot(): string {
  return path.join(hostStateRoot(), 'run');
}

export function postgresBinaryPath(name: string): string {
  return path.join(resourceRoot(), 'postgres/bin', name);
}

export function postgresSharePath(): string {
  return path.join(resourceRoot(), 'postgres/share');
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
      path.join(resourceRoot(), 'postgres/bin'),
      path.join(runtimeSeedRoot(), 'python/bin'),
      path.dirname(bundledGitBinary()),
      path.dirname(bundledGhBinary()),
      pathValue,
    ].join(':'),
  );
}

export function buildBackendEnv(
  ports: DesktopPorts,
  secrets: DesktopSecrets,
): NodeJS.ProcessEnv {
  return {
    PATH: runtimeCommandPath(''),
    // Frozen dependencies live alongside the payload; the backend source is on
    // cwd (payload/backend), so `app.*` resolves without an editable install.
    PYTHONPATH: payloadSitePackagesDir(),
    LANG: 'C',
    LC_ALL: 'C',
    LC_CTYPE: 'C',
    APP_ENV: 'desktop',
    DATABASE_HOST: '127.0.0.1',
    DATABASE_PORT: String(ports.postgres),
    DATABASE_USER: 'sentinel',
    DATABASE_PASSWORD: 'sentinel',
    DATABASE_MAINTENANCE_NAME: 'postgres',
    DATABASE_MANAGER_NAME: 'sentinel_manager',
    JWT_SECRET_KEY: secrets.jwtSecretKey,
    DATA_ENCRYPTION_KEY: secrets.dataEncryptionKey,
    AUTH_COOKIE_SECURE: 'false',
    AUTH_COOKIE_SAMESITE: 'lax',
  };
}
