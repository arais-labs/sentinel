import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { NotificationCenter } from '../../.test-dist/main/app/notifications.js';
import { publishWorkspaceNotification } from '../../.test-dist/main/workspace/workspaceNotifications.js';

async function center(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'sentinel-notifications-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const file = path.join(root, 'notifications.json');
  return { file, inbox: new NotificationCenter(file) };
}

test('progress updates reuse one durable entry; read/dismiss and final attention are preserved', async t => {
  const { file, inbox } = await center(t);
  const published = [];
  inbox.events.on('published', (item, alert) => published.push(alert));
  const base = { source: 'test', key: 'job-1', title: 'Preparing', message: 'Starting', progress: true };
  const first = inbox.publish(base);
  inbox.update(first.id, 'read');
  inbox.publish({ ...base, message: 'Installing' });
  assert.equal(inbox.list()[0].read, true);
  inbox.publish({ ...base, message: 'Installing' });
  assert.equal(published.length, 2);
  inbox.update(first.id, 'dismiss');
  const done = inbox.publish({ ...base, message: 'Ready', severity: 'success', progress: false });
  assert.equal(done.id, first.id);
  assert.equal(done.read, false);
  assert.equal(done.dismissed, false);
  assert.deepEqual(published, [true, false, true]);
  assert.deepEqual(new NotificationCenter(file).list(), JSON.parse(JSON.stringify(inbox.list())));
});

test('sources are independent, input validated, storage bounded, bulk actions persisted', async t => {
  const { inbox, file } = await center(t);
  for (const source of ['agent', 'build']) inbox.publish({ source, key: 'same', title: 'Alert', message: 'Details' });
  assert.equal(inbox.list().length, 2);
  assert.throws(() => inbox.publish({ source: 'agent', title: ' ', message: 'x' }), /Invalid/);
  assert.throws(() => inbox.publish({ source: 'agent', title: 'x', message: 'x', target: { sessionId: '../bad' } }), /Invalid/);
  for (let i = 0; i < 205; i++) inbox.publish({ source: 'test', title: `Alert ${i}`, message: 'Details' });
  assert.equal(inbox.list().length, 200);
  inbox.update(null, 'read');
  assert.ok(inbox.list().every(item => item.read));
  inbox.update(null, 'dismiss');
  assert.ok(new NotificationCenter(file).list().every(item => item.dismissed));
});

test('workspace adapter streams setup, failures, retries and ready without duplicate entries', async t => {
  const { inbox } = await center(t);
  const id = randomUUID();
  const spec = { project: '/project', tools: [], state: 'stopped', notificationContext: { instanceName: 'main', name: 'Project' } };
  publishWorkspaceNotification(inbox, id, spec);
  assert.equal(inbox.list().length, 0);
  for (const message of ['Starting', 'Installing tools']) publishWorkspaceNotification(inbox, id, { ...spec, state: 'preparing', message });
  assert.equal(inbox.list().length, 1);
  assert.equal(inbox.list()[0].message, 'Installing tools');
  publishWorkspaceNotification(inbox, id, { ...spec, state: 'failed', error: 'No disk space' });
  assert.equal(inbox.list()[0].severity, 'error');
  assert.equal(inbox.list()[0].message, 'No disk space');
  publishWorkspaceNotification(inbox, id, { ...spec, state: 'preparing', message: 'Retrying' });
  assert.equal(inbox.list()[0].progress, true);
  publishWorkspaceNotification(inbox, id, { ...spec, state: 'running' });
  assert.equal(inbox.list().length, 1);
  assert.equal(inbox.list()[0].severity, 'success');
  assert.equal(inbox.list()[0].target.instanceName, 'main');
  publishWorkspaceNotification(inbox, id, spec);
  assert.equal(inbox.list()[0].severity, 'success');
});

test('authenticated backend can publish over the desktop bridge without starting a workspace', async t => {
  const { inbox, file } = await center(t);
  const { openWorkspaceRuntimeBridge } = await import('../../.test-dist/main/transport/workspaceRuntimeBridge.js');
  const { request } = await import('node:http');
  const socket = path.join(path.dirname(file), 'bridge.sock');
  const bridge = await openWorkspaceRuntimeBridge({}, socket, 'test-token', {}, undefined, inbox);
  t.after(() => bridge.close());
  const post = token => new Promise((resolve, reject) => {
    const req = request({ socketPath: socket, path: '/v1/notifications', method: 'POST', headers: { 'x-sentinel-desktop-token': token } }, response => {
      let data = ''; response.on('data', chunk => data += chunk);
      response.on('end', () => resolve({ status: response.statusCode, data: JSON.parse(data) }));
    });
    req.on('error', reject);
    req.end(JSON.stringify({ source: 'agent', title: 'Deadline', message: 'Please review now', severity: 'urgent' }));
  });
  assert.equal((await post('wrong-token')).status, 401);
  assert.equal(inbox.list().length, 0);
  const response = await post('test-token');
  assert.equal(response.status, 200);
  assert.equal(inbox.list()[0].id, response.data.id);
});
