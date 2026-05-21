import path from 'node:path';
import { appSupportRoot, resourceRoot } from './paths.js';
import { commandSearchPath } from './shell.js';

export interface DesktopPorts {
  app: number;
  backend: number;
  postgres: number;
}

export interface DesktopSecrets {
  jwtSecretKey: string;
}

export function postgresDataDir(): string {
  return path.join(appSupportRoot(), 'postgres/data');
}

export function desktopWorkspaceRoot(): string {
  return path.join(appSupportRoot(), 'workspaces');
}

export function desktopRunRoot(): string {
  return path.join(appSupportRoot(), 'run');
}

export function postgresBinaryPath(name: string): string {
  return path.join(resourceRoot(), 'postgres/bin', name);
}

export function postgresSharePath(): string {
  return path.join(resourceRoot(), 'postgres/share');
}

export function runtimeSeedRoot(): string {
  return path.join(resourceRoot(), 'runtime-seed');
}

export function seedPythonDir(): string {
  return path.join(runtimeSeedRoot(), 'python');
}

export function seedNodeDir(): string {
  return path.join(runtimeSeedRoot(), 'node');
}

export function pythonHome(): string {
  return path.join(appSupportRoot(), 'python');
}

export function nodeHome(): string {
  return path.join(appSupportRoot(), 'node');
}

export function bundledPythonBinary(): string {
  return path.join(pythonHome(), 'bin/python3');
}

export function bundledWheelsDir(): string {
  return path.join(runtimeSeedRoot(), 'wheels');
}

export function bundledNodeModulesArchive(): string {
  return path.join(runtimeSeedRoot(), 'node_modules-cache.tar.gz');
}

export function bundledSourceBareArchive(): string {
  return path.join(runtimeSeedRoot(), 'source.git.tar');
}

export function userDataBareSourceDir(): string {
  return path.join(appSupportRoot(), 'source.git');
}

export function sourceRoot(): string {
  return path.join(appSupportRoot(), 'source');
}

export function backendSourceDir(): string {
  return path.join(sourceRoot(), 'apps/backend/sentinel');
}

export function frontendSourceDir(): string {
  return path.join(sourceRoot(), 'apps/frontend/sentinel');
}

export function venvPython(): string {
  return path.join(backendSourceDir(), '.venv/bin/python');
}

export function bundledGitBinary(): string {
  return path.join(runtimeSeedRoot(), 'git/bin/git');
}

export function bundledGhBinary(): string {
  return path.join(runtimeSeedRoot(), 'gh/bin/gh');
}

export function bundledUvBinary(): string {
  return path.join(runtimeSeedRoot(), 'uv');
}

export function bundledNodeBinary(): string {
  return path.join(nodeHome(), 'bin/node');
}

export function bundledNpmBinary(): string {
  return path.join(nodeHome(), 'bin/npm');
}

export function runtimeCommitMarkerPath(): string {
  return path.join(sourceRoot(), '.runtime-commit');
}

export function runtimeChannelMarkerPath(): string {
  return path.join(sourceRoot(), '.runtime-channel');
}

export function runtimeCommandPath(pathValue = process.env.PATH || ''): string {
  return commandSearchPath(
    [
      path.join(resourceRoot(), 'postgres/bin'),
      path.dirname(bundledGitBinary()),
      path.dirname(bundledGhBinary()),
      pathValue,
    ].join(':'),
  );
}

export function buildBackendEnv(
  ports: DesktopPorts,
  secrets: DesktopSecrets,
  runtimeEnv: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  return {
    ...runtimeEnv,
    PATH: runtimeCommandPath(''),
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
    SESSION_RUNTIME_BASE_DIR: desktopWorkspaceRoot(),
    SENTINEL_RUNTIME_WORKSPACES_DIR: runtimeEnv.SENTINEL_RUNTIME_WORKSPACES_DIR || desktopWorkspaceRoot(),
    AUTH_COOKIE_SECURE: 'false',
    AUTH_COOKIE_SAMESITE: 'lax',
  };
}
