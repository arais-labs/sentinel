import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { workspaceImageSource } from '../../scripts/packaging/workspace-images/stage.mjs';
import { runtimeRequirements } from '../../scripts/packaging/platforms/macos-arm64.mjs';

test('image staging has no dated-path fallback and uses explicit artifact selection', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'sentinel-image-source-'));
  try {
    assert.throws(() => workspaceImageSource(root, {}), /SENTINEL_WORKSPACE_IMAGES_DIR/);
    const directory = path.join(root, 'artifacts');
    for (const name of ['alpine', 'ubuntu', 'debian']) {
      await mkdir(path.join(directory, name), { recursive: true });
      await writeFile(path.join(directory, name, 'manifest.json'), '{}');
    }
    const source = workspaceImageSource(root, { SENTINEL_WORKSPACE_IMAGES_DIR: directory });
    assert.equal(source.directory, directory);
    assert.equal(source.manifests.length, 3);
    await rm(source.manifests[0]);
    assert.throws(() => workspaceImageSource(root, { SENTINEL_WORKSPACE_IMAGES_DIR: directory }), /Missing native/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('canonical cache metadata resolves its directory relative to the metadata, not cwd', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'sentinel-image-source-'));
  try {
    const cache = path.join(root, 'build/workspace-images');
    await mkdir(path.join(cache, 'verified'), { recursive: true });
    await writeFile(path.join(cache, 'verified/manifest.json'), '{}');
    await writeFile(path.join(cache, 'current.json'), JSON.stringify({ schema: 1, directory: 'verified' }));
    assert.equal(workspaceImageSource(root, {}).directory, path.join(cache, 'verified'));
    await writeFile(path.join(cache, 'current.json'), JSON.stringify({ schema: 99, directory: 'verified' }));
    assert.throws(() => workspaceImageSource(root, {}), /Invalid native/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('runtime completeness checks require all three native image layouts', () => {
  const required = runtimeRequirements()['workspace-runtime'];
  assert.ok(required.includes('workspace-images/manifest.json'));
  for (const name of ['alpine', 'ubuntu', 'debian']) {
    assert.ok(required.includes(`workspace-images/${name}/index.json`));
    assert.ok(required.includes(`workspace-images/${name}/oci-layout`));
  }
});
