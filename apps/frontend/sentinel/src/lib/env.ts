const rawApiBase = (import.meta.env.VITE_SENTINEL_API_BASE_URL as string | undefined)?.trim();
const rawSessionDebugPanel = (import.meta.env.VITE_SESSION_DEBUG_PANEL as string | undefined)?.trim().toLowerCase();

export const API_BASE_URL = rawApiBase && rawApiBase.length > 0 ? rawApiBase : '/api/v1';
export const AUTH_BASE_URL = `${API_BASE_URL}/auth`;
export const SESSION_DEBUG_PANEL_ENABLED = rawSessionDebugPanel === '1' || rawSessionDebugPanel === 'true' || rawSessionDebugPanel === 'yes';

// Derive WebSocket base: use current page origin with ws:// protocol
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
export const WS_BASE_URL = `${wsProtocol}//${window.location.host}`;

export const APP_VERSION = '0.1.0';

export function wsSessionsBaseUrl(instanceName: string): string {
  return `${WS_BASE_URL}/ws/instances/${encodeURIComponent(instanceName)}/sessions`;
}
