import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EventEmitter } from 'node:events';
import { mkdtemp, mkdir, writeFile, readFile, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { WorkspaceLifecycle } from '../../.test-dist/main/workspace/workspaceLifecycle.js';

function fake() {
  const gate = Promise.withResolvers();
  void gate.promise.catch(() => {});
  const calls = [];
  const runtime = {
    events: new EventEmitter(), isReady: false, preparationMessage: 'Downloading workspace image…',
    start: async () => { calls.push('boot'); await gate.promise; runtime.isReady = true; },
    request: async (action, value) => { calls.push([action, value]); return { exitCode: 0, distributions: ['alpine', 'ubuntu', 'debian'] }; },
    stop: async () => { gate.reject(new Error('stopped')); runtime.isReady = false; },
  };
  return { runtime, gate, calls };
}
async function until(check) {
  for (let i = 0; i < 200; i++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail('Lifecycle did not reach expected state');
}
async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'workspace-lifecycle-'));
  const f = fake();
  const lifecycle = new WorkspaceLifecycle(f.runtime, root);
  t.after(async () => { await lifecycle.close(); await rm(root, { recursive: true, force: true }); });
  await lifecycle.load();
  return { ...f, root, lifecycle, id: randomUUID() };
}

test('opening services and polling status do not boot or download; creation returns while preparing', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  assert.deepEqual(lifecycle.status().states, {});
  assert.deepEqual(calls, []);
  await lifecycle.prepare(id, '/project', ['node']);
  await lifecycle.prepare(id, '/project', ['node']);
  assert.equal(calls.length, 1);
  assert.equal(lifecycle.status().states[id].state, 'preparing');
  assert.match(lifecycle.status().states[id].message, /Downloading/);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.equal(calls.filter(value => value[0] === 'start').length, 1);
});

test('cancel during image download never creates a late container', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  await lifecycle.prepare(id, '/project', []);
  await lifecycle.stop(id);
  gate.resolve();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(lifecycle.status().states[id].state, 'stopped');
  assert.equal(calls.filter(value => value[0] === 'start').length, 0);
});

test('retry after cancellation ignores the superseded setup job', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  await lifecycle.prepare(id, '/project', ['node']);
  await lifecycle.stop(id);
  await lifecycle.prepare(id, '/project', ['node']);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.equal(calls.filter(value => value[0] === 'start').length, 1);
});

test('failed persistence does not leave a phantom preparation or start a helper', async t => {
  const { lifecycle, root, calls, gate, id } = await fixture(t);
  const temporaryManifest = path.join(root, 'workspaces.json.next');
  await mkdir(temporaryManifest);
  await assert.rejects(lifecycle.prepare(id, '/project', []));
  assert.equal(lifecycle.status().states[id], undefined);
  assert.deepEqual(calls, []);
  await rm(temporaryManifest, { recursive: true });
  await lifecycle.prepare(id, '/project', []);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
});

test('failed setup is retryable and runtime crashes preserve registration without replaying commands', async t => {
  const { lifecycle, runtime, gate, id } = await fixture(t);
  const real = runtime.request;
  runtime.request = async () => { throw new Error('network unavailable'); };
  await lifecycle.prepare(id, '/project', []);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'failed');
  assert.match(lifecycle.status().states[id].error, /network unavailable/);
  runtime.request = real;
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'running');
  runtime.isReady = false;
  runtime.events.emit('closed');
  assert.equal(lifecycle.status().states[id].state, 'failed');
});

test('unfinished preparation resumes after app restart but completed workspaces remain stopped', async t => {
  const { lifecycle, root, id, gate } = await fixture(t);
  await lifecycle.prepare(id, '/project', ['node']);
  const stored = JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'));
  assert.equal(stored[id].state, 'preparing');
  await lifecycle.close();
  const next = fake();
  const restored = new WorkspaceLifecycle(next.runtime, root);
  t.after(() => restored.close());
  await restored.load();
  assert.equal(restored.status().states[id].state, 'preparing');
  next.gate.resolve();
  await until(() => restored.status().states[id].state === 'running');
  await restored.close();
  const third = fake();
  const dormant = new WorkspaceLifecycle(third.runtime, root);
  await dormant.load();
  assert.equal(dormant.status().states[id].state, 'stopped');
  assert.deepEqual(third.calls, []);
  await dormant.close();
  gate.resolve();
});

