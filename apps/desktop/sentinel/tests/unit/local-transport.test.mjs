import assert from 'node:assert/strict';
import { once } from 'node:events';
import http from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { WebSocketServer } from 'ws';
import { backendPath, LocalTransport } from '../../.test-dist/main/transport/localTransport.js';

async function fixture(t, handler) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'st-ipc-'));
  const socketPath = path.join(dir, 'api.sock');
  const server = http.createServer(handler);
  const sockets = new Set();
  server.on('connection', socket => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    await rm(dir, { recursive: true, force: true });
  });
  server.listen(socketPath);
  await once(server, 'listening');
  return { server, transport: new LocalTransport(socketPath, 'private-test-token') };
}

const options = { timeout: 5000 };

test('Unix HTTP preserves binary bodies, status and headers and supplies the app credential', options, async t => {
  const payload = Buffer.from([0, 255, 128, 13, 10, 42]);
  const { transport } = await fixture(t, async (req, res) => {
    assert.equal(req.url, '/api/v1/upload?name=hello%20world');
    assert.equal(req.method, 'POST');
    assert.equal(req.headers['x-sentinel-desktop-token'], 'private-test-token');
    assert.equal(req.headers['content-type'], 'application/octet-stream');
    const parts = [];
    for await (const part of req) parts.push(part);
    assert.deepEqual(Buffer.concat(parts), payload);
    res.writeHead(201, { 'content-type': 'application/octet-stream', 'x-result': 'created' });
    res.end(payload);
  });
  const response = await transport.request(new Request('sentinel://app/api/v1/upload?name=hello%20world', {
    method: 'POST', body: payload,
    headers: { 'content-type': 'application/octet-stream', 'x-sentinel-desktop-token': 'spoofed' },
  }));
  assert.equal(response.status, 201);
  assert.equal(response.headers.get('x-result'), 'created');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), payload);
});

test('streaming exposes the first event before the backend completes', options, async t => {
  let finish;
  const { transport } = await fixture(t, (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: first\n\n');
    finish = () => res.end('data: last\n\n');
  });
  const response = await transport.request(new Request('sentinel://app/api/v1/events'));
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(first.done, false);
  assert.equal(new TextDecoder().decode(first.value), 'data: first\n\n');
  finish();
  const rest = [];
  for (;;) {
    const next = await reader.read();
    if (next.done) break;
    rest.push(Buffer.from(next.value));
  }
  assert.equal(Buffer.concat(rest).toString(), 'data: last\n\n');
});

test('DELETE forwards the provider selection with explicit HTTP body framing', options, async t => {
  const { transport } = await fixture(t, async (req, res) => {
    const parts = [];
    for await (const part of req) parts.push(part);
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ method: req.method, body: Buffer.concat(parts).toString() }));
  });
  const body = JSON.stringify({ provider: 'anthropic' });
  const response = await transport.request(new Request('sentinel://app/api/v1/instances/test/settings/api-keys', {
    method: 'DELETE', body, headers: { 'content-type': 'application/json' },
  }));
  assert.deepEqual(await response.json(), { method: 'DELETE', body });
});

test('upload forwards the first chunk before the source finishes', options, async t => {
  let receivedFirst;
  const first = new Promise(resolve => { receivedFirst = resolve; });
  const { transport } = await fixture(t, async (req, res) => {
    const parts = [];
    for await (const part of req) {
      parts.push(part);
      receivedFirst();
    }
    res.end(Buffer.concat(parts));
  });
  let source;
  const body = new ReadableStream({ start(controller) { source = controller; } });
  const pending = transport.request(new Request('sentinel://app/api/v1/upload', {
    method: 'POST', body, duplex: 'half',
    headers: { 'content-type': 'application/octet-stream' },
  }));
  source.enqueue(new Uint8Array([0, 255, 128]));
  await first;
  source.enqueue(new Uint8Array([13, 10, 42]));
  source.close();
  const response = await pending;
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), Buffer.from([0, 255, 128, 13, 10, 42]));
});

