const fallbackApiBase = 'http://localhost:8000/api/v1';
const fallbackAuthBase = '/platform/auth';

const rawApiBase = (import.meta.env.VITE_SENTINEL_API_BASE_URL as string | undefined)?.trim();
const rawAuthBase = (import.meta.env.VITE_PLATFORM_AUTH_BASE_URL as string | undefined)?.trim();

export const API_BASE_URL = rawApiBase && rawApiBase.length > 0 ? rawApiBase : fallbackApiBase;
export const AUTH_BASE_URL = rawAuthBase && rawAuthBase.length > 0 ? rawAuthBase : fallbackAuthBase;

export const WS_BASE_URL = API_BASE_URL.replace(/\/api\/v\d+$/, '').replace(/^http/, 'ws');

export const APP_VERSION = '0.1.0';