test('removing a dormant workspace deletes only its private disk without downloading', async t => {
  const { lifecycle, root, calls, id } = await fixture(t);
  const disk = path.join(root, 'store/containers', id);
  const project = path.join(root, 'project');
  await mkdir(disk, { recursive: true });
  await mkdir(project);
  await writeFile(path.join(disk, 'rootfs.ext4'), 'private');
  await writeFile(path.join(project, 'keep'), 'user file');
  await lifecycle.stop(id, true);
  await assert.rejects(access(disk));
  assert.equal(await readFile(path.join(project, 'keep'), 'utf8'), 'user file');
  assert.deepEqual(calls, []);
  await assert.rejects(lifecycle.stop('../project', true), /UUID/);
});

test('adding tools reuses the workspace and reports the same resources passed to the VM', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  await lifecycle.prepare(id, '/project', ['git']);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  await lifecycle.prepare(id, '/project', ['git', 'node']);
  await until(() => lifecycle.status().states[id].state === 'running');
  const starts = calls.filter(value => value[0] === 'start');
  assert.equal(starts.length, 2);
  assert.equal(calls.filter(value => value[0] === 'delete' || value[0] === 'stop').length, 0);
  assert.equal(starts[1][1].workspace, id);
  assert.deepEqual(lifecycle.status().states[id].resources, { cpus: 2, memory_gib: 2, disk_gib: 32 });
  assert.equal(starts[1][1].disk_gib, 32);
  assert.ok(calls.some(value => value[0] === 'exec' && value[1].arguments.includes('nodejs')));
  await assert.rejects(lifecycle.prepare(id, '/project', ['git']), /kept/);
});

test('Desktop provisioning waits for Metal graphics and never marks a failed renderer ready', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'workspace-metal-'));
  const f = fake();
  const graphicsGate = Promise.withResolvers();
  const graphics = { install: async () => graphicsGate.promise, stop: async () => {}, close: async () => {} };
  const lifecycle = new WorkspaceLifecycle(f.runtime, root, graphics);
  t.after(async () => { await lifecycle.close(); await rm(root, { recursive: true, force: true }); });
  await lifecycle.load();
  const id = randomUUID();
  await lifecycle.prepare(id, '/project', ['desktop']); f.gate.resolve();
  await until(() => lifecycle.status().states[id].message === 'Preparing Metal desktop graphics…');
  assert.equal(lifecycle.status().states[id].state, 'preparing');
  graphicsGate.reject(new Error('graphics install failed'));
  await until(() => lifecycle.status().states[id].state === 'failed');
  assert.match(lifecycle.status().states[id].error, /graphics install failed/);
});

test('reinstall reapplies definitions to a running workspace without deleting its disk', async t => {
  const { lifecycle, calls, gate, id, root } = await fixture(t);
  gate.resolve();
  await lifecycle.prepare(id, '/project', ['node']);
  await until(() => lifecycle.status().states[id].state === 'running');
  const count = calls.length;
  await lifecycle.prepare(id, '/project', ['node']);
  assert.equal(calls.length, count);
  await lifecycle.prepare(id, '/project', ['node'], true);
  await until(() => lifecycle.status().states[id].state === 'running');
  const updates = calls.slice(count).filter(value => value[0] === 'exec');
  assert.match(updates[0][1].arguments[2], /apk add --no-cache --upgrade/);
  assert.equal(updates[0][1].workspace, id);
  assert.ok(!calls.some(value => ['delete', 'stop'].includes(value[0])));
  const saved = JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'))[id];
  assert.equal(saved.project, '/project');
  assert.deepEqual(saved.tools, ['node']);
});

