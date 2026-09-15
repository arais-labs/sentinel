import { API_BASE_URL } from './env';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  timeoutMs?: number;
  signal?: AbortSignal;
  rawBody?: Blob;
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
  if (path === '/machines' || path.startsWith('/machines/')) return path;
  if (path === '/agent-modes' || path.startsWith('/agent-modes/')) return path;
  const instanceName = currentInstanceName();
  if (!instanceName) {
    throw new Error(
      `API call to "${path}" requires an instance context but the current URL has none.`,
    );
  }
  return `/instances/${encodeURIComponent(instanceName)}${path}`;
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${scopedPath(path)}`;
}

/** Let Chromium stream downloads to disk; never materialize file bodies as Blobs. */
export function downloadFile(path: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = apiUrl(path);
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET',
    body,
    timeoutMs = 30_000,
  } = options;

  const headers = new Headers();
  headers.set('Content-Type', options.rawBody ? 'application/octet-stream' : 'application/json');

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) controller.abort();

  try {
    const response = await fetch(`${API_BASE_URL}${scopedPath(path)}`, {
      method,
      headers,
      body: options.rawBody ?? (body === undefined ? undefined : JSON.stringify(body)),
      signal: controller.signal,
    });

    const payload = (await response.json().catch(() => null)) as T | ErrorShape | null;

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
      if (options.signal?.aborted) throw new ApiError('Upload cancelled.', 499);
      throw new ApiError('Sentinel service took too long to respond. Please retry.', 408);
    }
    throw new ApiError('Sentinel service is unavailable. Please retry.', 0);
  } finally {
    window.clearTimeout(timeoutId);
    options.signal?.removeEventListener('abort', cancel);
  }
}

export async function requestBlob(path: string, options: RequestOptions = {}): Promise<DownloadResponse> {
  const {
    method = 'GET',
    body,
    timeoutMs = 30_000,
  } = options;

  const headers = new Headers();
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) controller.abort();

  try {
    const response = await fetch(`${API_BASE_URL}${scopedPath(path)}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

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
      throw new ApiError('Sentinel service took too long to respond. Please retry.', 408);
    }
    throw new ApiError('Sentinel service is unavailable. Please retry.', 0);
  } finally {
    window.clearTimeout(timeoutId);
    options.signal?.removeEventListener('abort', cancel);
  }
}

export const api = {
  upload: <T>(path: string, body: Blob, signal?: AbortSignal) =>
    requestJson<T>(path, { method: 'POST', rawBody: body, signal, timeoutMs: 1800_000 }),
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
