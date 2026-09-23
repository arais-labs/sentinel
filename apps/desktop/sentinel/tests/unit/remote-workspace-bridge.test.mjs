import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { request } from 'node:http';
import { createInterface } from 'node:readline';
import { EventEmitter, once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { openWorkspaceRuntimeBridge } from '../../.test-dist/main/transport/workspaceRuntimeBridge.js';

function post(socketPath, payload) {
  return new Promise((resolve, reject) => {
    const call = request({ socketPath, path: '/v1/request', method: 'POST', headers: { 'x-sentinel-desktop-token': 'test' } }, response => {
      let body = ''; response.on('data', chunk => body += chunk); response.on('end', () => {
        const value = JSON.parse(body);
        response.statusCode === 200 ? resolve(value) : reject(new Error(value.error));
      });
    });
    call.on('error', reject); call.end(JSON.stringify(payload));
  });
}

test('remote bridge discovers worker configuration and disconnect never changes VM ownership', { skip: process.platform !== 'darwin', timeout: 15000 }, async () => {
  const directory = await mkdtemp('/tmp/sentinel-remote-');
  const workspace = randomUUID(), machine = randomUUID();
  const calls = [], clients = new Set();
  const states = {};
  const workspaces = {};
  const remote = createServer(socket => {
    clients.add(socket); socket.on('close', () => clients.delete(socket));
    socket.write('{"event":"ready","protocol_version":2}\n');
    createInterface({ input: socket }).on('line', line => {
      const value = JSON.parse(line); calls.push(value);
      if (value.action === 'workspace_configure') workspaces[value.workspace] = { spec: value.spec, revision: 1 };
      if (value.action === 'workspace_start') states[value.workspace] = 'running';
      socket.write(JSON.stringify({ id: value.id, states, workspaces, worker_id: machine, exitCode: 0 }) + '\n');
    });
  });
  let bridge;
  try {
    remote.listen(directory + '/control.sock'); await once(remote, 'listening');
    const localRuntime = { events: new EventEmitter(), deployment: async () => ({ executable: directory + '/helper', kernel: directory + '/kernels/hash/kernel' }), request: async () => { throw new Error('Local runtime must not receive remote commands'); } };
    bridge = await openWorkspaceRuntimeBridge(localRuntime, directory + '/local.sock', 'test', { status: () => ({ states: {} }) });
    const registered = await post(bridge.socketPath, { action: 'remote_register', machine, socket: directory + '/control.sock', bridge: directory + '/bridge.sock' });
    await post(registered.socket, { action: 'configure', workspace, name: 'Example', project: '/Users/remote/project', tools: [], revision: 0 });
    await post(registered.socket, { action: 'prepare', workspace });
    assert.equal((await post(registered.socket, { action: 'status' })).states[workspace].state, 'running');
    assert.equal(calls.find(call => call.action === 'workspace_configure').spec.project, '/Users/remote/project');
    assert.equal(calls.find(call => call.action === 'workspace_start').project, undefined);
    await post(registered.socket, { action: 'workspace_resume', approved_workspaces: [workspace] });
    assert.deepEqual(calls.find(call => call.action === 'workspace_resume').approved_workspaces, [workspace]);
    await post(registered.socket, { action: 'display_start', workspace });
    assert.equal(calls.filter(call => call.action === 'display_start').length, 1);
    assert.equal(calls.some(call => call.action === 'process_start'), false, 'Viewer must not start a graphics relay back to itself');
    await post(registered.socket, { action: 'port_forward', workspace, port: 5901 });
    assert.equal(calls.find(call => call.action === 'port_forward').port, 5901);
    const starts = calls.filter(call => call.action === 'workspace_start').length;
    await post(bridge.socketPath, { action: 'remote_register', machine, socket: directory + '/control.sock', bridge: directory + '/bridge.sock' });
    assert.equal(calls.filter(call => call.action === 'workspace_start').length, starts);
    assert.equal((await post(registered.socket, { action: 'status' })).states[workspace].state, 'running');
    assert.equal((await post(registered.socket, { action: 'status' })).workspaces[workspace].spec.name, 'Example');
    await bridge.close(); bridge = null;
    assert.equal(calls.some(call => call.action === 'graphics_stop'), false, 'Viewer disconnect must preserve remote rendering');
    assert.equal(states[workspace], 'running');
    assert.equal(calls.some(call => call.action === 'shutdown' || call.action === 'stop'), false);
  } finally {
    await bridge?.close();
    for (const client of clients) client.destroy();
    remote.close();
    await rm(directory, { recursive: true, force: true });
  }
});