test('an interrupted reinstall retains its upgrade intent when setup resumes', async t => {
  const { lifecycle, calls, gate, id, root } = await fixture(t);
  await writeFile(path.join(root, 'workspaces.json'), JSON.stringify({
    [id]: { project: '/project', tools: ['node'], state: 'preparing', reinstall: true },
  }));
  await lifecycle.load();
  await assert.rejects(lifecycle.prepare(id, '/project', ['node'], true), /Wait for workspace setup/);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  const installs = calls.filter(value => value[0] === 'exec');
  assert.match(installs[0][1].arguments[2], /--upgrade/);
});

test('provisioned resources persist and are used again after stop/start', async t => {
  const { lifecycle, calls, gate, root, id } = await fixture(t);
  const allocation = { cpus: 4, memory_gib: 8, disk_gib: 64 };
  await lifecycle.prepare(id, '/project', ['kind'], false, allocation);
  assert.deepEqual(JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'))[id].resources, allocation);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.deepEqual(lifecycle.status().states[id].resources, allocation);
  await lifecycle.stop(id);
  await lifecycle.prepare(id, '/project', ['kind']);
  await until(() => lifecycle.status().states[id].state === 'running');
  for (const [, value] of calls.filter(value => value[0] === 'start')) {
    for (const key of Object.keys(allocation)) assert.equal(value[key], allocation[key]);
  }
  await assert.rejects(lifecycle.prepare(id, '/project', ['kind'], false, { ...allocation, disk_gib: 32 }), /only be increased/);
});

test('invalid resource allocations never boot a workspace', async t => {
  const { lifecycle, calls, id } = await fixture(t);
  for (const value of [{ cpus: 0, memory_gib: 2, disk_gib: 32 }, { cpus: 2, memory_gib: 2.5, disk_gib: 32 }, { cpus: 2, memory_gib: 2, disk_gib: 2048 }]) {
    await assert.rejects(lifecycle.prepare(id, '/project', [], false, value), /Invalid workspace/);
  }
  assert.deepEqual(calls, []);
});


test('resizing a running workspace restarts it before applying resources and growing its disk', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  await lifecycle.prepare(id, '/project', []);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  calls.length = 0;
  await lifecycle.prepare(id, '/project', [], false, { cpus: 4, memory_gib: 4, disk_gib: 64 });
  await until(() => lifecycle.status().states[id].state === 'running');
  const operations = calls.filter(Array.isArray);
  assert.equal(operations[0][0], 'stop');
  assert.equal(operations[1][0], 'start');
  assert.equal(operations[1][1].grow_disk, true);
  assert.equal(operations[1][1].memory_gib, 4);
});

test('stopped size edits stay stopped and a failed resize retains its recovery intent', async t => {
  const { lifecycle, calls, runtime, gate, root, id } = await fixture(t);
  await lifecycle.prepare(id, '/project', []);
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  await lifecycle.stop(id);
  calls.length = 0;
  await lifecycle.prepare(id, '/project', [], false, { cpus: 4, memory_gib: 4, disk_gib: 64 });
  assert.equal(lifecycle.status().states[id].state, 'stopped');
  assert.deepEqual(calls, []);
  const real = runtime.request;
  runtime.request = async (action, values) => {
    if (action === 'start') throw new Error('resize interrupted');
    return real(action, values);
  };
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'failed');
  const saved = JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'))[id];
  assert.equal(saved.growDisk, true);
  assert.equal(saved.reconfigure, true);
  runtime.request = real;
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.ok(calls.some(value => value[0] === 'start' && value[1].grow_disk === true));
});

