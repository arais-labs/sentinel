import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { configureApplicationIdentity } from '../../.test-dist/main/app/applicationIdentity.js';

function host(root, isPackaged, ready = false) {
  const calls = [];
  const paths = { appData: root, userData: path.join(root, 'sentinel-desktop') };
  return { calls, app: {
    isPackaged, isReady: () => ready,
    getPath: key => paths[key],
    setName: name => calls.push(['name', name]),
    setPath: (key, value) => { paths[key] = value; calls.push(['path', key, value]); },
    setAppLogsPath: value => calls.push(['logs', value]),
  } };
}

test('development has a stable separate Keychain identity and retains its data location', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'sentinel-identity-test-'));
  try {
    const { app, calls } = host(root, false);
    configureApplicationIdentity(app);
    assert.deepEqual(calls, [
      ['name', 'Sentinel Dev'],
      ['path', 'userData', path.join(root, 'Sentinel Dev')],
      ['logs', path.join(root, 'Sentinel Dev', 'logs')],
    ]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('packaged identity and credential namespace remain unchanged', () => {
  const { app, calls } = host('/unused', true);
  configureApplicationIdentity(app);
  assert.deepEqual(calls, [['logs', undefined]]);
});

test('late identity changes fail before mutating paths or names', () => {
  const { app, calls } = host('/unused', false, true);
  assert.throws(() => configureApplicationIdentity(app), /before Electron is ready/);
  assert.deepEqual(calls, []);
});
