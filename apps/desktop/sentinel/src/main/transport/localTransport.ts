import http from 'node:http';
import { Readable, pipeline } from 'node:stream';
import type { ReadableStream as NodeReadableStream } from 'node:stream/web';
import WebSocket from 'ws';
import { DesktopStream } from './desktopStream.js';

export const APP_URL = 'sentinel://app/';
export function backendPath(url: string): string {
  const parsed = new URL(url, APP_URL);
  if (!/^\/(api\/|ws\/|health(?:$|\/)|vnc\/)/.test(parsed.pathname)) {
    throw new Error('Unsupported backend path');
  }
  return parsed.pathname + parsed.search;
}

// HTTP framing stays inside the Unix socket; no backend TCP listener is needed.
export class LocalTransport {
  constructor(readonly socketPath: string, private readonly token: string) {}

  async request(request: Request): Promise<Response> {
    return new Promise((resolve, reject) => {
      const headers = Object.fromEntries(request.headers);
      delete headers.host;
      delete headers.connection;
      delete headers['content-length'];
      delete headers['transfer-encoding'];
      // Node does not automatically frame DELETE bodies. Stream with explicit
      // framing for every method so provider deletion and file uploads both work.
      if (request.body) headers['transfer-encoding'] = 'chunked';
      headers['x-sentinel-desktop-token'] = this.token;
      const upstream = http.request({
        socketPath: this.socketPath, path: backendPath(request.url), method: request.method,
        headers,
      }, response => {
        const responseHeaders = new Headers();
        for (const [key, value] of Object.entries(response.headers)) {
          if (value !== undefined && !['connection', 'transfer-encoding'].includes(key)) {
            responseHeaders.set(key, Array.isArray(value) ? value.join(', ') : value);
          }
        }
        const status = response.statusCode || 502;
        const empty = request.method === 'HEAD' || [204, 205, 304].includes(status);
        if (empty) response.resume();
        resolve(new Response(empty ? null : Readable.toWeb(response) as ReadableStream<Uint8Array>, {
          status, headers: responseHeaders,
        }));
      });
      const abort = () => upstream.destroy(new Error('Request cancelled'));
      request.signal.addEventListener('abort', abort, { once: true });
      upstream.once('close', () => request.signal.removeEventListener('abort', abort));
      upstream.once('error', reject);
      if (request.signal.aborted) abort();
      else if (request.body) {
        pipeline(Readable.fromWeb(request.body as NodeReadableStream<Uint8Array>), upstream, error => {
          if (error) reject(error);
        });
      } else upstream.end();
    });
  }

  connect(url: string): WebSocket | DesktopStream {
    const route = backendPath(url);
    if (/^\/api\/v1\/instances\/[^/?]+\/runtime\/live-view\/[0-9a-f-]{36}\/stream$/.test(route)) {
      return new DesktopStream(async signal => {
        const response = await this.request(new Request(`sentinel://app${route}`, { method: 'POST', signal }));
        if (!response.ok) throw new Error('Desktop unavailable');
        const result = await response.json() as { socket?: unknown };
        if (typeof result.socket !== 'string') throw new Error('Invalid desktop connection');
        return result.socket;
      });
    }
    return new WebSocket(`ws+unix:${this.socketPath}:${route}`, {
      headers: { 'x-sentinel-desktop-token': this.token },
      maxPayload: 16 * 1024 * 1024,
    });
  }
}
