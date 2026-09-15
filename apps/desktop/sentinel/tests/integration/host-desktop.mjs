// Owns a disposable VM. Exercises native host graphics and raw desktop transport.
import assert from 'node:assert/strict';
import { randomUUID, randomBytes, createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { createConnection } from 'node:net';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const root = await mkdtemp('/tmp/sentinel-host-test-' + 'x'.repeat(45));
const forwardRoot = '/tmp/sentinel-forward-' + createHash('sha256').update(root).digest('hex').slice(0, 16);
const project = await mkdtemp('/tmp/sentinel-host-project-');
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({
  command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, path.join(desktop, 'build/graphics-sources/guest', config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: console.log, onFailure: console.error, log: () => {},
});
const graphics = new WorkspaceGraphics(runtime, path.join(resources, 'graphics'));
const commands = [];
const originalRequest = runtime.request.bind(runtime);
runtime.request = (action, ...args) => { commands.push(action); return originalRequest(action, ...args); };
async function exec(arguments_, timeout = 120) {
  const reply = await runtime.request('exec', { workspace, arguments: arguments_, timeout });
  assert.equal(reply.exitCode, 0, reply.stderr || reply.stdout);
  return reply.stdout;
}
try {
  await runtime.start();
  await runtime.request('start', { workspace, project, cpus: 2, memory_gib: 2, disk_gib: 16 });
  await exec(['apk', 'add', '--no-cache', 'python3', 'xz', 'socat', 'tigervnc', 'build-base', 'mesa-dev', 'mesa-egl', 'mesa-dri-gallium', 'libxshmfence', 'libdrm', 'zstd-libs', 'expat', 'libx11-dev', 'libxext', 'libxcb']);
  await graphics.install(workspace);
  const before = commands.length;
  await graphics.start(workspace);
  await graphics.start(workspace); // idempotent: must preserve existing GL clients
  assert.deepEqual(commands.slice(before), ['graphics_start', 'graphics_start']);
  await runtime.request('process_start', { workspace, process: randomUUID(), arguments: ['Xvnc', ':1', '-geometry', '1280x800', '-depth', '24', '-localhost', 'yes', '-SecurityTypes', 'None', '-rfbport', '5901', '-AlwaysShared', '-nolisten', 'tcp'] });
  const source = (await readFile(path.join(desktop, 'tests/fixtures/graphics-render.c'))).toString('base64');
  await exec(['python3', '-c', `import base64;open("/tmp/graphics-render.c","wb").write(base64.b64decode("${source}"))`]);
  await exec(['cc', '/tmp/graphics-render.c', '-o', '/tmp/graphics-render', '-lGL', '-lX11']);
  const render = ['sh', '-c', 'DISPLAY=:1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=virpipe VTEST_SOCKET_NAME=/run/sentinel-graphics/renderer.sock LD_LIBRARY_PATH=/opt/sentinel/graphics/lib /tmp/graphics-render 1280 800'];
  console.log(await exec(render));
  await graphics.close(); // viewer teardown must not stop host rendering
  console.log('After viewer close:', await exec(render));

  const { socket } = await runtime.request('port_forward', { workspace, port: 5901 });
  assert.equal(path.dirname(socket), forwardRoot);
  assert.ok(Buffer.byteLength(socket) < 104);
  assert.equal((await stat(socket)).mode & 0o777, 0o600);
  const client = createConnection(socket);
  await once(client, 'connect');
  assert.match((await once(client, 'data'))[0].toString(), /^RFB /);
  client.destroy();
  console.log('VNC greeting received through private binary socket');

  const echo = 'import socket\ns=socket.socket();s.bind(("127.0.0.1",5902));s.listen()\nwhile True:\n c,_=s.accept()\n with c:\n  while True:\n   b=c.recv(32768)\n   if not b:break\n   c.sendall(b)';
  await runtime.request('process_start', { workspace, process: randomUUID(), arguments: ['python3', '-u', '-c', echo] });
  await exec(['python3', '-c', 'import socket,time\nfor i in range(100):\n try:s=socket.create_connection(("127.0.0.1",5902));s.close();break\n except OSError:time.sleep(.02)\nelse:raise RuntimeError("echo not ready")']);
  const forward = await runtime.request('port_forward', { workspace, port: 5902 });
  const payload = randomBytes(1024 * 1024), chunks = [];
  const connection = createConnection(forward.socket);
  connection.on('data', chunk => chunks.push(chunk));
  await once(connection, 'connect');
  const ended = once(connection, 'end');
  const started = performance.now();
  connection.end(payload);
  await ended;
  assert.deepEqual(Buffer.concat(chunks), payload);
  console.log(`Binary echo: 1 MiB preserved including half-close, ${(performance.now() - started).toFixed(1)} ms`);
  await graphics.stop(workspace);
  await runtime.request('stop', { workspace });
  await assert.rejects(stat(socket), { code: 'ENOENT' });
  await assert.rejects(stat(forward.socket), { code: 'ENOENT' });
  console.log('Host graphics, viewer disconnect, binary forwarding and cleanup passed');
} finally {
  if (runtime.isReady) await runtime.request('delete', { workspace }).catch(console.error);
  await runtime.stop();
  await rm(project, { recursive: true, force: true });
  await rm(root, { recursive: true, force: true });
  await rm(forwardRoot, { recursive: true, force: true });
}
