import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';

test('a damaged bundled archive fails before a guest installer is started', async () => {
  const resources = await mkdtemp('/tmp/sentinel-graphics-bundle-');
  const calls = [];
  const runtime = { events: new EventEmitter(), request: async action => { calls.push(action); return { exitCode: 1 }; } };
  try {
    await writeFile(path.join(resources, 'mesa-linux-arm64.json'), JSON.stringify({ version: 'test', sha256: 'a'.repeat(64) }));
    await writeFile(path.join(resources, 'mesa-linux-arm64.tar.xz'), 'damaged');
    const graphics = new WorkspaceGraphics(runtime, resources);
    await assert.rejects(graphics.install('test'), /checksum mismatch/);
    assert.deepEqual(calls, ['exec']);
  } finally { await rm(resources, { recursive: true, force: true }); }
});


test('graphics use native runtime ownership and survive viewer closure', async () => {
  const calls = [];
  const runtime = { isReady: true, request: async (action, values) => { calls.push([action, values]); return {}; } };
  const workspace = '11111111-1111-4111-8111-111111111111';
  const graphics = new WorkspaceGraphics(runtime, '/unused');
  await graphics.start(workspace);
  await graphics.start(workspace);
  await graphics.close();
  assert.deepEqual(calls.map(([action]) => action), ['graphics_start', 'graphics_start']);
  await graphics.stop(workspace);
  assert.deepEqual(calls.at(-1), ['graphics_stop', { workspace }]);
  await assert.rejects(graphics.start('../bad'), /UUID/);
});


test('Ubuntu and Debian choose the glibc bundle without reading Alpine archives', async () => {
  const resources = await mkdtemp('/tmp/sentinel-glibc-bundle-');
  const digest = 'b'.repeat(64);
  const calls = [];
  const runtime = { request: async (action, values) => { calls.push([action, values]); return { exitCode: 0, stdout: digest }; } };
  try {
    await writeFile(path.join(resources, 'mesa-linux-arm64-glibc.json'), JSON.stringify({ version: 'test', sha256: digest }));
    const graphics = new WorkspaceGraphics(runtime, resources);
    for (const distribution of ['ubuntu', 'debian']) await graphics.install('test', distribution);
    assert.equal(calls.length, 2);
    assert.ok(calls.every(([action]) => action === 'exec'));
  } finally { await rm(resources, { recursive: true, force: true }); }
});