test('changing project remounts same workspace, preserving tools and resources', async t => {
  const { lifecycle, calls, gate, id } = await fixture(t);
  const project = await mkdtemp(path.join(tmpdir(), 'new-project-'));
  t.after(() => rm(project, { recursive: true, force: true }));
  await lifecycle.prepare(id, '/project', ['git']); gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  calls.length = 0;
  await lifecycle.prepare(id, project, ['git']);
  await until(() => lifecycle.status().states[id].state === 'running');
  const actions = calls.filter(Array.isArray);
  assert.equal(actions[0][0], 'stop');
  const start = actions.find(([action]) => action === 'start')[1];
  assert.equal(start.workspace, id); assert.equal(start.project, project);
  assert.equal(actions.some(([action]) => action === 'delete'), false);
  await lifecycle.stop(id); calls.length = 0;
  const other = await mkdtemp(path.join(tmpdir(), 'other-project-'));
  t.after(() => rm(other, { recursive: true, force: true }));
  await lifecycle.prepare(id, other, ['git']);
  assert.equal(lifecycle.status().states[id].state, 'stopped');
  assert.equal(calls.length, 0);
});

test('lifecycle publishes setup steps as they happen and preserves notification context', async t => {
  const { lifecycle, gate, id, root } = await fixture(t);
  const updates = [];
  lifecycle.events.on('changed', (workspace, entry) => updates.push({ workspace, ...entry }));
  await lifecycle.prepare(id, '/project', ['node'], false, undefined, { instanceName: 'main', name: 'Demo' });
  assert.equal(updates.at(-1).state, 'preparing');
  gate.resolve();
  await until(() => updates.at(-1)?.state === 'running');
  assert.ok(updates.some(update => update.message?.includes('Starting')));
  assert.ok(updates.filter(update => update.state === 'preparing').length > 2);
  assert.equal(updates.at(-1).notificationContext.name, 'Demo');
  const stored = JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'));
  assert.equal(stored[id].notificationContext.instanceName, 'main');
});

test('distribution survives restarts and cannot reinterpret an existing disk', async t => {
  const { lifecycle, calls, gate, id, root } = await fixture(t);
  await lifecycle.prepare(id, '/project', ['git'], false, undefined, undefined, 'ubuntu');
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.equal(calls.find(call => Array.isArray(call) && call[0] === 'start')[1].distribution, 'ubuntu');
  await lifecycle.stop(id);
  assert.equal(JSON.parse(await readFile(path.join(root, 'workspaces.json'), 'utf8'))[id].distribution, 'ubuntu');
  await assert.rejects(lifecycle.prepare(id, '/project', ['git'], false, undefined, undefined, 'debian'), /new workspace/);
  await assert.rejects(lifecycle.prepare(id, '/project', ['git', 'unknown']), /Unknown workspace tools/);
  await lifecycle.prepare(id, '/project', ['git']);
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.equal(calls.filter(call => Array.isArray(call) && call[0] === 'start').at(-1)[1].distribution, 'ubuntu');
});

test('an older runtime cannot silently create Alpine for a requested Ubuntu workspace', async t => {
  const { lifecycle, runtime, calls, gate, id } = await fixture(t);
  const request = runtime.request;
  runtime.request = async (action, value) => action === 'status' ? { states: {} } : request(action, value);
  await lifecycle.prepare(id, '/project', ['git'], false, undefined, undefined, 'ubuntu');
  gate.resolve();
  await until(() => lifecycle.status().states[id].state === 'failed');
  assert.match(lifecycle.status().states[id].error, /Update Sentinel Runtime/);
  assert.ok(!calls.some(call => Array.isArray(call) && call[0] === 'start'));
});


