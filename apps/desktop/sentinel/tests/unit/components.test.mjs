import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ComponentStore, validateComponents } from '../../.test-dist/main/app/components.js';

const FILES = {
  sentineld: ['bin/sentineld', 'bin/sentineld-media', 'tools/mke2fs', 'tools/resize2fs', 'tools/wimlib-imagex', 'tools/mkntfs', 'tools/hive', 'share/sentineld-agent', 'share/catalog.json'],
  'sentinel-workspace': ['bin/sentinel-workspace', 'lib/libkrun.dylib', 'lib/libvulkan_kosmickrisp.dylib', 'lib/kosmickrisp_icd.json',
    'share/firmware/SENTINEL_EFI.fd'],
};

// A release tarball as `make release` packs it: name-<sha256[:16]>.tar.gz.
async function release(dir, name, { omit, content = '' } = {}) {
  const tree = await mkdtemp(path.join(dir, `${name}-tree-`));
  for (const file of FILES[name].filter(file => file !== omit)) {
    await mkdir(path.dirname(path.join(tree, file)), { recursive: true });
    await writeFile(path.join(tree, file), `${file}${content}`);
  }
  const packed = path.join(dir, `${name}.tar.gz`);
  execFileSync('/usr/bin/tar', ['-czf', packed, '-C', tree, '.']);
  const sha256 = createHash('sha256').update(await readFile(packed)).digest('hex');
  const file = `${name}-${sha256.slice(0, 16)}.tar.gz`;
  await copyFile(packed, path.join(dir, file));
  return { file, sha256 };
}

async function setup() {
  const dir = await mkdtemp(path.join(tmpdir(), 'components-'));
  const components = { sentineld: await release(dir, 'sentineld'), 'sentinel-workspace': await release(dir, 'sentinel-workspace') };
  const fetched = [];
  const fetch = async (name, entry, destination) => { fetched.push(name); await copyFile(path.join(dir, entry.file), destination); };
  return { dir, components, fetched, fetch, store: new ComponentStore(path.join(dir, 'store')) };
}

test('installs the release\'s components, then downloads only what changed', async () => {
  const { dir, components, fetched, fetch, store } = await setup();
  try {
    assert.equal(await store.active(), null);
    assert.equal(await store.install(components, fetch), true);
    const active = await store.active();
    assert.ok(existsSync(path.join(active.sentineld, 'bin/sentineld')));
    assert.ok(existsSync(path.join(active.workspace, 'share/firmware/SENTINEL_EFI.fd')));
    // Same release again: nothing fetched, nothing changed.
    assert.equal(await store.install(components, fetch), false);
    assert.deepEqual(fetched, ['sentineld', 'sentinel-workspace']);
    // A new sentineld only.
    const next = { ...components, sentineld: await release(dir, 'sentineld', { content: ' v2' }) };
    assert.equal(await store.install(next, fetch), true);
    assert.deepEqual(fetched, ['sentineld', 'sentinel-workspace', 'sentineld']);
    const updated = await store.active();
    assert.notEqual(updated.sentineld, active.sentineld);
    assert.equal(updated.workspace, active.workspace);
    // The previous sentineld stays until pruned.
    assert.ok(existsSync(active.sentineld));
    await store.prune(updated);
    assert.ok(!existsSync(active.sentineld));
    assert.ok(existsSync(updated.sentineld) && existsSync(updated.workspace));
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('a download that does not match the index is never installed', async () => {
  const { dir, components, store } = await setup();
  try {
    const tampered = async (_name, entry, destination) => { await writeFile(destination, 'not the release'); };
    await assert.rejects(store.install(components, tampered), /does not match the release/);
    assert.equal(await store.active(), null);
    assert.deepEqual((await readdir(path.join(dir, 'store/sentineld'))), []);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('an incomplete component never becomes active', async () => {
  const { dir, components, fetch, store } = await setup();
  try {
    const broken = { ...components, 'sentinel-workspace': await release(dir, 'sentinel-workspace', { omit: 'lib/libkrun.dylib', content: ' x' }) };
    await assert.rejects(store.install(broken, fetch), /missing lib\/libkrun\.dylib/);
    assert.equal(await store.active(), null);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('the index shape is checked before anything is fetched', () => {
  const sha = 'a'.repeat(64);
  const good = {
    sentineld: { file: `sentineld-${sha.slice(0, 16)}.tar.gz`, sha256: sha },
    'sentinel-workspace': { file: `sentinel-workspace-${sha.slice(0, 16)}.tar.gz`, sha256: sha, url: 'https://github.com/x/y' },
  };
  assert.equal(validateComponents(good), good);
  for (const broken of [
    { ...good, sentineld: { ...good.sentineld, file: '../../sentineld-aaaaaaaaaaaaaaaa.tar.gz' } },
    { ...good, 'sentinel-workspace': { ...good['sentinel-workspace'], url: 'http://insecure/x' } },
  ]) assert.throws(() => validateComponents(broken));
});

test('pruning removes the image files of older local builds', async () => {
  const { dir, components, fetch, store } = await setup();
  try {
    assert.equal(await store.install(components, fetch), true);
    await mkdir(path.join(dir, 'store/images'), { recursive: true });
    await writeFile(path.join(dir, 'store/images/sentinel-workspace-alpine.oci.tar'), 'old image');
    await store.prune(await store.active());
    assert.ok(!existsSync(path.join(dir, 'store/images')));
  } finally { await rm(dir, { recursive: true, force: true }); }
});
