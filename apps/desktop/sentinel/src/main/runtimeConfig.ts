import path from 'node:path';
import { appSupportRoot, instanceRoot, qemuResourcePath, resourceRoot } from './paths.js';
import { commandSearchPath } from './shell.js';

export interface DesktopPorts {
  app: number;
  backend: number;
  postgres: number;
  qemuSsh: number;
  qemuVnc: number;
  qemuCdp: number;
}

export const DESKTOP_POSTGRES_PASSWORD = 'sentinel';

export function instancesRoot(): string {
  return path.join(appSupportRoot(), 'instances');
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

export function postgresBinaryPath(name: string): string {
  return path.join(resourceRoot(), 'postgres/bin', name);
}

export function pythonBinaryPath(): string {
  return path.join(resourceRoot(), 'python/bin/python3');
}

export function qemuBinaryPath(name: string): string {
  return path.join(qemuResourcePath(), 'bin', name);
}

export function runtimeCommandPath(pathValue = process.env.PATH || ''): string {
  return commandSearchPath(`${path.join(resourceRoot(), 'postgres/bin')}:${path.join(resourceRoot(), 'python/bin')}:${path.join(qemuResourcePath(), 'bin')}:${pathValue}`);
}

export function buildBackendEnv(
  instance: string,
  values: Record<string, string>,
  ports: DesktopPorts,
): NodeJS.ProcessEnv {
  const password = values.POSTGRES_PASSWORD || DESKTOP_POSTGRES_PASSWORD;
  const db = values.POSTGRES_DB || 'sentinel';
  const user = values.POSTGRES_USER || 'sentinel';
  return {
    ...process.env,
    ...values,
    PATH: runtimeCommandPath(values.PATH || process.env.PATH || ''),
    PYTHONHOME: path.join(resourceRoot(), 'python'),
    PYTHONNOUSERSITE: '1',
    LANG: values.LANG || process.env.LANG || 'C',
    LC_ALL: values.LC_ALL || process.env.LC_ALL || 'C',
    APP_ENV: 'desktop',
    DATABASE_URL: `postgresql+asyncpg://${user}:${password}@127.0.0.1:${ports.postgres}/${db}`,
    RUNTIME_EXEC_BACKEND: 'qemu',
    RUNTIME_QEMU_CONTROL: 'desktop',
    RUNTIME_QEMU_IMAGE: runtimeImagePath(),
    RUNTIME_QEMU_SSH_KEY_PATH: runtimeKeyPath(),
    RUNTIME_QEMU_WORKSPACE_ROOT: path.join(instanceRoot(instance), 'workspaces'),
    RUNTIME_QEMU_RUN_ROOT: path.join(instanceRoot(instance), 'qemu-run'),
    RUNTIME_QEMU_HOST: '127.0.0.1',
    RUNTIME_QEMU_PUBLIC_HOST: '127.0.0.1',
    RUNTIME_QEMU_SSH_PORT: String(ports.qemuSsh),
    RUNTIME_QEMU_VNC_PORT: String(ports.qemuVnc),
    RUNTIME_QEMU_CDP_PORT: String(ports.qemuCdp),
    RUNTIME_WORKSPACES_HOST_DIR: path.join(instanceRoot(instance), 'workspaces'),
    AUTH_COOKIE_SECURE: 'false',
    AUTH_COOKIE_SAMESITE: 'lax',
  };
}
