// Owns a disposable Ubuntu GNOME VM; never changes an existing workspace.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, readFile, copyFile, cp, realpath, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = process.env.SENTINEL_RUNTIME_RESOURCES || path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const manifest = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const root = await realpath(await mkdtemp('/tmp/sentinel-cursor-'));
const project = path.join(root, 'project');
await mkdir(project);
await copyFile(path.join(desktop, 'tests/fixtures/desktop-cursor.py'), path.join(project, 'cursor.py'));
if (process.env.SENTINEL_DISTRO_IMAGE_CACHE) {
  const store = path.join(root, 'runtime/store');
  await mkdir(store, { recursive: true });
  for (const name of ['content', 'state.json', 'initfs.ext4']) {
    await cp(path.join(process.env.SENTINEL_DISTRO_IMAGE_CACHE, 'store', name), path.join(store, name), { recursive: true });
  }
}
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({ command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [path.join(root, 'runtime'), path.join(resources, 'kernel'), manifest.initImage],
  log: console.error, onProgress: console.log, onFailure: console.error });
async function exec(arguments_, timeout = 60) {
  const result = await runtime.request('exec', { workspace, arguments: arguments_, timeout }, (timeout + 30) * 1000);
  assert.equal(result.exitCode, 0, result.stdout + '\n' + result.stderr);
  return result.stdout;
}
try {
  await runtime.start();
  await runtime.request('start', { workspace, project, distribution: 'ubuntu', cpus: 4, memory_gib: 4, disk_gib: 16 }, 600000);
  console.log('Installing GNOME in disposable Ubuntu VM');
  await runtime.request('graphics_install', { workspace, desktop: 'gnome', distribution: 'ubuntu' }, 1200000);
  await exec(['apt-get', 'install', '-y', '--no-install-recommends', 'gir1.2-gtk-3.0', 'python3-gi-cairo', 'python3-pil'], 300);
  await runtime.request('display_start', { workspace, width: 1280, height: 800 }, 90000);
  const session = JSON.parse(await exec(['python3', '/opt/sentinel/desktop/desktop-session.py',
    JSON.stringify({ action: 'start', geometry: '1280x800' })], 90));
  assert.equal(session.state, 'running');
  for (const backend of ['wayland', 'x11']) {
    console.log('Testing guest cursor:', backend);
    const result = JSON.parse(await exec(['python3', path.join(project, 'cursor.py'), backend], 75));
    assert.equal(result.guest_cursor_pixels, true);
    assert.equal(result.active_cursor_plane, true);
    console.log('PASS', result);
  }
  const logs = await exec(['journalctl', '-b', '--no-pager', '_COMM=gnome-shell']);
  assert.match(logs, /using atomic mode setting/);
  assert.doesNotMatch(logs, /drmModeSetCursor failed|Page flip failed|Disabling hardware cursors/);
  const state = await exec(['cat', '/sys/kernel/debug/dri/0/state']);
  assert.equal((state.match(/^plane\[/gm) || []).length, 2, 'Guest must expose primary and cursor planes');
  console.log('PASS stock GNOME atomic display and native cursor plane');
} catch (error) {
  if (runtime.isReady) {
    console.error(await exec(['sh', '-c', 'journalctl -b --no-pager _COMM=gnome-shell | tail -40; cat /sys/kernel/debug/dri/0/state'], 15).catch(String));
  }
  throw error;
} finally {
  try { if (runtime.isReady) await runtime.request('delete', { workspace }, 60000); }
  finally { await runtime.stop(); await rm(root, { recursive: true, force: true }); }
}
