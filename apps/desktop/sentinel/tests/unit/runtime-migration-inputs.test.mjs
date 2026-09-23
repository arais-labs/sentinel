import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { runtimeMigrationInputs } from '../../.test-dist/main/workspace/migrations/runtimeMigrationInputs.js';

test('migration exports existing IDs, resources and manifest-only registrations without changing the source', async () => {
  const root = await mkdtemp('/tmp/sentinel-migration-inputs-');
  try {
    const machine = randomUUID(), id = randomUUID(), orphan = randomUUID();
    await mkdir(`${root}/remotes/${machine}`, { recursive: true });
    const entry = { project: '/project', tools: ['git'], distribution: 'ubuntu', resources: { cpus: 4, memory_gib: 8, disk_gib: 64 }, state: 'stopped' };
    const file = `${root}/remotes/${machine}/workspaces.json`;
    const source = JSON.stringify({ [id]: entry, [orphan]: { ...entry, notificationContext: { name: 'Historical project' } } });
    await writeFile(file, source);
    const reference = { id, name: 'Project', project: '/project', tools: ['git'], distribution: 'ubuntu' };
    const inputs = await runtimeMigrationInputs(root, machine, [reference]);
    const records = inputs['001_worker_ownership'].workspaces;
    assert.deepEqual(Object.keys(records).sort(), [id, orphan].sort());
    assert.deepEqual(records[id].spec.resources, entry.resources);
    assert.equal(records[orphan].spec.name, 'Historical project');
    assert.ok(records[id].spec.steps.length);
    assert.equal(await readFile(file, 'utf8'), source);
    await assert.rejects(runtimeMigrationInputs(root, machine, [{ ...reference, project: '/different' }]), /disagree/);
    await assert.rejects(runtimeMigrationInputs(root, machine, [reference, { ...reference, name: 'Conflict' }]), /disagree/);
    await assert.rejects(runtimeMigrationInputs(root, randomUUID(), [reference]), /missing/);
    assert.deepEqual(await runtimeMigrationInputs(root, randomUUID(), []), { '001_worker_ownership': { workspaces: {} } });
    await writeFile(file, JSON.stringify({ [id]: { ...entry, state: 'preparing' } }));
    await assert.rejects(runtimeMigrationInputs(root, machine, [reference]), /pending/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
