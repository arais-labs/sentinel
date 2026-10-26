import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';

// What the fake sentineld's `catalog` op lists (DESIGN.md §5).
const catalog = {
  distributions: ['alpine', 'debian', 'ubuntu'].map(id => ({ id, name: id, detail: null, features: ['shell', 'terminal', 'files'] }))
    .concat({ id: 'windows-11', name: 'Windows 11', detail: null, features: [] }),
  desktops: [{ id: 'xfce', name: 'Xfce', distributions: ['alpine', 'debian', 'ubuntu'] }],
  tools: [
    { id: 'git', name: 'Git', detail: '', category: 'Essentials', distributions: ['alpine', 'debian', 'ubuntu'] },
    { id: 'firefox', name: 'Firefox', detail: '', category: 'Browsers', distributions: ['alpine', 'debian', 'ubuntu'] },
    { id: 'chrome', name: 'Google Chrome', detail: '', category: 'Browsers', distributions: ['debian', 'ubuntu'] },
  ],
  stacks: [], wallpapers: [],
};

// A fake sentineld (DESIGN.md §4): NDJSON ops, workspace events to watchers, process frames.
async function fakeSentineld(parent = tmpdir(), prefix = 'sentineld-') {
  const dir = await mkdtemp(path.join(parent, prefix));
  const socket = path.join(dir, 'sentineld.sock');
  const workspaces = {};
  const requests = [];
  const watchers = new Set();
  const frame = (type, payload) => {
    const header = Buffer.alloc(5);
    header[0] = type;
    header.writeUInt32BE(payload.length, 1);
    return Buffer.concat([header, payload]);
  };
  const server = createServer(connection => {
    let buffer = Buffer.alloc(0), process = null;
    const reply = message => connection.write(`${JSON.stringify(message)}\n`);
    const changed = id => { for (const watcher of watchers) watcher({ event: 'workspace', id, record: workspaces[id] }); };
    connection.on('close', () => watchers.delete(reply));
    connection.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (process) {  // stdin frames
        while (buffer.length >= 5 && buffer.length >= 5 + buffer.readUInt32BE(1)) {
          const length = buffer.readUInt32BE(1), type = buffer[0], payload = buffer.subarray(5, 5 + length);
          buffer = buffer.subarray(5 + length);
          if (type === 0 && length) connection.write(frame(1, Buffer.concat([Buffer.from('echo:'), payload])));
          if (type === 0 && !length) { connection.write(frame(3, Buffer.from(JSON.stringify({ code: 7 })))); connection.end(); }
        }
        return;
      }
      for (let newline; (newline = buffer.indexOf(10)) >= 0;) {
        const request = JSON.parse(buffer.subarray(0, newline).toString());
        buffer = buffer.subarray(newline + 1);
        requests.push(request);
        const { id, op, workspace } = request;
        if (op === 'status') reply({ id, api: 1 });
        else if (op === 'catalog') reply({ id, ...catalog });
        else if (op === 'help') reply({ id, text: request.topic === 'distributions' ? 'Writing a distribution' : 'sentineld' });
        else if (op === 'distribution') reply({ id, distribution: request.distribution, files: Object.keys(request.files), problem: null, writable: true });
        else if (op === 'distribution_read') reply(request.path ? { id, path: request.path, text: '{}', executable: false } : { id, distribution: request.distribution, files: [] });
        else if (op === 'distribution_delete') reply({ id });
        else if (op === 'source') reply({ id, url: request.url, state: 'ready', bytes: 3, sha256: 'ab'.repeat(32) });
        else if (op === 'usage') reply({ id, workspaces: { w1: 2048 }, shared: { systems: 4096, packages: 512, sources: 0, installs: 0, distributions: 8 }, total: 6664, free: 100, capacity: 1000 });
        else if (op === 'log') reply({ id, console: `last ${request.lines ?? 200}`, build: null, failure: 'install: boom' });
        else if (op === 'list') { if (request.watch) watchers.add(reply); reply({ id, workspaces }); }
        else if (op === 'configure') {
          if ((workspaces[workspace]?.revision ?? 0) !== request.revision) { reply({ id, error: { code: 'conflict', message: 'revision mismatch' } }); continue; }
          workspaces[workspace] = { spec: request.spec, revision: request.revision + 1, state: 'stopped', operation: null, error: null };
          reply({ id, record: workspaces[workspace] });
        } else if (op === 'start') {
          workspaces[workspace] = { ...workspaces[workspace], operation: 'preparing' };
          reply({ id, record: workspaces[workspace] });
          // Provisioning reports what it is doing before the boot settles.
          setTimeout(() => {
            workspaces[workspace] = { ...workspaces[workspace], progress: 'Starting graphics…' };
            changed(workspace);
          }, 5);
          setTimeout(() => {
            const failed = workspaces[workspace].spec.distribution === 'ubuntu';
            workspaces[workspace] = { ...workspaces[workspace], operation: null, progress: null, state: failed ? 'failed' : 'running', error: failed ? 'boot failed' : null };
            changed(workspace);
          }, 20);
        } else if (op === 'stop') { workspaces[workspace].state = 'stopped'; reply({ id, record: workspaces[workspace] }); }
        else if (op === 'delete') { delete workspaces[workspace]; reply({ id }); for (const watcher of watchers) watcher({ event: 'workspace', id: workspace, record: null }); }
        else if (op === 'exec') reply({ id, code: 3, stdout: request.argv.join(' '), stderr: 'e', truncated: false, timed_out: false });
        else if (op === 'forward') reply({ id, socket: `/forwards/${workspace}-${request.port}.sock` });
        else if (op === 'display') reply({ id, port: 4433, cert_sha256: 'ab'.repeat(32), ticket: 't'.repeat(32), expires: 1 });
        else if (op === 'clipboard') reply(request.action === 'read' ? { id, text: 'from the guest' } : { id });
        else if (op === 'process') {
          process = request;
          // `started` and the first output frame in one write: the client must split them.
          connection.write(Buffer.concat([Buffer.from(`${JSON.stringify({ id, started: true })}\n`), frame(1, Buffer.from('hello'))]));
        } else reply({ id, error: { code: 'unsupported', message: `unknown op ${op}` } });
      }
    });
  });
  await new Promise(resolve => server.listen(socket, resolve));
  const close = async () => { server.close(); await rm(dir, { recursive: true, force: true }); };
  return { socket, dir, workspaces, requests, close };
}

