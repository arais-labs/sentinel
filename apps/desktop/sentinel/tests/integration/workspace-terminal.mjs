import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
process.chdir(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..'));
import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceLifecycle } from '../../.test-dist/main/workspace/workspaceLifecycle.js';
import { openWorkspaceRuntimeBridge } from '../../.test-dist/main/transport/workspaceRuntimeBridge.js';
const [root, project, workspace] = process.argv.slice(2);
if (!root || !project || !workspace) throw new Error('Expected disposable STATE_ROOT PROJECT WORKSPACE_UUID');
const resources = path.resolve('apps/desktop/sentinel/build/macos-arm64/runtime/workspace-runtime');
const manifest = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const kernel = path.join(homedir(), 'Library/Application Support/Sentinel Dev/state/workspace-runtime/kernels', manifest.kernelFileSha256, 'kernel');
const runtime = new WorkspaceRuntime({ command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, kernel, manifest.initImage], onProgress: console.log, onFailure: console.error, log: console.error });
const lifecycle = new WorkspaceLifecycle(runtime, root);
const temporary = await mkdtemp(path.join(tmpdir(), 'sentinel-bridge-'));
const token = randomBytes(32).toString('hex');
const bridge = await openWorkspaceRuntimeBridge(runtime, path.join(temporary, 'runtime.sock'), token, lifecycle);
try {
  await lifecycle.load();
  await lifecycle.prepare(workspace, project, []);
  for (let i = 0; i < 120; i++) {
    const state = lifecycle.status().states[workspace];
    if (state.state === 'failed') throw new Error(state.error);
    if (state.state === 'running') break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert.equal(lifecycle.status().states[workspace].state, 'running');
  const child = spawn(path.resolve('apps/backend/sentinel/.venv/bin/python'), [path.resolve('apps/backend/sentinel/tests/integration/workspace_terminal.py')], {
    cwd: path.resolve('apps/backend/sentinel'), stdio: 'inherit',
    env: { ...process.env, PYTHONASYNCIODEBUG: '1', PYTHONPATH: path.resolve('apps/backend/sentinel'), SENTINEL_DESKTOP_TOKEN: token, DATA_ENCRYPTION_KEY: randomBytes(32).toString('hex'), SENTINEL_STORAGE_ROOT: temporary, TEST_RUNTIME_SOCKET: bridge.socketPath, TEST_RUNTIME_TOKEN: token, TEST_PROJECT: project, TEST_WORKSPACE: workspace },
  });
  assert.equal(await new Promise(resolve => child.on('exit', resolve)), 0);
} finally {
  await bridge.close();
  await lifecycle.close();
  await rm(temporary, { recursive: true, force: true });
}
