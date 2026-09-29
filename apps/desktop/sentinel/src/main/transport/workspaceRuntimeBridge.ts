import path from 'node:path';
import { WorkerClient } from '../workspace/workerClient.js';
import { runtimeMigrationInputs } from '../workspace/migrations/runtimeMigrationInputs.js';
import type { NotificationCenter } from '../app/notifications.js';
import { WebSocketServer, WebSocket } from 'ws';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { chmod } from 'node:fs/promises';
import { timingSafeEqual } from 'node:crypto';
import { WorkspaceRuntime } from '../workspace/workspaceRuntime.js';
import { WorkspaceLifecycle } from '../workspace/workspaceLifecycle.js';
import { RuntimeCompatibilityError } from '../workspace/runtimeCompatibility.js';

const actions = new Set(['status', 'recover', 'prepare', 'reinstall', 'exec', 'stop', 'delete', 'display_start', 'port_forward', 'deployment', 'remote_register', 'configure', 'workspaces', 'workspace_resume', 'runtime_migration_inputs']);

export async function openWorkspaceRuntimeBridge(runtime: WorkspaceRuntime, socketPath: string, token: string, lifecycle: WorkspaceLifecycle, notifications?: NotificationCenter, worker?: WorkerClient) {
  const remotes = new Map<string, { socket: string; bridge: Awaited<ReturnType<typeof openWorkspaceRuntimeBridge>>; runtime: WorkspaceRuntime }>();
  const expectedToken = Buffer.from(token);
  const authorized = (value: unknown) => {
    const supplied = Buffer.from(String(value || ''));
    return supplied.length === expectedToken.length && timingSafeEqual(supplied, expectedToken);
  };
  const streams = new WebSocketServer({ noServer: true, maxPayload: 1024 * 1024 });
  const server = createServer(async (request, response) => {
    response.setHeader('content-type', 'application/json');
    const supplied = Buffer.from(String(request.headers['x-sentinel-desktop-token'] || ''));
    if (supplied.length !== expectedToken.length || !timingSafeEqual(supplied, expectedToken)) {
      response.writeHead(401).end(JSON.stringify({ error: 'Unauthorized' })); return;
    }
    if (request.method !== 'POST' || !['/v1/request', '/v1/notifications'].includes(request.url || '')) {
      response.writeHead(404).end(JSON.stringify({ error: 'Not found' })); return;
    }
    try {
      const chunks: Buffer[] = [];
      let length = 0;
      for await (const chunk of request) {
        length += chunk.length;
        if (length > 1024 * 1024) {
          response.writeHead(413).end(JSON.stringify({ error: 'Request too large' })); return;
        }
        chunks.push(chunk);
      }
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (request.url === '/v1/notifications') {
        if (!notifications) throw new Error('Notifications unavailable');
        response.end(JSON.stringify(notifications.publish(payload))); return;
      }
      const { action, ...values } = payload;
      if (!actions.has(action)) {
        response.writeHead(400).end(JSON.stringify({ error: 'Unknown runtime action' })); return;
      }
      let reply;
      if (action === 'remote_register') {
        if (!/^[0-9a-f-]{36}$/i.test(values.machine) || typeof values.socket !== 'string' || !values.socket.startsWith('/tmp/sentinel-remote-') || values.bridge !== path.join(path.dirname(values.socket), 'bridge.sock')) throw new Error('Invalid remote runtime tunnel');
        const existing = remotes.get(values.machine);
        if (existing && existing.socket === values.socket) {
          await existing.runtime.start();
          reply = { socket: existing.bridge.socketPath };
        } else {
          if (existing) { await existing.bridge.close(); await existing.runtime.stop(); remotes.delete(values.machine); }
          const remoteRuntime = new WorkspaceRuntime({ remote: true, command: '/usr/bin/nc', args: ['-U', values.socket], onProgress: () => {}, onFailure: () => {}, log: () => {} });
          try {
            await remoteRuntime.start();
            const bridge = await openWorkspaceRuntimeBridge(remoteRuntime, values.bridge, token, lifecycle, notifications, new WorkerClient(remoteRuntime));
            remotes.set(values.machine, { socket: values.socket, bridge, runtime: remoteRuntime });
            reply = { socket: bridge.socketPath };
          } catch (error) { await remoteRuntime.stop(); throw error; }
        }
      }
      else if (worker) reply = await worker.request(action, values);
      else if (action === 'runtime_migration_inputs') reply = await runtimeMigrationInputs((await runtime.deployment(false)).root, values.machine, values.references);
      else if (action === 'deployment') reply = await runtime.deployment(values.prepare !== false);
      else if (action === 'status') reply = await lifecycle.overview();
      else if (action === 'recover') { await lifecycle.recover(values.workspace); reply = { state: 'recovering' }; }
      else if (action === 'display_start') {
        if (lifecycle.status().states[values.workspace]?.state !== 'running') throw new Error('Start the workspace before its desktop');
        reply = await runtime.request(action, values);
      }
      else if (action === 'prepare' || action === 'reinstall') {
        if (action === 'reinstall' && values.confirmed !== true) throw new Error('Confirm erasing the workspace Linux disk before reinstalling');
        await lifecycle.prepare(values.workspace, values.project, values.tools || [], action === 'reinstall', values.resources, values.notificationContext, values.distribution, values.desktop, values.browser);
        reply = { state: 'preparing' };
      } else if (action === 'stop' || action === 'delete') {
        await lifecycle.stop(values.workspace, action === 'delete');
        reply = {};
      } else reply = await runtime.request(action, values, 31 * 60_000);
      response.end(JSON.stringify(reply));
    } catch (error) {
      response.writeHead(error instanceof RuntimeCompatibilityError ? 409 : error instanceof SyntaxError ? 400 : 502)
        .end(JSON.stringify({ error: error instanceof Error ? error.message : String(error),
          ...(error instanceof RuntimeCompatibilityError ? { code: error.code, details: error.details } : {}) }));
    }
  });
  server.on('upgrade', (request, socket, head) => {
    if (request.url !== '/v1/process' || !authorized(request.headers['x-sentinel-desktop-token'])) {
      socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); return;
    }
    streams.handleUpgrade(request, socket, head, ws => streams.emit('connection', ws));
  });
  streams.on('connection', ws => {
    const process = randomUUID();
    let workspace: string | undefined;
    let chain = Promise.resolve();
    const send = (value: unknown) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > 8 * 1024 * 1024) { ws.close(1011, 'Terminal consumer is too slow'); return; }
      ws.send(JSON.stringify(value));
    };
    const output = (reply: unknown) => send(reply);
    const closed = () => ws.close(1011, 'Workspace runtime stopped');
    runtime.events.on(process, output);
    runtime.events.on('closed', closed);
    ws.on('message', message => {
      // Serialize control messages so input, resize and EOF preserve their order.
      chain = chain.then(async () => {
        const value = JSON.parse(message.toString());
        if (!workspace) {
          if (value.action !== 'start' || typeof value.workspace !== 'string' || !Array.isArray(value.arguments)) throw new Error('Expected process start');
          workspace = value.workspace;
          await runtime.request('process_start', { ...value, workspace, process });
          send({ event: 'started' });
        } else {
          const actions: Record<string, string> = { input: 'process_input', resize: 'process_resize', stop: 'process_stop' };
          if (!actions[value.action]) throw new Error('Unknown process action');
          await runtime.request(actions[value.action], { ...value, workspace, process });
        }
      }).catch(error => { send({ event: 'exit', error: String(error) }); ws.close(1011); });
    });
    ws.on('close', () => {
      runtime.events.off(process, output);
      runtime.events.off('closed', closed);
      void chain.finally(() => {
        if (workspace && runtime.isReady) void runtime.request('process_stop', { workspace, process }, 10_000).catch(() => {});
      });
    });
  });
  server.headersTimeout = 10_000;
  server.requestTimeout = 15_000;
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(socketPath, () => { server.off('error', reject); resolve(); });
  });
  await chmod(socketPath, 0o600);
  return {
    socketPath,
    close: async () => {
      for (const remote of remotes.values()) { await remote.bridge.close(); await remote.runtime.stop(); }
      remotes.clear();
      return new Promise<void>((resolve, reject) => {
      for (const client of streams.clients) client.terminate();
      streams.close();
      server.closeAllConnections();
      server.close(error => error ? reject(error) : resolve());
      });
    },
  };
}