function runtime(fake) {
  return new WorkspaceRuntime({ socket: fake.socket, root: fake.dir,
    onProgress: () => {}, onFailure: () => {}, log: () => {} });
}

test('start configures the workspace from the app spec and waits for its boot', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    await owner.request('start', { workspace: 'w1', project: '/p', distribution: 'debian', desktop: 'xfce', cpus: 2, memory_gib: 4, disk_gib: 32 });
    assert.deepEqual(fake.workspaces.w1.spec, { name: 'w1', distribution: 'debian', project: '/p',
      desktop: 'xfce', tools: [], resources: { cpus: 2, memory_gib: 4, disk_gib: 32 } });
    assert.equal(fake.workspaces.w1.state, 'running');
    assert.deepEqual((await owner.request('status')).states, { w1: 'running' });
    // Unchanged spec on a stopped workspace: started again without a new revision.
    await owner.request('stop', { workspace: 'w1' });
    await owner.request('start', { workspace: 'w1', project: '/p', distribution: 'debian', desktop: 'xfce', cpus: 2, memory_gib: 4, disk_gib: 32 });
    assert.equal(fake.requests.filter(request => request.op === 'configure').length, 1);
    // Added tools are a changed spec: sentineld provisions them on this start.
    await owner.request('stop', { workspace: 'w1' });
    await owner.request('start', { workspace: 'w1', project: '/p', distribution: 'debian', desktop: 'xfce', tools: ['git'], cpus: 2, memory_gib: 4, disk_gib: 32 });
    assert.equal(fake.requests.filter(request => request.op === 'configure').length, 2);
    assert.deepEqual(fake.workspaces.w1.spec.tools, ['git']);
  } finally { await owner.stop(); await fake.close(); }
});

