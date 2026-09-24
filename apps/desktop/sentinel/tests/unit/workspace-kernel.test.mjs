import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { WorkspaceKernel } from '../../.test-dist/main/workspace/workspaceKernel.js';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'sentinel-kernel-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bytes = Buffer.from('bundled kernel');
  await writeFile(path.join(root, 'kernel'), bytes);
  const manifest = { kernelFileSha256: createHash('sha256').update(bytes).digest('hex'), kernelRelease: '6.18.35-sentinel-1' };
  return { root, bytes, manifest };
}
test('bundled kernel verification is shared and never downloads a replacement', async t => {
  const { root, bytes, manifest } = await fixture(t);
  const fetch = globalThis.fetch;
  globalThis.fetch = () => assert.fail('Workspace startup must not download a kernel');
  t.after(() => { globalThis.fetch = fetch; });
  const messages = [], kernel = new WorkspaceKernel(root, manifest, message => messages.push(message));
  const first = kernel.ensure(), second = kernel.ensure();
  assert.equal(first, second);
  await first;
  await kernel.ensure();
  assert.equal(messages.length, 1);
  assert.deepEqual(await readFile(kernel.file), bytes);
});
test('corrupt or missing bundled kernels fail without replacing user files', async t => {
  const { root, bytes, manifest } = await fixture(t);
  const kernel = new WorkspaceKernel(root, manifest, () => {});
  await writeFile(kernel.file, 'damaged');
  await assert.rejects(kernel.ensure(), /checksum mismatch/);
  assert.equal(await readFile(kernel.file, 'utf8'), 'damaged');
  await rm(kernel.file);
  await assert.rejects(kernel.ensure(), /ENOENT/);
  await writeFile(kernel.file, bytes);
  await kernel.ensure();
});
test('shutdown cancels verification and a subsequent start can retry', async t => {
  const { root, manifest } = await fixture(t);
  const kernel = new WorkspaceKernel(root, manifest, () => {});
  const pending = kernel.ensure();
  const rejected = assert.rejects(pending, /abort/i);
  await kernel.cancel();
  await rejected;
  await kernel.ensure();
});
test('a runtime manifest must identify the bundled kernel and module release', async t => {
  const { root, manifest } = await fixture(t);
  assert.throws(() => new WorkspaceKernel(root, { ...manifest, kernelFileSha256: 'bad' }, () => {}), /Invalid/);
  assert.throws(() => new WorkspaceKernel(root, { ...manifest, kernelRelease: '' }, () => {}), /Invalid/);
});
