// Run against a disposable workspace store, never a user's active terminal.
// Usage: node apps/desktop/sentinel/tests/integration/workspace-pty.mjs STATE_ROOT PROJECT WORKSPACE_UUID
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
process.chdir(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..'));
import { randomUUID } from 'node:crypto';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
const [root, project, workspace] = process.argv.slice(2);
if (!root || !project || !workspace) throw new Error('Expected disposable STATE_ROOT PROJECT WORKSPACE_UUID');
const resources = path.resolve('apps/desktop/sentinel/build/macos-arm64/runtime/workspace-runtime');
const manifest = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const kernel = path.join(homedir(), 'Library/Application Support/Sentinel Dev/state/workspace-runtime/kernels', manifest.kernelFileSha256, 'kernel');
const runtime = new WorkspaceRuntime({ command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, kernel, manifest.initImage, manifest.workspaceImage], onProgress: console.log, onFailure: console.error, log: console.error });
const socket = `/tmp/sentinel-pty-test-${randomUUID()}.sock`;
async function exec(arguments_) {
  const result = await runtime.request('exec', { workspace, arguments: arguments_, timeout: 20 });
  assert.equal(result.exitCode, 0, result.stderr || result.error);
  return result.stdout;
}
async function attach() {
  const id = randomUUID();
  let output = '';
  let failure;
  const listener = event => {
    if (event.data) output += Buffer.from(event.data, 'base64').toString();
    if (event.error) failure = event.error;
  };
  runtime.events.on(id, listener);
  await runtime.request('process_start', { workspace, process: id, terminal: true, cols: 100, rows: 30,
    arguments: ['tmux', '-S', socket, 'attach-session', '-t', 'smoke'] });
  return { id, async see(text) {
    for (let i = 0; i < 100; i++) {
      if (failure) throw new Error(failure);
      if (output.includes(text)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.fail(`PTY did not show ${text}: ${output.slice(-500)}`);
  }, async close() {
    await runtime.request('process_stop', { workspace, process: id });
    runtime.events.off(id, listener);
  } };
}
try {
  await runtime.start();
  await runtime.request('start', { workspace, project, cpus: 2, memory_gib: 2, disk_gib: 32 });
  const installed = await runtime.request('exec', { workspace,
    arguments: ['sh', '-ec', 'apk info -e tmux bash >/dev/null 2>&1 || apk add --no-cache tmux bash'], timeout: 120 });
  assert.equal(installed.exitCode, 0, installed.stderr);
  await exec(['touch', '/run/sentinel-restart-probe']);
  await runtime.request('stop', { workspace });
  await runtime.request('start', { workspace, project, cpus: 2, memory_gib: 2, disk_gib: 32 });
  await exec(['sh', '-ec', 'test ! -e /run/sentinel-restart-probe; command -v tmux; for i in $(seq 1 5); do timeout 2 docker info >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1']);
  await exec(['tmux', '-S', socket, 'new-session', '-d', '-s', 'smoke', 'bash --noprofile --norc']);
  const first = await attach();
  const command = "printf 'OUT_%s\\n' READY; printf 'ERR_%s\\n' READY >&2\r";
  await runtime.request('process_input', { workspace, process: first.id, data: Buffer.from(command).toString('base64') });
  await first.see('OUT_READY');
  await first.see('ERR_READY');
  await runtime.request('process_resize', { workspace, process: first.id, cols: 120, rows: 35 });
  // tmux reserves one terminal row for its status bar.
  assert.match(await exec(['tmux', '-S', socket, 'display-message', '-p', '#{window_width}x#{window_height}']), /120x34/);
  await first.close();
  const second = await attach();
  await second.see('OUT_READY');
  await second.see('ERR_READY');
  await second.close();
  console.log('PASS: PTY input, stdout/stderr, resize, detach and reconnect with preserved output');
} finally {
  if (runtime.isReady) await exec(['tmux', '-S', socket, 'kill-server']).catch(() => {});
  await runtime.stop();
}
