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

test('remote workspace uses existing lifecycle through tunnel and closing bridge preserves VM ownership', { skip: process.platform !== 'darwin', timeout: 15000 }, async () => {
  const directory = await mkdtemp('/tmp/sentinel-remote-');
  const workspace = randomUUID(), machine = randomUUID();
  const calls = [], clients = new Set();
  const states = {};
  const remote = createServer(socket => {
    clients.add(socket); socket.on('close', () => clients.delete(socket));
    socket.write('{"event":"ready"}\n');
    createInterface({ input: socket }).on('line', line => {
      const value = JSON.parse(line); calls.push(value);
      if (value.action === 'start') states[value.workspace] = 'running';
      socket.write(JSON.stringify({ id: value.id, states, exitCode: 0 }) + '\n');
    });
  });
  let bridge;
  try {
    remote.listen(directory + '/control.sock'); await once(remote, 'listening');
    const localRuntime = { events: new EventEmitter(), deployment: async () => ({ executable: directory + '/helper', kernel: directory + '/kernels/hash/kernel' }), request: async () => { throw new Error('Local runtime must not receive remote commands'); } };
    bridge = await openWorkspaceRuntimeBridge(localRuntime, directory + '/local.sock', 'test', { status: () => ({ states: {} }) });
    const registered = await post(bridge.socketPath, { action: 'remote_register', machine, socket: directory + '/control.sock', bridge: directory + '/bridge.sock' });
    await post(registered.socket, { action: 'prepare', workspace, project: '/Users/remote/project', tools: [] });
    for (let attempt = 0; attempt < 100; attempt++) {
      const status = await post(registered.socket, { action: 'status' });
      if (status.states[workspace]?.state === 'running') break;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.equal((await post(registered.socket, { action: 'status' })).states[workspace].state, 'running');
    assert.equal(calls.find(call => call.action === 'start').project, '/Users/remote/project');
    await post(registered.socket, { action: 'graphics_start', workspace });
    assert.equal(calls.filter(call => call.action === 'graphics_start').length, 1);
    assert.equal(calls.some(call => call.action === 'process_start'), false, 'Viewer must not start a graphics relay back to itself');
    await post(registered.socket, { action: 'port_forward', workspace, port: 5901 });
    assert.equal(calls.find(call => call.action === 'port_forward').port, 5901);
    await post(registered.socket, { action: 'check_remote_update' });
    const starts = calls.filter(call => call.action === 'start').length;
    states[workspace] = 'stopped';
    await post(bridge.socketPath, { action: 'remote_register', machine, socket: directory + '/control.sock', bridge: directory + '/bridge.sock' });
    await post(registered.socket, { action: 'resume_remote', workspaces: [workspace] });
    assert.equal(calls.filter(call => call.action === 'start').length, starts + 1);
    assert.equal((await post(registered.socket, { action: 'status' })).states[workspace].state, 'running');
    await assert.rejects(post(registered.socket, { action: 'resume_remote', workspaces: [randomUUID()] }), /not registered/);
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
