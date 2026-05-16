import path from 'node:path';
import { appSupportRoot, qemuResourcePath, resourceRoot } from './paths.js';
import { commandSearchPath } from './shell.js';

export interface DesktopPorts {
  app: number;
  backend: number;
  postgres: number;
  qemuSsh: number;
  qemuVnc: number;
  qemuCdp: number;
}

export interface DesktopSecrets {
  jwtSecretKey: string;
}

export function runtimeOutputDir(): string {
  return path.join(appSupportRoot(), 'qemu/output');
}

export function runtimeImagePath(): string {
  return path.join(runtimeOutputDir(), 'sentinel-runtime-base-arm64.qcow2');
}

export function runtimeKeyPath(): string {
  return path.join(runtimeOutputDir(), 'sentinel-runtime-base-arm64.id_ed25519');
}

export function postgresDataDir(): string {
  return path.join(appSupportRoot(), 'postgres/data');
}

export function desktopWorkspaceRoot(): string {
  return path.join(appSupportRoot(), 'workspaces');
}

export function qemuRunRoot(): string {
  return path.join(appSupportRoot(), 'qemu/run');
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

// Source-of-truth python/node trees ship inside the DMG's read-only resources.
// They are *copied* into userData on first bootstrap so all userData artefacts
// (venv, pyvenv.cfg, etc.) reference stable absolute paths that survive .app
// updates and DMG remounts.
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

export function qemuBinaryPath(name: string): string {
  return path.join(qemuResourcePath(), 'bin', name);
}

export function runtimeCommandPath(pathValue = process.env.PATH || ''): string {
  return commandSearchPath(`${path.join(resourceRoot(), 'postgres/bin')}:${path.join(qemuResourcePath(), 'bin')}:${pathValue}`);
}

export function buildBackendEnv(ports: DesktopPorts, secrets: DesktopSecrets): NodeJS.ProcessEnv {
  return {
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
    RUNTIME_EXEC_BACKEND: 'qemu',
    RUNTIME_QEMU_CONTROL: 'desktop',
    RUNTIME_QEMU_IMAGE: runtimeImagePath(),
    RUNTIME_QEMU_SSH_KEY_PATH: runtimeKeyPath(),
    RUNTIME_QEMU_WORKSPACE_ROOT: desktopWorkspaceRoot(),
    RUNTIME_QEMU_RUN_ROOT: qemuRunRoot(),
    RUNTIME_QEMU_HOST: '127.0.0.1',
    RUNTIME_QEMU_PUBLIC_HOST: '127.0.0.1',
    RUNTIME_QEMU_SSH_PORT: String(ports.qemuSsh),
    RUNTIME_QEMU_VNC_PORT: String(ports.qemuVnc),
    RUNTIME_QEMU_CDP_PORT: String(ports.qemuCdp),
    RUNTIME_WORKSPACES_HOST_DIR: desktopWorkspaceRoot(),
    AUTH_COOKIE_SECURE: 'false',
    AUTH_COOKIE_SAMESITE: 'lax',
  };
}
