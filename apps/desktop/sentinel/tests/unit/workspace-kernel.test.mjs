import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { mkdtemp, writeFile, readFile, rm, mkdir, access } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { zstdCompressSync } from 'node:zlib';
import { WorkspaceKernel } from '../../.test-dist/main/workspace/workspaceKernel.js';
const sha = value => createHash('sha256').update(value).digest('hex');
async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'sentinel-kernel-test-'));
  const kernel = Buffer.from('test kernel');
  await writeFile(path.join(root, 'vmlinux'), kernel);
  execFileSync('/usr/bin/tar', ['-cf', path.join(root, 'archive'), '-C', root, 'vmlinux']);
  const archive = zstdCompressSync(await readFile(path.join(root, 'archive')));
  const manifest = { kernelUrl: 'https://example.invalid/kernel', kernelSha256: sha(archive), kernelFileSha256: sha(kernel), kernelArchivePath: 'vmlinux' };
  const originalFetch = globalThis.fetch;
  t.after(async () => { globalThis.fetch = originalFetch; await rm(root, { recursive: true, force: true }); });
  return { root, kernel, archive, manifest };
}
test('kernel download is shared, verified and reused across app launches', async t => {
  const { root, kernel, archive, manifest } = await fixture(t);
  const originalPath = process.env.PATH;
  process.env.PATH = '/usr/bin:/bin:/usr/sbin:/sbin';
  t.after(() => { process.env.PATH = originalPath; });
  let requests = 0;
  globalThis.fetch = async () => { requests++; return new Response(archive); };
  const asset = new WorkspaceKernel(root, manifest, () => {});
  await Promise.all([asset.ensure(), asset.ensure()]);
  assert.equal(requests, 1);
  assert.deepEqual(await readFile(asset.file), kernel);
  await new WorkspaceKernel(root, manifest, () => {}).ensure();
  assert.equal(requests, 1);
  await assert.rejects(access(path.join(path.dirname(asset.file), 'download.partial')));
});
test('invalid Zstandard data leaves no partial kernel and can be retried', async t => {
  const { root, archive, kernel, manifest } = await fixture(t);
  const invalid = Buffer.from('not a Zstandard stream');
  const invalidManifest = { ...manifest, kernelSha256: sha(invalid) };
  const asset = new WorkspaceKernel(root, invalidManifest, () => {});
  globalThis.fetch = async () => new Response(invalid);
  await assert.rejects(asset.ensure());
  for (const file of ['kernel', 'kernel.partial', 'download.partial']) {
    await assert.rejects(access(path.join(path.dirname(asset.file), file)));
  }
  globalThis.fetch = async () => new Response(archive);
  await new WorkspaceKernel(root, manifest, () => {}).ensure();
  assert.deepEqual(await readFile(asset.file), kernel);
});
test('corrupt downloads are rejected and retry replaces a corrupt cached kernel', async t => {
  const { root, archive, kernel, manifest } = await fixture(t);
  const asset = new WorkspaceKernel(root, manifest, () => {});
  await mkdir(path.dirname(asset.file), { recursive: true });
  await writeFile(asset.file, 'corrupt cache');
  globalThis.fetch = async () => new Response('bad archive');
  await assert.rejects(asset.ensure(), /checksum/);
  globalThis.fetch = async () => new Response(archive);
  await asset.ensure();
  assert.deepEqual(await readFile(asset.file), kernel);
});
test('shutdown cancels a download and a subsequent start can retry', async t => {
  const { root, archive, manifest } = await fixture(t);
  let began;
  const fetching = new Promise(resolve => { began = resolve; });
  globalThis.fetch = async (_, { signal }) => { began(); return new Promise((_, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true })); };
  const asset = new WorkspaceKernel(root, manifest, () => {});
  const pending = asset.ensure();
  const rejected = assert.rejects(pending, /abort/i);
  await fetching;
  await asset.cancel();
  await rejected;
  globalThis.fetch = async () => new Response(archive);
  await asset.ensure();
});
