// Owns only disposable VMs and a temporary store; never touches user workspaces.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, writeFile, mkdir, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
const resources = path.resolve('build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const root = await mkdtemp('/tmp/sentinel-recovery-integration-');
const project = await mkdtemp('/tmp/sentinel-recovery-project-');
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({ command: process.env.SENTINEL_RECOVERY_HELPER || path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, path.resolve('build/graphics-sources/guest', config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: console.log, onFailure: console.error, log: console.error });
const start = () => runtime.request('start', { workspace, project, cpus: 1, memory_gib: 1, disk_gib: 2 });
try {
  console.log('Disposable store:', root);
  await writeFile(path.join(project, 'proof'), 'project preserved');
  await runtime.start(); await start();
  assert.equal((await runtime.request('exec', {workspace, arguments: ['sh', '-ec', 'printf disk-preserved >/root/proof; sync'], timeout: 10})).exitCode, 0);
  await assert.rejects(runtime.request('recover', {workspace}), /not been identified/);
  await runtime.request('stop', {workspace}); await runtime.stop();
  // Simulate a diagnosed failure persisted before the helper restarted.
  await mkdir(path.join(root, 'recovery-required'), {recursive: true});
  await writeFile(path.join(root, 'recovery-required', workspace), 'Previously diagnosed guest failure');
  await runtime.start();
  assert.equal((await runtime.request('status')).states[workspace], 'failed');
  await assert.rejects(start(), /Previously diagnosed/);
  const recovering = runtime.request('recover', {workspace});
  void recovering.catch(() => {});
  await new Promise(resolve => setTimeout(resolve, 200));
  const began = performance.now();
  assert.equal((await runtime.request('status', {}, 1000)).states[workspace], 'recovering');
  console.log('Status during recovery:', Math.round(performance.now() - began), 'ms');
  const result = await recovering;
  assert.ok(result.backup.startsWith(path.join(root, 'recovery')));
  assert.equal((await stat(result.backup)).size, (await stat(path.join(root, 'store/containers', workspace, 'rootfs.ext4'))).size);
  assert.equal((await stat(result.backup)).mode & 0o777, 0o600);
  console.log('Repair report:', await readFile(path.join(path.dirname(result.backup), 'result.txt'), 'utf8'));
  assert.equal((await runtime.request('status')).states[workspace], 'stopped');
  await start();
  assert.equal((await runtime.request('exec', {workspace, arguments: ['cat', '/root/proof'], timeout: 10})).stdout, 'disk-preserved');
  assert.equal(await readFile(path.join(project, 'proof'), 'utf8'), 'project preserved');
  await runtime.request('delete', {workspace});
  console.log('PASS: backup, offline repair, independent status, restart and preserved data');
} finally {
  await runtime.stop();
  await rm(root, {recursive: true, force: true});
  await rm(project, {recursive: true, force: true});
}