test('aborting an upload cancels its body source and closes the Unix connection', options, async t => {
  let receivedFirst;
  let closed;
  let cancelled;
  const first = new Promise(resolve => { receivedFirst = resolve; });
  const connectionClosed = new Promise(resolve => { closed = resolve; });
  const sourceCancelled = new Promise(resolve => { cancelled = resolve; });
  const { transport } = await fixture(t, (req, _res) => {
    req.once('data', receivedFirst);
    req.once('close', closed);
  });
  const controller = new AbortController();
  const body = new ReadableStream({
    start(source) { source.enqueue(new Uint8Array([1, 2, 3])); },
    cancel() { cancelled(); },
  });
  const pending = transport.request(new Request('sentinel://app/api/v1/upload', {
    method: 'POST', body, duplex: 'half', signal: controller.signal,
  }));
  const rejected = assert.rejects(pending, /cancelled/);
  await first;
  controller.abort();
  await Promise.all([rejected, connectionClosed, sourceCancelled]);
});

test('aborting a streaming request closes its Unix connection', options, async t => {
  let closed;
  const { transport } = await fixture(t, (_req, res) => {
    closed = new Promise(resolve => res.once('close', resolve));
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: first\n\n');
  });
  const controller = new AbortController();
  const response = await transport.request(new Request('sentinel://app/api/v1/events', { signal: controller.signal }));
  const reader = response.body.getReader();
  await reader.read();
  const pending = reader.read();
  controller.abort();
  await assert.rejects(pending);
  await closed;
});

test('already cancelled requests reject without dispatching a backend request', options, async t => {
  let requests = 0;
  const { transport } = await fixture(t, (_req, res) => { requests++; res.end(); });
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(transport.request(new Request('sentinel://app/health', { signal: controller.signal })), /cancelled/);
  assert.equal(requests, 0);
});

test('HEAD and bodyless statuses produce valid empty responses', options, async t => {
  const { transport } = await fixture(t, (req, res) => {
    res.writeHead(req.method === 'HEAD' ? 200 : 204, { 'x-empty': 'yes' });
    res.end();
  });
  for (const method of ['HEAD', 'DELETE']) {
    const response = await transport.request(new Request('sentinel://app/api/v1/item', { method }));
    assert.equal(response.status, method === 'HEAD' ? 200 : 204);
    assert.equal(response.body, null);
    assert.equal(response.headers.get('x-empty'), 'yes');
  }
});

test('Unix WebSocket carries text, binary and close frames with the app credential', options, async t => {
  const { server, transport } = await fixture(t);
  const wss = new WebSocketServer({ noServer: true });
  t.after(() => wss.close());
  server.on('upgrade', (req, socket, head) => {
    assert.equal(req.url, '/ws/terminal?session=123');
    assert.equal(req.headers['x-sentinel-desktop-token'], 'private-test-token');
    wss.handleUpgrade(req, socket, head, ws => wss.emit('connection', ws, req));
  });
  const peerReady = once(wss, 'connection');
  const client = transport.connect('sentinel://app/ws/terminal?session=123');
  t.after(() => client.terminate());
  await once(client, 'open');
  const [peer] = await peerReady;
  for (const [data, binary] of [['terminal text', false], [Buffer.from([0, 255, 1]), true]]) {
    const received = once(peer, 'message');
    client.send(data, { binary });
    const [bytes, isBinary] = await received;
    assert.equal(isBinary, binary);
    assert.deepEqual(bytes, Buffer.from(data));
    const echoed = once(client, 'message');
    peer.send(bytes, { binary: isBinary });
    const [reply, replyBinary] = await echoed;
    assert.deepEqual(reply, Buffer.from(data));
    assert.equal(replyBinary, binary);
  }
  const clientClosed = once(client, 'close');
  const peerClosed = once(peer, 'close');
  peer.close(1000, 'done');
  const [code, reason] = await clientClosed;
  assert.equal(code, 1000);
  assert.equal(reason.toString(), 'done');
  await peerClosed;
});

test('backend routing allows supported paths and rejects UI or unrelated paths', options, async () => {
  for (const pathname of ['/api/v1/items', '/ws/terminal', '/health', '/health/ready', '/vnc/session']) {
    assert.equal(backendPath(`sentinel://app${pathname}?x=1`), `${pathname}?x=1`);
  }
  const transport = new LocalTransport('/unused.sock', 'token');
  for (const pathname of ['/', '/desktop', '/assets/app.js', '/healthcheck', '/api/../desktop']) {
    assert.throws(() => backendPath(pathname), /Unsupported backend path/);
    assert.throws(() => transport.connect(`sentinel://app${pathname}`), /Unsupported backend path/);
    await assert.rejects(transport.request(new Request(`sentinel://app${pathname}`)), /Unsupported backend path/);
  }
});
