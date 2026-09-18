import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  PaneWindowBoundsStore, PaneWindowRegistry, isTrustedDesktopSender, paneWindowKey, validatePaneWindowRequest,
} from '../../.test-dist/main/app/paneWindowRegistry.js';

let nextId = 1;
function fakeWindow() {
  const frame = { id: nextId };
  const window = {
    webContents: { id: nextId++, mainFrame: frame },
    closed: false, minimized: false, focused: 0,
    close() { this.closed = true; }, focus() { this.focused++; },
    isDestroyed() { return this.closed; }, isMinimized() { return this.minimized; }, restore() { this.minimized = false; },
  };
  return window;
}
const request = { instance: 'demo', session: '11111111-1111-4111-8111-111111111111', tabId: 'terminal', paneId: 'pane-terminal-abc', title: 'Terminal' };

test('registry keys windows by webContents and finds one per instance, session and tab', () => {
  const registry = new PaneWindowRegistry();
  const first = fakeWindow(), second = fakeWindow();
  registry.add(first, request);
  registry.add(second, { ...request, tabId: 'files' });
  assert.equal(registry.size, 2);
  assert.equal(registry.get(first.webContents.id).window, first);
  assert.equal(registry.find(request).window, first);
  assert.equal(registry.find({ ...request, session: null }), undefined);
  assert.equal(paneWindowKey(request), JSON.stringify(['demo', request.session, 'terminal']));
  registry.delete(first.webContents.id);
  assert.equal(registry.find(request), undefined);
  registry.closeAll();
  assert.equal(second.closed, true);
});

test('only the main window or a registered pane window may invoke desktop IPC', () => {
  const registry = new PaneWindowRegistry();
  const pane = fakeWindow(), stranger = fakeWindow();
  registry.add(pane, request);
  const mainFrame = { main: true };
  const isSentinelUrl = url => url.startsWith('sentinel://app');
  const trusted = sender => isTrustedDesktopSender(sender, mainFrame, registry, isSentinelUrl);
  assert.equal(trusted({ id: 0, frame: mainFrame, url: 'sentinel://app/instances/demo' }), true);
  assert.equal(trusted({ id: pane.webContents.id, frame: pane.webContents.mainFrame, url: 'sentinel://app/pane?tab=terminal' }), true);
  assert.equal(trusted({ id: pane.webContents.id, frame: { other: true }, url: 'sentinel://app/pane' }), false, 'subframes of a pane window are rejected');
  assert.equal(trusted({ id: stranger.webContents.id, frame: stranger.webContents.mainFrame, url: 'sentinel://app/pane' }), false, 'unregistered windows are rejected');
  assert.equal(trusted({ id: pane.webContents.id, frame: pane.webContents.mainFrame, url: 'https://evil.test/' }), false);
  assert.equal(trusted({ id: 0, frame: undefined, url: undefined }), false);
  pane.close();
  assert.equal(trusted({ id: pane.webContents.id, frame: pane.webContents.mainFrame, url: 'sentinel://app/pane' }), false, 'destroyed windows lose trust');
});

test('requests are validated and bounds are remembered per tab', async t => {
  assert.deepEqual(validatePaneWindowRequest({ ...request, session: undefined, title: 'x'.repeat(200) }), { ...request, session: null, title: 'x'.repeat(120) });
  for (const bad of [null, {}, { ...request, tabId: '../etc' }, { ...request, instance: '' }, { ...request, session: 5 }, { ...request, paneId: '' }]) {
    assert.throws(() => validatePaneWindowRequest(bad), /Invalid pane window request/);
  }
  const root = await mkdtemp(path.join(tmpdir(), 'sentinel-pane-windows-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const store = new PaneWindowBoundsStore(path.join(root, 'pane-windows.json'));
  assert.equal(store.get('terminal'), undefined);
  store.set('terminal', { x: 10, y: 20, width: 900, height: 600 });
  store.set('files', { width: 700, height: 500 });
  assert.deepEqual(new PaneWindowBoundsStore(path.join(root, 'pane-windows.json')).get('terminal'), { x: 10, y: 20, width: 900, height: 600 });
  assert.deepEqual(store.get('files'), { width: 700, height: 500 });
});
