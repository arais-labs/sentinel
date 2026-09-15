import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { transformWithOxc } from 'vite';

const { code: outputText } = await transformWithOxc(
  readFileSync(new URL('../src/lib/desktop-socket.ts', import.meta.url), 'utf8'),
  'desktop-socket.ts',
);
const { DesktopSocket } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function channel() {
  const sent = [];
  let receive, id;
  globalThis.window = { sentinelDesktop: {
    onSocketEvent(callback) { receive = callback; return () => {}; },
    async socketOpen(value) { id = value; },
    socketSend(_, data) { sent.push(typeof data === 'string' ? data : [...data]); },
    socketClose() { sent.push('closed'); },
  } };
  const socket = new DesktopSocket('/test');
  receive({ id, type: 'open' });
  return { socket, sent };
}

test('send snapshots reusable binary buffers before queued delivery', async () => {
  const { socket, sent } = channel();
  const bytes = new Uint8Array([99, 0, 32, 24, 99]);
  socket.send(bytes.subarray(1, 4));
  bytes.set([2, 0, 1], 1);
  socket.send(new DataView(bytes.buffer, 1, 3));
  bytes.fill(9);
  const buffer = new Uint8Array([3, 4]).buffer;
  socket.send(buffer);
  new Uint8Array(buffer).fill(8);
  await flush();
  assert.deepEqual(sent, [[0, 32, 24], [2, 0, 1], [3, 4]]);
});

test('mixed blob, binary and text writes retain order before close', async () => {
  const { socket, sent } = channel();
  let release;
  const blob = new Blob(['hello']);
  blob.arrayBuffer = () => new Promise(resolve => { release = resolve; });
  socket.send(blob);
  const bytes = new Uint8Array([1, 2]);
  socket.send(bytes);
  socket.send('last');
  bytes.fill(7);
  socket.close();
  await flush();
  assert.deepEqual(sent, []);
  release(new Uint8Array([104, 105]).buffer);
  await flush();
  assert.deepEqual(sent, [[104, 105], [1, 2], 'last', 'closed']);
});
