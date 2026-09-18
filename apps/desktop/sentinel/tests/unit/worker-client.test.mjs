import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkerClient } from '../../.test-dist/main/workspace/workerClient.js';

const record = { revision: 3, spec: { name: 'Remote', project: '/remote/project', tools: ['git'], distribution: 'ubuntu', resources: { cpus: 4, memory_gib: 8, disk_gib: 64 } } };

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

test('explicit edits carry an expected revision and a self-contained setup plan', async () => {
  const calls = [];
  const client = new WorkerClient({ async request(...args) { calls.push(args); return {}; } });
  await client.request('configure', { workspace: 'example', revision: 3, ...record.spec });
  const [action, request] = calls[0];
  assert.equal(action, 'workspace_configure');
  assert.equal(request.revision, 3);
  assert.equal(request.spec.distribution, 'ubuntu');
  assert.ok(request.spec.steps.length > 0);
  assert.deepEqual(request.spec.resources, record.spec.resources);
});

test('reinstall uses the workers current tools, not the clients cached selection', async () => {
  const calls = [];
  const client = new WorkerClient({ async request(...args) {
    calls.push(args);
    return args[0] === 'workspaces' ? { workspaces: { example: record } } : {};
  } });
  await client.request('reinstall', { workspace: 'example', tools: ['obsolete'] });
  assert.equal(calls[1][0], 'workspace_reinstall');
  assert.equal(calls[1][1].revision, 3);
  assert.ok(calls[1][1].steps.length);
});
