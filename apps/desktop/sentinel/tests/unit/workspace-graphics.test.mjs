import assert from 'node:assert/strict';
import test from 'node:test';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';
import { validateDesktop } from '../../.test-dist/main/workspace/workspaceDesktop.js';

test('desktop validation preserves every supported desktop selection', () => {
  for (const desktop of ['none', 'xfce', 'weston', 'lxqt', 'gnome', 'plasma']) assert.doesNotThrow(() => validateDesktop(desktop));
  for (const desktop of ['', 'wayland', 'LXQt', 'unknown']) assert.throws(() => validateDesktop(desktop), /Unknown workspace desktop/);
});

test('normal desktop selections are forwarded unchanged to the worker', async () => {
  const calls = [];
  const graphics = new WorkspaceGraphics({ request: async (...args) => { calls.push(args); } });
  for (const desktop of ['xfce', 'lxqt', 'gnome', 'plasma']) await graphics.install('workspace-id', desktop, 'alpine');
  assert.deepEqual(calls, ['xfce', 'lxqt', 'gnome', 'plasma'].map(desktop => ['graphics_install', { workspace: 'workspace-id', desktop, distribution: 'alpine' }, 1_200_000]));
});

test('desktop installation runs on the worker with explicit desktop and distribution', async () => {
  const calls = [];
  const graphics = new WorkspaceGraphics({ request: async (...args) => { calls.push(args); } });
  for (const distribution of ['alpine', 'ubuntu', 'debian']) {
    await graphics.install('workspace-id', 'weston', distribution);
  }
  assert.deepEqual(calls, ['alpine', 'ubuntu', 'debian'].map(distribution =>
    ['graphics_install', { workspace: 'workspace-id', desktop: 'weston', distribution }, 1_200_000]));
});

test('worker installation failures propagate without a client-side fallback', async () => {
  const failure = new Error('Bundled desktop graphics checksum mismatch');
  const graphics = new WorkspaceGraphics({ request: async () => { throw failure; } });
  await assert.rejects(graphics.install('workspace-id', 'xfce'), error => error === failure);
  assert.equal(graphics.start, undefined);
  assert.equal(graphics.stop, undefined);
});