test('recovery eligibility comes from one guest fault, never from a host connection failure', async t => {
  const { lifecycle, runtime, calls, gate, id } = await fixture(t);
  const stopped = randomUUID();
  gate.resolve();
  for (const workspace of [id, stopped]) {
    await lifecycle.prepare(workspace, '/project', []);
    await until(() => lifecycle.status().states[workspace].state === 'running');
  }
  await lifecycle.stop(stopped);
  runtime.request = async () => { throw new Error('SSH disconnected'); };
  await assert.rejects(lifecycle.overview(), /SSH disconnected/);
  assert.equal(lifecycle.status().states[stopped].recovery_available, false);
  assert.equal(lifecycle.status().states[id].recovery_available, false);
  runtime.request = async () => ({ states: { [id]: 'failed', [stopped]: 'stopped' }, errors: { [id]: 'Guest did not respond' }, capabilities: ['workspace-recovery-v1'] });
  await lifecycle.overview();
  assert.equal(lifecycle.status().states[id].recovery_available, true);
  assert.equal(lifecycle.status().states[stopped].recovery_available, false);
  await assert.rejects(lifecycle.recover(stopped), /not been identified/);
  await assert.rejects(lifecycle.prepare(id, '/project', []), /Recover this workspace/);
});

test('recovery waits for native success, blocks competing actions, and restarts only the affected workspace', async t => {
  const { lifecycle, runtime, calls, gate, id } = await fixture(t);
  gate.resolve();
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'running');
  const original = runtime.request;
  const repair = Promise.withResolvers();
  runtime.request = async (action, values) => {
    if (action === 'status') return { states: { [id]: 'failed' }, errors: { [id]: 'hung' }, capabilities: ['workspace-recovery-v1'] };
    if (action === 'recover') { assert.equal(values.workspace, id); return repair.promise; }
    return original(action, values);
  };
  await lifecycle.overview();
  const starts = calls.filter(call => call[0] === 'start').length;
  await lifecycle.recover(id);
  assert.equal(lifecycle.status().states[id].state, 'recovering');
  await assert.rejects(lifecycle.recover(id), /already in progress/);
  await assert.rejects(lifecycle.stop(id, true), /recovery to finish/);
  assert.equal(calls.filter(call => call[0] === 'start').length, starts);
  repair.resolve({ backup: '/private/backup/rootfs.ext4' });
  await until(() => lifecycle.status().states[id].state === 'running');
  assert.equal(calls.filter(call => call[0] === 'start').length, starts + 1);
  assert.equal(lifecycle.status().states[id].recovery_backup, '/private/backup/rootfs.ext4');
  assert.equal(lifecycle.status().states[id].recovery_available, false);
});

test('failed disk repair keeps recovery available and never starts the VM', async t => {
  const { lifecycle, runtime, calls, gate, id } = await fixture(t);
  gate.resolve();
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'running');
  runtime.request = async action => {
    if (action === 'status') return { states: { [id]: 'failed' }, errors: { [id]: 'hung' }, capabilities: ['workspace-recovery-v1'] };
    throw new Error('Repair failed; backup preserved');
  };
  await lifecycle.overview();
  const starts = calls.filter(call => call[0] === 'start').length;
  await lifecycle.recover(id);
  await until(() => lifecycle.status().states[id].state === 'failed');
  assert.match(lifecycle.status().states[id].error, /backup preserved/);
  assert.equal(lifecycle.status().states[id].recovery_available, true);
  assert.equal(calls.filter(call => call[0] === 'start').length, starts);
});

test('reconnecting during native recovery follows completion without replaying workspace startup', async t => {
  const { lifecycle, runtime, calls, gate, id } = await fixture(t);
  gate.resolve();
  await lifecycle.prepare(id, '/project', []);
  await until(() => lifecycle.status().states[id].state === 'running');
  const starts = calls.filter(call => call[0] === 'start').length;
  runtime.request = async () => ({ states: { [id]: 'recovering' } });
  await lifecycle.overview();
  assert.equal(lifecycle.status().states[id].state, 'recovering');
  runtime.request = async () => ({ states: { [id]: 'stopped' } });
  await lifecycle.overview();
  assert.equal(lifecycle.status().states[id].state, 'stopped');
  assert.equal(lifecycle.status().states[id].recovery_available, false);
  assert.equal(calls.filter(call => call[0] === 'start').length, starts);
});
