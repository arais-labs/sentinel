import assert from 'node:assert/strict';
import test from 'node:test';
import { runtimePresentation } from '../src/lib/runtime-status.ts';

const installed = { installed: true, host_key_changed: false, installed_version: 'old', available_version: 'new' };
test('runtime actions reflect verified installation state, not SSH readiness', () => {
  assert.equal(runtimePresentation().busy, true);
  assert.equal(runtimePresentation(undefined, true).action, 'Check runtime');
  assert.equal(runtimePresentation({ ...installed, installed: false }).action, 'Install runtime');
  assert.equal(runtimePresentation(installed).action, 'Update runtime');
  assert.equal(runtimePresentation({ ...installed, installed_version: 'new' }).action, 'Verify / repair…');
  assert.equal(runtimePresentation({ ...installed, installed_version: 'new' }).label, 'Up to date');
  assert.equal(runtimePresentation({ ...installed, available_version: undefined }).action, 'Check runtime');
  assert.equal(runtimePresentation({ ...installed, update_phase: 'activating' }).action, 'Recover runtime');
  assert.equal(runtimePresentation({ ...installed, update_phase: 'activating', progress: 'Initializing' }).action, undefined);
  assert.equal(runtimePresentation({ ...installed, host_key_changed: true }).action, 'Review identity');
});
