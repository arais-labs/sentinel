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

export function backendBinaryPath(): string {
  return path.join(resourceRoot(), 'backend/sentinel-backend/sentinel-backend');
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
