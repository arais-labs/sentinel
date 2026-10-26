import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkerClient } from '../../.test-dist/main/workspace/workerClient.js';

// The runtime builds a spec from the catalog (workspace-runtime.test.mjs); here it echoes what it was given.
const spec = async ({ browser, ...values }) => ({ ...values, tools: [...values.tools, ...(browser && browser !== 'chromium' ? [browser] : [])] });
const record = { revision: 3, spec: { name: 'Remote', project: '/remote/project', tools: ['git'], distribution: 'ubuntu', desktop: 'none', resources: { cpus: 4, memory_gib: 8, disk_gib: 64 } } };

test('reinstall edits require the reviewed revision and reject stale edits', async () => {
  const calls = [];
  const client = new WorkerClient({ spec, async request(action, values) {
    calls.push([action, values]);
    return action === 'workspaces' ? { workspaces: { example: record } } : { capabilities: ['workspace-reinstall-v1'] };
  } });
  const edit = { workspace: 'example', confirmed: true, spec: { name: 'New name', browser: 'firefox' } };
  await assert.rejects(client.request('reinstall', edit), /Refresh workspace settings/);
  assert.equal(calls.length, 0);
  await assert.rejects(client.request('reinstall', { ...edit, revision: 2 }), /settings changed/);
  assert.equal(calls.some(([action]) => action === 'workspace_reinstall'), false);
  await client.request('reinstall', { ...edit, revision: 3 });
  const [action, request] = calls.at(-1);
  assert.equal(action, 'workspace_reinstall');
  assert.equal(request.revision, 3);
  assert.equal(request.spec.name, 'New name');
  assert.equal(request.spec.project, record.spec.project);
  assert.deepEqual(request.spec.resources, record.spec.resources);
  assert.deepEqual(request.spec.tools, ['git', 'firefox']);
  assert.equal('steps' in request.spec || 'browser' in request.spec, false);
});

test('a fresh client discovers worker-owned configuration and operation state', async () => {
  const calls = [];
  const client = new WorkerClient({ async request(action) {
    calls.push(action);
    return action === 'status' ? { states: { example: 'running' } } : { worker_id: 'worker', workspaces: { example: { ...record, operation: 'preparing' } } };
  } });
  const result = await client.request('status');
  assert.deepEqual(calls.sort(), ['status', 'workspaces']);
  assert.equal(result.worker_id, 'worker');
  assert.equal(result.workspaces.example.spec.project, '/remote/project');
  assert.equal(result.states.example.state, 'preparing');
  assert.equal(result.states.example.revision, 3);
});

test('starting by ID never pushes stale client configuration', async () => {
  const calls = [];
  const client = new WorkerClient({ async request(...args) { calls.push(args); return {}; } });
  await client.request('prepare', { workspace: 'example', project: '/stale', tools: ['other'] });
  assert.deepEqual(calls, [['workspace_start', { workspace: 'example' }]]);
});

test('explicit edits carry an expected revision and the catalog spec, the browser as a tool', async () => {
  const calls = [];
  const client = new WorkerClient({ spec, async request(...args) { calls.push(args); return {}; } });
  await client.request('configure', { workspace: 'example', revision: 3, ...record.spec, browser: 'firefox' });
  const [action, request] = calls[0];
  assert.equal(action, 'workspace_configure');
  assert.equal(request.revision, 3);
  assert.equal(request.spec.distribution, 'ubuntu');
  assert.deepEqual(request.spec.tools, ['git', 'firefox']);
  assert.equal('steps' in request.spec || 'browser' in request.spec, false);
  assert.deepEqual(request.spec.resources, record.spec.resources);
});

test('reinstall uses the workers current tools, not the clients cached selection', async () => {
  const calls = [];
  const client = new WorkerClient({ async request(...args) {
    calls.push(args);
    return args[0] === 'workspaces' ? { workspaces: { example: record } } : {capabilities:['workspace-reinstall-v1']};
  } });
  await assert.rejects(client.request('reinstall', {workspace:'example'}), /Confirm erasing/);
  assert.equal(calls.length, 0);
  await client.request('reinstall', { workspace: 'example', tools: ['obsolete'], confirmed: true });
  assert.equal(calls[2][0], 'workspace_reinstall');
  assert.equal(calls[2][1].revision, 3);
  assert.equal(calls[2][1].confirmed, true);
  assert.equal('spec' in calls[2][1] || 'steps' in calls[2][1], false);
});

test('older same-protocol workers require update instead of silently performing old reinstall behavior', async () => {
  const calls=[];
  const client=new WorkerClient({async request(action){calls.push(action);return {protocol_version:2,capabilities:['workspace-recovery-v1']};}});
  await assert.rejects(client.request('reinstall',{workspace:'example',confirmed:true}), error => {
    assert.equal(error.code,'runtime_update_required');
    assert.equal(error.details.capability,'workspace-reinstall-v1');
    return true;
  });
  assert.deepEqual(calls,['status']);
});