test('specs follow the catalog: browser tools where offered', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    const resources = { cpus: 2, memory_gib: 4, disk_gib: 32 };
    const spec = values => owner.spec({ name: 'n', project: '/p', resources, ...values });
    assert.deepEqual((await spec({ distribution: 'debian', tools: ['git'], browser: 'chrome' })).tools, ['git', 'chrome']);
    assert.deepEqual((await spec({ distribution: 'alpine', tools: ['git'], browser: 'chrome' })).tools, ['git']);
    assert.deepEqual((await spec({ distribution: 'alpine', tools: ['firefox'], browser: 'firefox' })).tools, ['firefox']);
    // The default browser is sentineld's own.
    assert.deepEqual((await spec({ distribution: 'alpine', browser: 'chromium' })).tools, []);
    assert.deepEqual((await spec({ distribution: 'alpine', tools: ['docker-builder', 'docker'] })).tools, ['docker']);
    const windows = await spec({ distribution: 'windows-11', browser: 'firefox' });
    assert.deepEqual(windows, { name: 'n', project: '/p', distribution: 'windows-11', desktop: 'none', tools: [], resources });
    assert.equal('image' in await spec({ distribution: 'ubuntu' }), false);
    await assert.rejects(spec({ distribution: 'fedora' }), /does not support the selected distribution/);
    assert.equal(fake.requests.filter(request => request.op === 'catalog').length, 1);
    assert.equal('browser' in await spec({ distribution: 'debian', browser: 'firefox' }), false);
  } finally { await owner.stop(); await fake.close(); }
});

test('the backend reads sentineld\'s catalog through the bridge', async () => {
  const { openWorkspaceRuntimeBridge } = await import('../../.test-dist/main/transport/workspaceRuntimeBridge.js');
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  const local = path.join(fake.dir, 'bridge.sock');
  const bridge = await openWorkspaceRuntimeBridge(owner, local, 'test-token', {});
  try {
    await owner.start();
    const reply = await new Promise((resolve, reject) => {
      const req = httpRequest({ socketPath: local, path: '/v1/request', method: 'POST', headers: { 'x-sentinel-desktop-token': 'test-token' } }, response => {
        let data = ''; response.on('data', chunk => data += chunk);
        response.on('end', () => resolve({ status: response.statusCode, data: JSON.parse(data) }));
      });
      req.on('error', reject);
      req.end(JSON.stringify({ action: 'catalog' }));
    });
    assert.equal(reply.status, 200);
    assert.deepEqual(reply.data, catalog);
  } finally { await bridge.close(); await owner.stop(); await fake.close(); }
});

