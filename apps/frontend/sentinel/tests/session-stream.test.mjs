import assert from 'node:assert/strict';
import test from 'node:test';
import { transformWithOxc } from 'vite';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../src/lib/session-stream.ts', import.meta.url), 'utf8')
  .replace("import { DesktopSocket } from './desktop-socket';", `class DesktopSocket {
    constructor() { globalThis.testSockets.push(this); }
    close() {}
    receive(payload) { this.onmessage({ data: JSON.stringify(payload) }); }
  }`)
  .replace("import { wsSessionsBaseUrl } from './env';", "const wsSessionsBaseUrl = () => 'test://sessions';");
const { code: outputText } = await transformWithOxc(source, 'session-stream.ts');
globalThis.testSockets = [];
globalThis.window = { setTimeout, clearTimeout };
const { subscribeSessionStream, isSessionRunActive } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('late chat subscriber receives current run state, not original connection state', () => {
  const terminal = subscribeSessionStream('main', 'one', { onEvent() {} });
  const socket = testSockets.at(-1);
  socket.receive({ type: 'connected', run_active: false, history: [] });
  socket.receive({ type: 'run_state', run_active: true });
  const states = [];
  let chat = subscribeSessionStream('main', 'one', { onRunState: value => states.push(value) });
  assert.deepEqual(states, [true]);
  assert.equal(isSessionRunActive('main', 'one'), true);
  chat.unsubscribe();
  socket.receive({ type: 'run_state', run_active: false });
  chat = subscribeSessionStream('main', 'one', { onRunState: value => states.push(value) });
  assert.deepEqual(states, [true, false]);
  assert.equal(isSessionRunActive('main', 'one'), false);
  chat.unsubscribe();
  terminal.unsubscribe();
});

test('fresh connection restores running turn and receives completion', () => {
  const states = [];
  const chat = subscribeSessionStream('main', 'two', { onRunState: value => states.push(value) });
  const socket = testSockets.at(-1);
  socket.receive({ type: 'connected', run_active: true, history: [] });
  socket.receive({ type: 'text_delta', delta: 'continued' });
  socket.receive({ type: 'run_state', run_active: false });
  assert.deepEqual(states, [false, true, false]);
  chat.unsubscribe();
});

test('transport open is not Live until the session handshake arrives', () => {
  const states = [];
  const chat = subscribeSessionStream('main', 'handshake', { onState: state => states.push(state) });
  const socket = testSockets.at(-1);
  socket.onopen();
  assert.equal(chat.connection, 'connecting');
  assert.equal(states.includes('connected'), false);
  socket.receive({ type: 'connected', run_active: true, history_via_http: true });
  assert.equal(chat.connection, 'connected');
  assert.equal(isSessionRunActive('main', 'handshake'), true);
  socket.onclose();
  assert.equal(chat.connection, 'reconnecting');
  chat.unsubscribe();
});
