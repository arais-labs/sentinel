const fallbackApiBase = 'http://localhost:8000/api/v1';
const fallbackSentinelAppUrl = '/sentinel/';
const fallbackAraiosAppUrl = '/araios/';

const rawApiBase = (import.meta.env.VITE_SENTINEL_API_BASE_URL as string | undefined)?.trim();
const rawSentinelAppUrl = (import.meta.env.APP_SENTINEL_URL as string | undefined)?.trim();
const rawAraiosAppUrl = (import.meta.env.APP_ARAIOS_URL as string | undefined)?.trim();

function resolveAppUrl(value: string | undefined, fallback: string): string {
  if (!value) {
    return fallback;
  }
  if (value.startsWith('/')) {
    return value;
  }
  try {
    const parsed = new URL(value);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.toString();
    }
  } catch {
    // Fall through to fallback.
  }
  return fallback;
}

export const API_BASE_URL = rawApiBase && rawApiBase.length > 0 ? rawApiBase : fallbackApiBase;
export const AUTH_BASE_URL = `${API_BASE_URL}/auth`;

export const WS_BASE_URL = API_BASE_URL.replace(/\/api\/v\d+$/, '').replace(/^http/, 'ws');
export const SENTINEL_APP_URL = resolveAppUrl(rawSentinelAppUrl, fallbackSentinelAppUrl);
export const ARAIOS_APP_URL = resolveAppUrl(rawAraiosAppUrl, fallbackAraiosAppUrl);

export const APP_VERSION = '0.1.0';
