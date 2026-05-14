import { useAuthStore } from '../store/auth-store';
import { API_BASE_URL } from './env';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  authenticated?: boolean;
  timeoutMs?: number;
  allowRefresh?: boolean;
}

interface ErrorShape {
  error?: { message?: string; code?: string; details?: unknown };
  detail?: string;
}

interface DownloadResponse {
  blob: Blob;
  filename: string | null;
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

function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const plainMatch = header.match(/filename=\"?([^\";]+)\"?/i);
  return plainMatch?.[1] ?? null;
}

function currentInstanceName(): string | null {
  const match = window.location.pathname.match(/^\/instances\/([^/]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

function scopedPath(path: string): string {
  if (!path.startsWith('/')) return path;
  if (path === '/instances' || path.startsWith('/instances/')) return path;
  if (path === '/auth' || path.startsWith('/auth/')) return path;
  const instanceName = currentInstanceName();
  if (!instanceName) return path;
  return `/instances/${encodeURIComponent(instanceName)}${path}`;
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

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE_URL}${scopedPath(path)}`, {
      method,
      headers,
      credentials: 'include',
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

export async function requestBlob(path: string, options: RequestOptions = {}): Promise<DownloadResponse> {
  const {
    method = 'GET',
    body,
    authenticated = true,
    timeoutMs = 30_000,
    allowRefresh = true,
  } = options;

  const headers = new Headers();
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE_URL}${scopedPath(path)}`, {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (response.status === 401 && authenticated && allowRefresh) {
      const refreshed = await useAuthStore.getState().refresh();
      if (refreshed) {
        return requestBlob(path, { ...options, allowRefresh: false });
      }
      useAuthStore.getState().clearSession();
    }

    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as ErrorShape | null;
      throw new ApiError(
        errorMessage(payload, `Request failed (${response.status})`),
        response.status,
        payload?.error?.code,
        payload?.error?.details,
      );
    }

    return {
      blob: await response.blob(),
      filename: filenameFromContentDisposition(response.headers.get('Content-Disposition')),
    };
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
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'PATCH', body }),
  delete: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestJson<T>(path, { ...options, method: 'DELETE', body }),
  download: (path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    requestBlob(path, { ...options, method: 'GET' }),
};
