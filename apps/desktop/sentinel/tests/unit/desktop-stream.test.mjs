import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import test from 'node:test';
import { DesktopStream } from '../../.test-dist/main/transport/desktopStream.js';
import { LocalTransport } from '../../.test-dist/main/transport/localTransport.js';

test('desktop route resolves through authenticated backend then carries raw binary in both directions', { timeout: 5000 }, async t => {
  const dir = await mkdtemp('/tmp/desktop-stream-');
  const path = dir + '/stream.sock';
  const server = createServer(socket => { socket.write('RFB 003.008\n'); socket.pipe(socket); });
  t.after(async () => { server.close(); await rm(dir, { recursive: true, force: true }); });
  server.listen(path); await once(server, 'listening');
  const transport = new LocalTransport('/unused', 'test');
  const route = '/api/v1/instances/test/runtime/live-view/00000000-0000-0000-0000-000000000001/stream';
  transport.request = async request => {
    assert.equal(request.method, 'POST');
    assert.equal(new URL(request.url).pathname, route);
    return Response.json({ socket: path });
  };
  const client = transport.connect(route);
  assert.ok(client instanceof DesktopStream);
  t.after(() => client.terminate());
  const greeting = once(client, 'message');
  await once(client, 'open');
  assert.equal((await greeting)[0].toString(), 'RFB 003.008\n');
  const payload = Buffer.from(Array.from({length: 256 * 1024}, (_, i) => i % 251));
  const chunks = [];
  const echoed = new Promise(resolve => {
    let length = 0;
    client.on('message', (data, binary) => { assert.equal(binary, true); chunks.push(data); length += data.length; if (length === payload.length) resolve(); });
  });
  client.send(payload); await echoed;
  assert.deepEqual(Buffer.concat(chunks), payload);
  const closed = once(client, 'close');client.close();await closed;
});

test('closing during backend resolution cancels setup and never opens a late stream', async () => {
  let resolve,signal;
  const client = new DesktopStream(s => { signal=s;return new Promise(r => resolve=r); });
  let opens=0,closes=0;
  client.on('open',()=>opens++);client.on('close',()=>closes++);
  client.close();resolve('/tmp/should-not-be-opened.sock');
  await new Promise(r=>setImmediate(r));
  assert.equal(signal.aborted,true);assert.equal(opens,0);assert.equal(closes,1);assert.equal(client.readyState,3);
});

test('failed authorization never attempts a socket and produces a single close', async () => {
  const transport = new LocalTransport('/unused', 'test');
  transport.request = async () => new Response(null,{status:404});
  const client = transport.connect('/api/v1/instances/test/runtime/live-view/00000000-0000-0000-0000-000000000001/stream');
  let errors=0;client.on('error',()=>errors++);
  const closed = await new Promise(resolve=>client.once('close',(...args)=>resolve(args)));
  assert.equal(errors,1);assert.equal(closed[0],1006);assert.equal(client.readyState,3);
});
