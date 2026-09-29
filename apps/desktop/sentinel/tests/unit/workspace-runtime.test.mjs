import assert from 'node:assert/strict';
import { test } from 'node:test';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';

const helper = `
  const { createInterface } = await import('node:readline');
  console.log(JSON.stringify({ event: 'preparing' }));
  console.log(JSON.stringify({ event: 'ready', protocol_version: 2 }));
  const lines = createInterface({ input: process.stdin });
  lines.on('line', line => {
    const request = JSON.parse(line);
    if (request.action === 'crash') process.exit(17);
    console.log(JSON.stringify({ id: request.id,
      ...(request.action === 'fail' ? { error: 'guest error' } : { stdout: request.value, event: 'ready' })
    }));
  });
  lines.on('close', () => process.exit(0));
`;

function runtime(script = helper, overrides = {}) {
  const progress = [], failures = [];
  const owner = new WorkspaceRuntime({
    command: process.execPath, args: ['--input-type=module', '-e', script],
    onProgress: message => progress.push(message),
    onFailure: message => failures.push(message), log: () => {},
    startupTimeoutMs: 2000, shutdownTimeoutMs: 200, ...overrides,
  });
  return { owner, progress, failures };
}

for (const version of [undefined, 1, 3]) for (const remote of [false, true]) {
  test(`${remote ? 'remote' : 'bundled'} runtime protocol ${version ?? 'absent'} blocks commands with the correct recovery`, async () => {
    const script = helper.replace('protocol_version: 2', `protocol_version: ${version}`);
    const { owner } = runtime(script, { remote });
    try {
      await owner.start();
      await owner.request('status');
      await assert.rejects(owner.request('crash'), error => {
        assert.equal(error.code, remote && (version ?? 1) < 2 ? 'runtime_update_required' : 'app_update_required');
        assert.deepEqual(error.details, { installed: version ?? 1, required: 2 });
        if (!remote) assert.match(error.message, /bundled runtime/);
        return true;
      });
      // The blocked command would terminate the child if sent.
      await owner.request('status');
    } finally { await owner.stop(); }
  });
}

test('reading runtime deployment metadata does not prepare assets or start a helper', async () => {
  let prepared = 0;
  const { owner } = runtime(helper, { prepare: async () => { prepared++; } });
  await owner.deployment(false);
  assert.equal(prepared, 0);
  assert.equal(owner.isReady, false);
  await owner.deployment();
  assert.equal(prepared, 1);
  assert.equal(owner.isReady, false);
});

test('deployment metadata has only native boot inputs, not a default Docker image', async () => {
  const { owner } = runtime(helper, {
    command: '/runtime/helper', args: ['/state', '/runtime/kernel', 'init@digest'],
  });
  assert.deepEqual(await owner.deployment(false), {
    root: '/state', executable: '/runtime/helper', kernel: '/runtime/kernel', initImage: 'init@digest',
  });
  assert.equal(owner.isReady, false);
});

test('one owned helper serves concurrent requests and closes through parent EOF', async () => {
  const { owner, progress } = runtime();
  try {
    await Promise.all([owner.start(), owner.start()]);
    const replies = await Promise.all([
      owner.request('status', { value: 'first' }), owner.request('status', { value: 'second' }),
    ]);
    assert.deepEqual(replies.map(reply => reply.stdout), ['first', 'second']);
    assert.equal(progress.filter(value => value === 'Workspace runtime ready').length, 1);
    await assert.rejects(owner.request('fail'), /guest error/);
    assert.equal((await owner.request('status')).event, 'ready');
  } finally { await owner.stop(); }
});

test('an in-flight request fails on crash and is never replayed; restart stays usable', async () => {
  const { owner, failures } = runtime();
  try {
    await owner.start();
    await assert.rejects(owner.request('crash'), /exited/);
    assert.equal(failures.length, 1);
    await assert.rejects(owner.request('status'), /not ready/);
    await owner.start();
    assert.equal((await owner.request('status')).event, 'ready');
    assert.equal(failures.length, 1);
  } finally { await owner.stop(); }
});

// This process outlives the helper while holding both output pipes open,
// like a renderer that inherited stderr. It exits on its own after the test.
const inheritedPipesHelper = `
  const { spawn } = await import('node:child_process');
  spawn(process.execPath, ['-e', 'setTimeout(() => {}, 2000)'], {
    stdio: ['ignore', 'inherit', 'inherit'],
  }).unref();
  ${helper}
`;

test('helper exit rejects requests despite inherited pipes and permits a clean restart', { timeout: 1500 }, async () => {
  const { owner, failures } = runtime(inheritedPipesHelper);
  let closed = 0;
  owner.events.on('closed', () => { closed++; });
  try {
    await owner.start();
    await assert.rejects(owner.request('crash'), /exited \(17\)/);
    assert.equal(owner.isReady, false);
    assert.equal(failures.length, 1);
    assert.equal(closed, 1);
    await owner.start();
    assert.equal((await owner.request('status', { value: 'restarted' })).stdout, 'restarted');
    assert.equal(closed, 1);
  } finally { await owner.stop(); }
  assert.equal(closed, 2);
  assert.equal(failures.length, 1);
});

test('shutdown completes on helper exit while a descendant holds its pipes', { timeout: 1500 }, async () => {
  const { owner, failures } = runtime(inheritedPipesHelper);
  await owner.start();
  await owner.stop();
  assert.equal(owner.isReady, false);
  assert.deepEqual(failures, []);
  await owner.stop();
});

test('shutdown escalates to SIGKILL when the helper ignores EOF and SIGTERM', { timeout: 7000 }, async () => {
  const { owner, failures } = runtime(`
    process.on('SIGTERM', () => {});
    process.stdin.resume();
    setInterval(() => {}, 1000);
    console.log(JSON.stringify({ event: 'ready', protocol_version: 2 }));
  `, { shutdownTimeoutMs: 10 });
  let exit;
  owner.events.once('closed', error => { exit = error; });
  await owner.start();
  await owner.stop();
  assert.match(exit.message, /SIGKILL/);
  assert.equal(owner.isReady, false);
  assert.deepEqual(failures, []);
});

test('a startup timeout terminates its child and reports failure', async () => {
  const { owner } = runtime('setInterval(() => {}, 1000)', { startupTimeoutMs: 100 });
  try { await assert.rejects(owner.start(), /setup timed out/); }
  finally { await owner.stop(); }
});

test('a missing bundled helper is reported without unhandled pipe errors', async () => {
  const { owner } = runtime(helper, { command: '/no-such-sentinel-helper' });
  try { await assert.rejects(owner.start(), /ENOENT/); }
  finally { await owner.stop(); }
});

test('shutdown during kernel preparation never launches a late helper', async () => {
  const gate = Promise.withResolvers();
  const { owner, progress } = runtime(helper, {
    prepare: () => gate.promise,
    cancelPreparation: async () => gate.reject(new Error('cancelled')),
  });
  const starting = assert.rejects(owner.start(), /cancelled/);
  await owner.stop();
  await starting;
  assert.equal(owner.isReady, false);
  assert.equal(progress.length, 0);
});
