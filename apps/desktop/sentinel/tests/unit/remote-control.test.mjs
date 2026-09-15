import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { createConnection } from 'node:net';
import { once } from 'node:events';
import { createInterface } from 'node:readline';

test('remote control survives client disconnect and keeps its private socket', { skip: process.platform !== 'darwin', timeout: 60000 }, async (t) => {
  const directory = await mkdtemp('/tmp/sentinel-control-test-');
  let service;
  const clients = [];
  try {
    execFileSync('swiftc', ['native/macos/Sources/WorkspaceRuntime/RemoteControl.swift', 'tests/fixtures/RemoteControlHarness.swift', '-o', directory + '/harness']);
    t.after(() => { for (const client of clients) client.destroy(); service?.kill(); });
    const socket = directory + '/control.sock';
    assert.equal(JSON.parse(execFileSync(directory + '/harness', ['--relay', socket], { encoding: 'utf8' })).event, 'unavailable');
    service = spawn(directory + '/harness', [socket], { stdio: 'ignore' });
    for (let attempt = 0; attempt < 100; attempt++) {
      try { await stat(socket); break; } catch { await new Promise(resolve => setTimeout(resolve, 20)); }
    }
    assert.equal((await stat(socket)).mode & 0o777, 0o600);
    const relay = spawn(directory + '/harness', ['--relay', socket]);
    try {
      const lines = createInterface({ input: relay.stdout })[Symbol.asyncIterator]();
      assert.equal(JSON.parse((await lines.next()).value).event, 'connected');
      assert.equal(JSON.parse((await lines.next()).value).event, 'ready');
    } finally {
      const exited = once(relay, 'exit');
      relay.stdin.end();
      await exited;
    }
    for (const id of ['first', 'reconnected']) {
      const client = createConnection(socket); clients.push(client);
      const lines = createInterface({ input: client })[Symbol.asyncIterator]();
      assert.equal(JSON.parse((await lines.next()).value).event, 'ready');
      // A health probe closes without consuming the server's ready frame. Its
      // broken pipe must not kill the service or interrupt this other client.
      for (let attempt = 0; attempt < 10; attempt++) {
        execFileSync(directory + '/harness', ['--ping', socket]);
        client.write(JSON.stringify({ id: `ping-${attempt}`, action: 'status' }) + '\n');
        const reply = await lines.next();
        assert.equal(reply.done, false, 'Health check killed the active runtime connection');
        assert.equal(JSON.parse(reply.value).id, `ping-${attempt}`);
      }
      client.write(JSON.stringify({ id, action: 'status' }) + '\n');
      assert.equal(JSON.parse((await lines.next()).value).id, id);
      client.destroy();
    }
    // Keep one client reading complete replies while other connections close
    // during broadcasts. emit's client snapshot can outlive their removal.
    const observer = createConnection(socket); clients.push(observer);
    const lines = createInterface({ input: observer })[Symbol.asyncIterator]();
    assert.equal(JSON.parse((await lines.next()).value).event, 'ready');
    observer.write(JSON.stringify({ id: 'block-guest' }) + '\n');
    await new Promise(resolve => setTimeout(resolve, 50));
    const started = performance.now();
    observer.write(JSON.stringify({ id: 'independent-status', action: 'status' }) + '\n');
    assert.equal(JSON.parse((await lines.next()).value).id, 'independent-status');
    assert.ok(performance.now() - started < 1000, 'Status waited on the guest request loop');
    assert.equal(JSON.parse((await lines.next()).value).id, 'block-guest');
    const payload = 'x'.repeat(256);
    for (let batch = 0; batch < 125; batch++) {
      await Promise.all(Array.from({ length: 16 }, async (_, index) => {
        const client = createConnection(socket); clients.push(client);
        client.resume();
        client.on('error', () => {}); // Broken pipes are expected for these peers.
        await once(client, 'connect');
        client.write(JSON.stringify({ id: `churn-${batch}-${index}`, payload }) + '\n');
        if (index % 2) client.end();
        else client.destroy();
      }));
      const id = `barrier-${batch}`;
      observer.write(JSON.stringify({ id }) + '\n');
      while (true) {
        const reply = await lines.next();
        assert.equal(reply.done, false, 'Concurrent close killed the runtime');
        if (JSON.parse(reply.value).id === id) break;
      }
    }
    assert.equal(service.exitCode, null);
    assert.equal(service.signalCode, null);
  } finally {
    for (const client of clients) client.destroy();
    if (service && service.exitCode === null && service.signalCode === null) { service.kill(); await once(service, 'exit'); }
    await rm(directory, { recursive: true, force: true });
  }
});