test('sentineld\'s guide, source, log, usage and distribution ops pass through the bridge; writes reread the catalog', async () => {
  const { openWorkspaceRuntimeBridge } = await import('../../.test-dist/main/transport/workspaceRuntimeBridge.js');
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  const local = path.join(fake.dir, 'bridge.sock');
  const bridge = await openWorkspaceRuntimeBridge(owner, local, 'test-token', {});
  const post = body => new Promise((resolve, reject) => {
    const req = httpRequest({ socketPath: local, path: '/v1/request', method: 'POST', headers: { 'x-sentinel-desktop-token': 'test-token' } }, response => {
      let data = ''; response.on('data', chunk => data += chunk);
      response.on('end', () => resolve(JSON.parse(data)));
    });
    req.on('error', reject);
    req.end(JSON.stringify(body));
  });
  try {
    await owner.start();
    assert.deepEqual(await post({ action: 'help', topic: 'distributions' }), { text: 'Writing a distribution' });
    assert.deepEqual(await post({ action: 'help' }), { text: 'sentineld' });
    await post({ action: 'catalog' });
    const files = { 'distribution.json': { text: '{}' }, 'old.sh': null };
    assert.deepEqual(await post({ action: 'distribution', distribution: 'mine', files }),
      { distribution: 'mine', files: ['distribution.json', 'old.sh'], problem: null, writable: true });
    assert.deepEqual(await post({ action: 'distribution_read', distribution: 'mine', path: 'distribution.json' }),
      { path: 'distribution.json', text: '{}', executable: false });
    assert.deepEqual(await post({ action: 'distribution_delete', distribution: 'mine' }), {});
    await post({ action: 'catalog' });
    assert.deepEqual(await post({ action: 'source', url: 'https://example.org/a.tar' }),
      { url: 'https://example.org/a.tar', state: 'ready', bytes: 3, sha256: 'ab'.repeat(32) });
    await post({ action: 'source', url: 'https://example.org/a.tar', paths: ['etc/x'], offset: 10, length: 5, limit: 50000 });
    assert.deepEqual(await post({ action: 'log', workspace: 'w1', lines: 50 }), { console: 'last 50', build: null, failure: 'install: boom' });
    assert.deepEqual(await post({ action: 'log', workspace: 'w1' }), { console: 'last 200', build: null, failure: 'install: boom' });
    assert.deepEqual(await post({ action: 'usage' }), { workspaces: { w1: 2048 },
      shared: { systems: 4096, packages: 512, sources: 0, installs: 0, distributions: 8 }, total: 6664, free: 100, capacity: 1000 });
    // Only each op's own fields reach sentineld, which rejects unknown ones.
    const sent = fake.requests.filter(request => !['status', 'list'].includes(request.op)).map(({ id: _id, ...request }) => request);
    assert.deepEqual(sent, [
      { op: 'help', topic: 'distributions' }, { op: 'help' }, { op: 'catalog' },
      { op: 'distribution', distribution: 'mine', files },
      { op: 'distribution_read', distribution: 'mine', path: 'distribution.json' },
      { op: 'distribution_delete', distribution: 'mine' }, { op: 'catalog' },
      { op: 'source', url: 'https://example.org/a.tar' },
      { op: 'source', url: 'https://example.org/a.tar', paths: ['etc/x'], offset: 10, length: 5, limit: 50000 },
      { op: 'log', workspace: 'w1', lines: 50 }, { op: 'log', workspace: 'w1' }, { op: 'usage' },
    ]);
  } finally { await bridge.close(); await owner.stop(); await fake.close(); }
});

test('provisioning progress reaches the app while the workspace prepares', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    const seen = [];
    owner.events.on('workspace-progress', (id, message) => seen.push([id, message]));
    await owner.request('start', { workspace: 'w4', project: '/p', distribution: 'alpine', desktop: 'lxqt', cpus: 1, memory_gib: 1, disk_gib: 8 });
    assert.deepEqual(seen, [['w4', 'Starting graphics…']]);
  } finally { await owner.stop(); await fake.close(); }
});

test('a failed boot rejects with sentineld\'s error', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    await assert.rejects(owner.request('start', { workspace: 'w2', project: '/p', distribution: 'ubuntu', cpus: 1, memory_gib: 1, disk_gib: 8 }), /boot failed/);
    assert.equal((await owner.request('status')).errors.w2, 'boot failed');
  } finally { await owner.stop(); await fake.close(); }
});

test('exec, forward and display map to the actions the backend uses', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    assert.deepEqual(await owner.request('exec', { workspace: 'w', arguments: ['bash', '-c', 'x'], timeout: 5 }),
      { exitCode: 3, stdout: 'bash -c x', stderr: 'e', truncated: false });
    assert.deepEqual(await owner.request('port_forward', { workspace: 'w', port: 80 }), { socket: '/forwards/w-80.sock' });
    assert.deepEqual(await owner.request('display', { workspace: 'w' }), { port: 4433, cert_sha256: 'ab'.repeat(32), ticket: 't'.repeat(32) });
    await assert.rejects(owner.request('graphics_install', { workspace: 'w' }), /does not support graphics_install/);
  } finally { await owner.stop(); await fake.close(); }
});

