import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, open, readFile, rm, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { createConnection } from 'node:net';
import { once } from 'node:events';

test('native update lease survives staging, excludes competitors, and hands off storage ownership', { skip: process.platform !== 'darwin', timeout: 60000 }, async () => {
  const root = await mkdtemp('/tmp/sentinel-update-');
  const children = [];
  let client;
  let servicePid;
  const run = args => {
    const child = spawn(root + '/releases/new/helper', args);
    children.push(child);
    const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
    return { child, next: async () => JSON.parse((await lines.next()).value),
      send: async value => { child.stdin.write(JSON.stringify(value) + '\n'); return JSON.parse((await lines.next()).value); } };
  };
  try {
    await mkdir(root + '/releases/new', { recursive: true });
    execFileSync('swiftc', ['native/macos/Sources/WorkspaceRuntime/RemoteControl.swift', 'native/macos/Sources/WorkspaceRuntime/RuntimeUpdate.swift', 'native/macos/Sources/WorkspaceRuntime/WorkerWorkspaceModels.swift', 'native/macos/Sources/WorkspaceRuntime/Migrations/RuntimeMigrations.swift', 'native/macos/Sources/WorkspaceRuntime/Migrations/M001WorkerOwnership.swift', 'native/macos/Sources/WorkspaceRuntime/Migrations/M002VirtualDesktop.swift', 'tests/fixtures/RuntimeUpdateHarness.swift', '-o', root + '/releases/new/helper']);
    await writeFile(root + '/workspace-data', 'preserve me');
    const workspace = randomUUID();
    await mkdir(`${root}/store/containers/${workspace}`, { recursive: true });
    await writeFile(`${root}/store/containers/${workspace}/rootfs.ext4`, 'keep');
    const disk = await open(`${root}/store/containers/${workspace}/rootfs.ext4`, 'r+');
    await disk.truncate(8 * 1024 ** 3); await disk.close();
    const inputs = { '001_worker_ownership': { workspaces: { [workspace]: { revision: 1, reconfigure: false, grow_disk: false,
      spec: { name: 'Existing workspace', project: root, distribution: 'alpine', tools: [],
        resources: { cpus: 2, memory_gib: 2, disk_gib: 8 }, steps: [{ message: 'Prepare', arguments: ['true'], timeout: 10 }] },
    } } } };
    const abandoned = run(['--short-lease', root]);
    assert.equal((await abandoned.next()).event, 'locked');
    await once(abandoned.child, 'exit'); // No EOF or heartbeat: lease must expire.
    const lease = run(['--update-session', root]);
    assert.equal((await lease.next()).event, 'locked');
    const competitor = run(['--update-session', root]);
    assert.match((await competitor.next()).error, /busy/);
    assert.equal((await lease.send({ action: 'inspect' })).owner_free, true);
    const legacy = spawn(root + '/releases/new/helper', ['--legacy-owner', root]);
    children.push(legacy);
    await once(legacy.stdout, 'data');
    assert.equal((await lease.send({ action: 'inspect' })).owner_free, false);
    const migrations = await lease.send({ action: 'migration_status' });
    assert.deepEqual(migrations.target, ['001_worker_ownership', '002_virtual_desktop']);
    assert.deepEqual(migrations.inputs, ['001_worker_ownership']);
    assert.match((await lease.send({ action: 'migration_plan', inputs: {} })).error, /registrations/);
    assert.equal((await lease.send({ action: 'migration_plan', inputs })).ok, true);
    assert.match((await lease.send({ action: 'migrate', inputs })).error, /busy/);
    const manifest = { version: 'new', updateProtocol: 1, storeVersion: 1, executable: root + '/releases/new/helper', kernel: root + '/releases/new/kernel', initImage: 'init' };
    assert.match((await lease.send({ action: 'activate', manifest })).error, /busy/);
    await assert.rejects(readFile(root + '/manifest.json'), { code: 'ENOENT' });
    const legacyExit = once(legacy, 'exit'); legacy.stdin.end(); await legacyExit;
    assert.equal((await lease.send({ action: 'migrate', inputs })).ok, true);
    const catalog = JSON.parse(await readFile(root + '/workspaces.json', 'utf8'));
    assert.equal(catalog.workspaces[workspace].spec.name, 'Existing workspace');
    assert.equal((await lease.send({ action: 'migrate', inputs: {} })).ok, true);
    assert.equal(JSON.parse(await readFile(root + '/workspaces.json', 'utf8')).worker_id, catalog.worker_id);
    assert.match((await lease.send({ action: 'activate', manifest })).error, /activation refused/);
    manifest.runtimeMigrations = migrations.target;
    await lease.send({ action: 'journal', journal: { phase: 'activating', target: manifest } });
    const activated = await lease.send({ action: 'activate', manifest });
    assert.equal(activated.ok, true);
    servicePid = activated.pid;
    assert.equal((await lease.send({ action: 'inspect' })).owner_free, false);
    // Simulate the desktop disappearing immediately after activation.
    const exit = once(lease.child, 'exit'); lease.child.kill('SIGKILL'); await exit;
    const recovery = run(['--update-session', root]);
    assert.equal((await recovery.next()).event, 'locked');
    const state = await recovery.send({ action: 'inspect' });
    assert.equal(state.owner_free, false, 'service must keep the inherited lock after updater exit');
    assert.equal(state.manifest.version, 'new');
    assert.equal(state.journal.phase, 'activating');
    for (let attempt = 0; attempt < 100; attempt++) {
      try { await readFile(root + '/manifest.json'); client = createConnection(root + '/control.sock'); await once(client, 'connect'); break; }
      catch { client?.destroy(); await new Promise(resolve => setTimeout(resolve, 10)); }
    }
    const lines = createInterface({ input: client })[Symbol.asyncIterator]();
    assert.equal(JSON.parse((await lines.next()).value).event, 'ready');
    assert.match((await recovery.send({ action: 'activate', manifest })).error, /busy/);
    client.write('{"id":"stop","action":"maintenance"}\n');
    assert.equal(JSON.parse((await lines.next()).value).id, 'stop');
    await lines.next(); // EOF, followed by ownership release.
    for (let attempt = 0; attempt < 100; attempt++) {
      if ((await recovery.send({ action: 'inspect' })).owner_free) break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.equal((await recovery.send({ action: 'inspect' })).owner_free, true);
    servicePid = undefined;
    assert.equal(await readFile(root + '/workspace-data', 'utf8'), 'preserve me');
  } finally {
    if (servicePid) { try { process.kill(servicePid, 'SIGTERM'); } catch {} }
    client?.destroy();
    for (const child of children) {
      if (child.exitCode === null && child.signalCode === null) { const exited = once(child, 'exit'); child.kill(); await exited; }
    }
    await rm(root, { recursive: true, force: true });
  }
});
