const rawApiBase = (import.meta.env.VITE_SENTINEL_API_BASE_URL as string | undefined)?.trim();

export const API_BASE_URL = rawApiBase && rawApiBase.length > 0 ? rawApiBase : '/api/v1';
export const AUTH_BASE_URL = `${API_BASE_URL}/auth`;

// Derive WebSocket base: use current page origin with ws:// protocol
const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
export const WS_BASE_URL = `${wsProtocol}//${window.location.host}`;

export const APP_VERSION = '0.1.0';
