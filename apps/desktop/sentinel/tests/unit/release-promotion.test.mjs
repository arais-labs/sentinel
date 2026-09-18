import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isSuccessfulCandidateRun,
  promoteIndex,
  promoteManifest,
} from '../../scripts/publishing/promote-release.mjs';
import { obsoletePointerAssets } from '../../scripts/publishing/publish-release.mjs';

test('release promotion changes only target metadata and removes the source URL', () => {
  const source = {
    schema: 1,
    channel: 'beta',
    version: '2.3.3',
    commit: 'source',
    file: 'sentinel-payload-beta-2.3.3.tar.gz',
    sha256: 'old-hash',
    url: 'https://example.invalid/source',
    alembicHeads: { manager: ['m'], instance: ['i'] },
  };
  const index = promoteIndex(source, 'stable', 'target', 'sentinel-payload-stable-2.3.3.tar.gz', 'new-hash');
  const { url: _sourceUrl, ...sourceWithoutUrl } = source;
  assert.deepEqual(index, {
    ...sourceWithoutUrl,
    channel: 'stable',
    commit: 'target',
    file: 'sentinel-payload-stable-2.3.3.tar.gz',
    sha256: 'new-hash',
  });
  assert.equal(Object.hasOwn(index, 'url'), false);
  assert.equal(source.channel, 'beta', 'source metadata remains immutable');

  const manifest = promoteManifest(source, 'stable', 'target', '2026-09-18T00:00:00.000Z');
  assert.equal(manifest.channel, 'stable');
  assert.equal(manifest.commit, 'target');
  assert.equal(manifest.builtAt, '2026-09-18T00:00:00.000Z');
});

test('pointer cleanup retains only the current index and installer', () => {
  const keep = new Set(['latest-stable.json', 'Sentinel-2.3.3-arm64.dmg']);
  assert.deepEqual(
    obsoletePointerAssets([
      'latest-stable.json',
      'Sentinel-2.2.0-arm64.dmg',
      'Sentinel-2.3.2-arm64.dmg',
      'Sentinel-2.3.3-arm64.dmg',
    ], keep),
    ['Sentinel-2.2.0-arm64.dmg', 'Sentinel-2.3.2-arm64.dmg'],
  );
});

test('beta promotion authenticates the successful PR run by its real head', () => {
  const run = {
    headSha: 'candidate-head',
    event: 'pull_request',
    status: 'completed',
    conclusion: 'success',
  };
  assert.equal(isSuccessfulCandidateRun(run, 'candidate-head'), true);
  assert.equal(isSuccessfulCandidateRun(run, 'different-head'), false);
  assert.equal(isSuccessfulCandidateRun({ ...run, conclusion: 'failure' }, 'candidate-head'), false);
});
