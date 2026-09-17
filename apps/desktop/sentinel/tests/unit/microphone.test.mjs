import assert from 'node:assert/strict';
import test from 'node:test';
import { createMicrophoneAccess } from '../../.test-dist/main/app/microphone.js';

test('first macOS microphone request prompts once, then uses the saved grant', async () => {
  let state = 'not-determined', asks = 0, resolve;
  const access = createMicrophoneAccess({ platform: 'darwin', appName: 'Sentinel',
    getStatus: () => state, ask: () => { asks++; return new Promise(done => { resolve = done; }); }, openExternal: async () => {} });
  const first = access.request(), second = access.request();
  assert.equal(asks, 1);
  state = 'granted'; resolve(true);
  assert.equal((await first).status, 'granted');
  assert.equal((await second).status, 'granted');
  assert.equal((await access.request()).status, 'granted');
  assert.equal(asks, 1);
});

test('denied and restricted access do not repeatedly prompt; settings URL is fixed', async () => {
  for (const state of ['denied', 'restricted']) {
    const opened = [];
    const access = createMicrophoneAccess({ platform: 'darwin', appName: 'Electron', getStatus: () => state,
      ask: async () => assert.fail('Must not prompt after denial'), openExternal: async url => { opened.push(url); } });
    assert.equal((await access.request()).status, state);
    await access.openSettings();
    assert.deepEqual(opened, ['x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone']);
  }
});

test('non-macOS platforms never invoke macOS prompt APIs', async () => {
  for (const platform of ['win32', 'linux']) {
    const access = createMicrophoneAccess({ platform, appName: 'Sentinel', getStatus: () => 'granted',
      ask: async () => assert.fail('macOS only'), openExternal: async () => {} });
    assert.equal((await access.request()).status, platform === 'linux' ? 'unknown' : 'granted');
  }
});
