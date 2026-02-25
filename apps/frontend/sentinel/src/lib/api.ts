import { useAuthStore } from '../store/auth-store';
import { API_BASE_URL } from './env';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  authenticated?: boolean;
  timeoutMs?: number;
  allowRefresh?: boolean;
}

interface ErrorShape {
  error?: { message?: string; code?: string; details?: unknown };
  detail?: string;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;

  constructor(message: string, status: number, code?: string, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function errorMessage(payload: ErrorShape | null, fallback: string) {
  if (!payload) {
    return fallback;
  }
  if (payload.error?.message) {
    return payload.error.message;
  }
  if (payload.detail) {
    return payload.detail;
  }
  return fallback;
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET',
    body,
    authenticated = true,
    timeoutMs = 30_000,
    allowRefresh = true,
  } = options;

  const headers = new Headers();
  headers.set('Content-Type', 'application/json');

  if (authenticated) {
    const token = await useAuthStore.getState().getValidAccessToken();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    const payload = (await response.json().catch(() => null)) as T | ErrorShape | null;

    if (response.status === 401 && authenticated && allowRefresh) {
      const refreshed = await useAuthStore.getState().refresh();
      if (refreshed) {
        return requestJson<T>(path, { ...options, allowRefresh: false });
      }
      useAuthStore.getState().clearSession();
    }

    if (!response.ok) {
      const shape = payload as ErrorShape | null;
      throw new ApiError(
        errorMessage(shape, `Request failed (${response.status})`),
        response.status,
        shape?.error?.code,
        shape?.error?.details,
      );
    }

    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('Request timed out. Check your connection and retry.', 408);
    }
    throw new ApiError('Network error. Please retry.', 0);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'DELETE', body }),
};
