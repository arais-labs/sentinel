import assert from 'node:assert/strict';
import test from 'node:test';
import { workspaceRuntimeManifest } from '../../scripts/packaging/platforms/macos-arm64.mjs';

test('published native runtime metadata excludes compiler-only containers', () => {
  const config = {
    initImage: 'init@digest', kernelSha256: 'kernel-source',
    buildImage: 'docker@build-digest', glibcBuildImage: 'ubuntu@compiler-digest',
  };
  const images = { schema: 1, distributions: { alpine: 'native-init@digest' } };
  assert.deepEqual(workspaceRuntimeManifest(config, { kernelFileSha256: 'kernel' }, images), {
    protocol: 1, initImage: 'init@digest', kernelSha256: 'kernel-source',
    kernelFileSha256: 'kernel', workspaceImages: images,
  });
  assert.equal(config.buildImage, 'docker@build-digest');
});
