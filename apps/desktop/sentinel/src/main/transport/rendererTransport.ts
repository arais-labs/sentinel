import { app, ipcMain, net, protocol, type IpcMainEvent, type IpcMainInvokeEvent } from 'electron';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { existsSync } from 'node:fs';
import WebSocket from 'ws';
import { IPC, type SocketEvent } from '../../shared/ipc.js';
import { type LocalTransport } from './localTransport.js';
import { frontendDistPath } from '../paths.js';

protocol.registerSchemesAsPrivileged([{ scheme: 'sentinel', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true,
} }]);

export function installRendererTransport(getTransport: () => LocalTransport | undefined): void {
  function trusted(url: string): boolean {
    const parsed = new URL(url);
    if (parsed.protocol === 'sentinel:' && parsed.host === 'app') return true;
    return !app.isPackaged && Boolean(process.env.ELECTRON_RENDERER_URL)
      && parsed.origin === new URL(process.env.ELECTRON_RENDERER_URL!).origin;
  }
  function requireSender(event: IpcMainEvent | IpcMainInvokeEvent): void {
    if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame || !trusted(event.senderFrame.url)) {
      throw new Error('Untrusted desktop request');
    }
  }
  protocol.handle('sentinel', async request => {
    const url = new URL(request.url);
    if (url.host !== 'app') return new Response(null, { status: 403 });
    const origin = request.headers.get('origin');
    if (origin && !trusted(origin)) return new Response(null, { status: 403 });
    const cors: Record<string, string> = origin ? { 'access-control-allow-origin': origin, 'vary': 'Origin', 'access-control-expose-headers': 'Content-Disposition, Content-Length, Content-Type, Content-Range, Accept-Ranges, ETag' } : {};
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: {
      ...cors, 'access-control-allow-methods': 'GET, POST, PUT, PATCH, DELETE, HEAD',
      'access-control-allow-headers': 'content-type, range, if-range',
    } });
    if (/^\/(api\/|health(?:$|\/)|vnc\/)/.test(url.pathname)) {
      try {
        const transport = getTransport();
        if (!transport) throw new Error('Backend unavailable');
        const response = await transport.request(request);
        const headers = new Headers(response.headers);
        Object.entries(cors).forEach(([key, value]) => headers.set(key, value));
        return new Response(response.body, { status: response.status, headers });
      } catch {
        return Response.json({ detail: 'Sentinel service is unavailable.' }, { status: 503, headers: cors });
      }
    }
    const root = frontendDistPath();
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(root, relative);
    if (file !== root && !file.startsWith(root + path.sep)) return new Response(null, { status: 403 });
    const target = relative && existsSync(file) ? file : path.join(root, 'index.html');
    return net.fetch(pathToFileURL(target).href);
  });

  const connections = new Map<string, ReturnType<LocalTransport['connect']>>();
  const owners = new Set<number>();
  const keyFor = (event: IpcMainEvent | IpcMainInvokeEvent, id: string) => `${event.sender.id}:${id}`;
  ipcMain.handle(IPC.socketOpen, (event, id: string, url: string) => {
    requireSender(event);
    if (typeof id !== 'string' || id.length > 100 || typeof url !== 'string') throw new Error('Invalid socket request');
    const key = keyFor(event, id);
    if (connections.has(key)) throw new Error('Socket already open');
    const transport = getTransport();
    if (!transport) throw new Error('Backend unavailable');
    const socket = transport.connect(url);
    connections.set(key, socket);
    const emit = (value: SocketEvent) => { if (!event.sender.isDestroyed()) event.sender.send(IPC.socketEvent, value); };
    socket.on('open', () => emit({ id, type: 'open' }));
    socket.on('message', (data, binary) => emit({ id, type: 'message', data: binary ? new Uint8Array(data as Buffer) : data.toString() }));
    socket.on('error', () => emit({ id, type: 'error' }));
    socket.on('close', (code, reason) => { connections.delete(key); emit({ id, type: 'close', code, reason: reason.toString() }); });
    if (!owners.has(event.sender.id)) {
      owners.add(event.sender.id);
      const owner = event.sender.id;
      const cleanup = () => {
        for (const [socketKey, connection] of connections) if (socketKey.startsWith(`${owner}:`)) connection.terminate();
      };
      event.sender.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => { if (isMainFrame && !inPlace) cleanup(); });
      event.sender.once('destroyed', () => { cleanup(); owners.delete(owner); });
    }
  });
  ipcMain.on(IPC.socketSend, (event, id: string, data: string | Uint8Array) => {
    try {
      requireSender(event);
      const socket = connections.get(keyFor(event, id));
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (!(typeof data === 'string' || data instanceof Uint8Array)) return;
      if (socket.bufferedAmount > 8 * 1024 * 1024) { socket.close(1009, 'Send buffer exceeded'); return; }
      socket.send(data);
    } catch { /* Ignore messages from an untrusted or closed frame. */ }
  });
  ipcMain.on(IPC.socketClose, (event, id: string, code = 1000, reason = '') => {
    try {
      requireSender(event);
      const socket = connections.get(keyFor(event, id));
      if (socket?.readyState === WebSocket.CONNECTING) socket.terminate();
      else socket?.close(code, reason);
    } catch { /* A renderer may disappear while a socket is closing. */ }
  });
}