test('clipboard: the direction a request names is sentineld\'s action', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    assert.equal((await owner.request('clipboard', { workspace: 'w', direction: 'read' })).text, 'from the guest');
    await owner.request('clipboard', { workspace: 'w', direction: 'write', text: 'café' });
    const sent = fake.requests.filter(r => r.op === 'clipboard').map(({ action, text }) => ({ action, text }));
    assert.deepEqual(sent, [{ action: 'read', text: undefined }, { action: 'write', text: 'café' }]);
  } finally { await owner.stop(); await fake.close(); }
});

test('a process streams output, takes input and reports its exit code', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    const events = [];
    const exited = new Promise(resolve => owner.events.on('p1', event => { events.push(event); if (event.event === 'exit') resolve(); }));
    await owner.request('process_start', { workspace: 'w', process: 'p1', arguments: ['sh'], terminal: false });
    await owner.request('process_input', { process: 'p1', data: Buffer.from('hi').toString('base64') });
    await owner.request('process_input', { process: 'p1' });
    await exited;
    const output = events.filter(event => event.event === 'output').map(event => Buffer.from(event.data, 'base64').toString()).join('');
    assert.equal(output, 'helloecho:hi');
    assert.deepEqual(events.at(-1), { process: 'p1', event: 'exit', exitCode: 7 });
  } finally { await owner.stop(); await fake.close(); }
});

test('requests before the runtime is connected fail without being queued', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await assert.rejects(owner.request('status'), /not ready/);
    assert.equal(fake.requests.length, 0);
  } finally { await fake.close(); }
});

test('a deleted workspace\'s event (no record) never breaks the runtime', async () => {
  const fake = await fakeSentineld();
  const owner = runtime(fake);
  try {
    await owner.start();
    await owner.request('start', { workspace: 'w3', project: '/p', distribution: 'alpine', cpus: 1, memory_gib: 1, disk_gib: 8 });
    await owner.request('delete', { workspace: 'w3' });
    assert.deepEqual((await owner.request('status')).states, {});
  } finally { await owner.stop(); await fake.close(); }
});

test('a remote Mac registers only through the backend\'s private tunnel', async () => {
  const { openWorkspaceRuntimeBridge } = await import('../../.test-dist/main/transport/workspaceRuntimeBridge.js');
  // The backend's tunnel directory: /tmp/sentinel-remote-*/control.sock (remote_mac.py).
  const fake = await fakeSentineld('/tmp', 'sentinel-remote-');
  const local = path.join(fake.dir, 'local.sock');
  const bridge = await openWorkspaceRuntimeBridge({}, local, 'test-token', {});
  const post = body => new Promise((resolve, reject) => {
    const req = httpRequest({ socketPath: local, path: '/v1/request', method: 'POST', headers: { 'x-sentinel-desktop-token': 'test-token' } }, response => {
      let data = ''; response.on('data', chunk => data += chunk);
      response.on('end', () => resolve({ status: response.statusCode, data: JSON.parse(data) }));
    });
    req.on('error', reject);
    req.end(JSON.stringify(body));
  });
  const register = (socket, bridgePath) => post({ action: 'remote_register', machine: randomUUID(), socket, bridge: bridgePath });
  try {
    for (const [socket, bridgePath] of [
      ['/var/run/other.sock', path.join(fake.dir, 'bridge.sock')],
      [fake.socket, '/tmp/elsewhere/bridge.sock'],
    ]) {
      const response = await register(socket, bridgePath);
      assert.notEqual(response.status, 200);
      assert.match(response.data.error, /Invalid remote runtime tunnel/);
    }
    assert.equal(fake.requests.length, 0);
    const accepted = await register(fake.socket, path.join(fake.dir, 'bridge.sock'));
    assert.equal(accepted.status, 200);
    assert.equal(accepted.data.socket, path.join(fake.dir, 'bridge.sock'));
    assert.equal(fake.requests[0].op, 'status');
  } finally { await bridge.close(); await fake.close(); }
});
