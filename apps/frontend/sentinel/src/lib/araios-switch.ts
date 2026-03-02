import { ARAIOS_APP_URL } from './env';

const DEFAULT_ARAIOS_SWITCH_URL = ARAIOS_APP_URL;
const ARAIOS_SWITCH_URL_STORAGE_KEY = 'sentinel.araios.switch.url';
export const ARAIOS_SWITCH_URL_EVENT = 'sentinel:araios-switch-url-updated';

function normalizeAraiosSwitchUrl(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim().replace(/\/+$/, '');
  if (!trimmed) {
    return null;
  }

  if (trimmed.startsWith('/')) {
    return trimmed;
  }

  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null;
    }
    return parsed.toString().replace(/\/+$/, '');
  } catch {
    return null;
  }
}

export function resolveAraiosSwitchUrl(baseUrl: string | null | undefined): string {
  return normalizeAraiosSwitchUrl(baseUrl) ?? DEFAULT_ARAIOS_SWITCH_URL;
}

export function readAraiosSwitchUrl(): string {
  if (typeof window === 'undefined') {
    return DEFAULT_ARAIOS_SWITCH_URL;
  }
  try {
    return resolveAraiosSwitchUrl(window.localStorage.getItem(ARAIOS_SWITCH_URL_STORAGE_KEY));
  } catch {
    return DEFAULT_ARAIOS_SWITCH_URL;
  }
}

export function persistAraiosSwitchUrl(baseUrl: string | null | undefined): string {
  const resolved = resolveAraiosSwitchUrl(baseUrl);

  if (typeof window !== 'undefined') {
    try {
      if (resolved === DEFAULT_ARAIOS_SWITCH_URL) {
        window.localStorage.removeItem(ARAIOS_SWITCH_URL_STORAGE_KEY);
      } else {
        window.localStorage.setItem(ARAIOS_SWITCH_URL_STORAGE_KEY, resolved);
      }
    } catch {
      // Ignore storage write failures and continue with runtime state update.
    }

    window.dispatchEvent(
      new CustomEvent(ARAIOS_SWITCH_URL_EVENT, {
        detail: { url: resolved },
      }),
    );
  }

  return resolved;
}
