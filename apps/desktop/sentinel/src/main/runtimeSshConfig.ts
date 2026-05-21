import { desktopWorkspaceRoot } from './desktopConfig.js';

export interface RuntimeSshConfig {
  provider: 'ssh';
  configured: boolean;
  host?: string;
  port?: number;
  username?: string;
  workspacesDir: string;
  authMethod: 'key' | 'password' | 'none';
  message: string;
}

function clean(value: string | undefined): string {
  return (value || '').trim();
}

export function runtimeSshEnv(source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    SENTINEL_RUNTIME_WORKSPACES_DIR: clean(source.SENTINEL_RUNTIME_WORKSPACES_DIR) || desktopWorkspaceRoot(),
  };
  for (const key of [
    'SENTINEL_RUNTIME_SSH_HOST',
    'SENTINEL_RUNTIME_SSH_PORT',
    'SENTINEL_RUNTIME_SSH_USERNAME',
    'SENTINEL_RUNTIME_SSH_KEY_PATH',
    'SENTINEL_RUNTIME_SSH_PASSWORD',
  ]) {
    const value = clean(source[key]);
    if (value) env[key] = value;
  }
  return env;
}

export function runtimeSshConfig(source: NodeJS.ProcessEnv = process.env): RuntimeSshConfig {
  const host = clean(source.SENTINEL_RUNTIME_SSH_HOST);
  const username = clean(source.SENTINEL_RUNTIME_SSH_USERNAME);
  const portRaw = clean(source.SENTINEL_RUNTIME_SSH_PORT);
  const keyPath = clean(source.SENTINEL_RUNTIME_SSH_KEY_PATH);
  const password = clean(source.SENTINEL_RUNTIME_SSH_PASSWORD);
  const workspacesDir = clean(source.SENTINEL_RUNTIME_WORKSPACES_DIR) || desktopWorkspaceRoot();
  const port = Number(portRaw || 22);
  const configured = Boolean(host && username && (keyPath || password));
  const authMethod = keyPath ? 'key' : password ? 'password' : 'none';
  return {
    provider: 'ssh',
    configured,
    host: host || undefined,
    port: Number.isInteger(port) && port > 0 ? port : 22,
    username: username || undefined,
    workspacesDir,
    authMethod,
    message: configured
      ? 'SSH runtime is configured from desktop launch environment.'
      : 'SSH runtime is not configured. Configure SENTINEL_RUNTIME_SSH_HOST, SENTINEL_RUNTIME_SSH_USERNAME, and SSH auth.',
  };
}
