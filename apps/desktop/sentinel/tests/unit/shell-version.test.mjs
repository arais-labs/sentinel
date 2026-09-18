import assert from 'node:assert/strict';
import test from 'node:test';
import { assertShellCompatible, compareVersions, releasePageUrl, shellUpdateFor } from '../../.test-dist/main/app/version.js';

test('versions compare numerically and tolerate a leading v', () => {
  assert.equal(compareVersions('2.3.0', '2.3.0'), 0);
  assert.equal(compareVersions('2.2.9', '2.3.0'), -1);
  assert.equal(compareVersions('2.10.0', '2.9.0'), 1);
  assert.equal(compareVersions('v2.3.0', '2.3.0'), 0);
  assert.equal(compareVersions('2.3.0-beta', '2.3.0'), 0);
  assert.throws(() => compareVersions('latest', '2.3.0'), /Not a version/);
});

const index = { channel: 'stable', version: '2.5.0', commit: 'abcdef0123456789', minShellVersion: '2.4.0' };

test('an index without a shell requirement never asks for a shell update', () => {
  assert.equal(shellUpdateFor({ channel: 'stable', version: '2.5.0', commit: 'abcdef0' }, '2.2.0'), null);
});

test('a shell at or above the requirement installs; an older shell is pointed at the release page', () => {
  assert.equal(shellUpdateFor(index, '2.4.0'), null);
  assert.equal(shellUpdateFor(index, '2.6.1'), null);
  assert.deepEqual(shellUpdateFor(index, '2.3.0'), {
    version: '2.5.0', minShellVersion: '2.4.0', url: 'https://github.com/arais-labs/sentinel/releases/tag/stable-2.5.0-abcdef0',
  });
  assert.equal(releasePageUrl('beta', '2.5.0', 'abcdef0123456789'), 'https://github.com/arais-labs/sentinel/releases/tag/beta-2.5.0-abcdef0');
});

test('applying a payload that needs a newer shell is refused with the required version', () => {
  assertShellCompatible({ version: '2.5.0', shellUpdate: null });
  assert.throws(
    () => assertShellCompatible({ version: '2.5.0', shellUpdate: shellUpdateFor(index, '2.3.0') }),
    /needs app version 2\.4\.0 or newer/,
  );
});
