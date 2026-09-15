// Run after the desktop build and TypeScript compilation. Owns and removes its VM.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const cache = path.join(desktop, 'build/graphics-sources/guest');
const runtimeRoot = await mkdtemp(path.join(cache, 'integration-'));
const project = await mkdtemp('/tmp/sentinel-graphics-install-');
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({
  command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [runtimeRoot, path.join(cache, config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: message => console.log(message), onFailure: console.error, log: () => {},
});
const graphics = new WorkspaceGraphics(runtime, path.join(resources, 'graphics'));
try {
  await runtime.start();
  await runtime.request('start', { workspace, project, cpus: 2, memory_gib: 2, disk_gib: 16 });
  const dependencies = await runtime.request('exec', { workspace, arguments: ['apk', 'add', '--no-cache', 'python3', 'xz', 'mesa-egl', 'mesa-dri-gallium', 'libxshmfence', 'libdrm', 'zstd-libs', 'expat', 'libx11', 'libxext', 'libxcb'], timeout: 120 });
  assert.equal(dependencies.exitCode, 0, dependencies.stderr);
  let started = performance.now();
  await graphics.install(workspace);
  console.log(`Precompiled installation: ${((performance.now() - started) / 1000).toFixed(2)}s`);
  const check = await runtime.request('exec', { workspace, arguments: ['sh', '-c', 'test -s /opt/sentinel/graphics/bundle.sha256 && test -s /opt/sentinel/graphics/lib/libEGL.so.1 && ! command -v cc && ! command -v meson'], timeout: 5 });
  assert.equal(check.exitCode, 0, check.stdout + check.stderr);
  started = performance.now();
  await graphics.install(workspace);
  console.log(`Existing installation: ${((performance.now() - started) / 1000).toFixed(2)}s`);
} finally {
  await graphics.close();
  try {
    if (runtime.isReady) await runtime.request('delete', { workspace });
  } finally {
    await runtime.stop();
    await rm(project, { recursive: true, force: true });
    await rm(runtimeRoot, { recursive: true, force: true });
  }
}
