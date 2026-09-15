// Real apt desktop + Metal rendering; owns and deletes only new disposable UUIDs.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, symlink, copyFile, cp } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = process.env.SENTINEL_RUNTIME_RESOURCES || path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const parent = await mkdtemp('/tmp/sentinel-distro-desktop-');
const root = path.join(parent, 'runtime');
const project = path.join(parent, 'project');
await mkdir(project);
if (process.env.SENTINEL_DISTRO_IMAGE_CACHE) {
  const store = path.join(root, 'store');
  await mkdir(store, { recursive: true });
  for (const name of ['content', 'state.json', 'initfs.ext4']) await cp(path.join(process.env.SENTINEL_DISTRO_IMAGE_CACHE, 'store', name), path.join(store, name), { recursive: true });
}
await symlink(path.join(resources, 'graphics'), path.join(parent, 'graphics'));
const runtime = new WorkspaceRuntime({ command: path.join(resources, 'sentinel-workspace-runtime'), args: [root,
  process.env.SENTINEL_DISTRO_KERNEL || path.join(desktop, 'build/graphics-sources/guest', config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: console.log, onFailure: console.error, log: () => {} });
const graphics = new WorkspaceGraphics(runtime, path.join(resources, 'graphics'));
const control = await readFile(path.join(desktop, '../../backend/sentinel/app/services/runtime/guest_commands/linux/desktop/control.sh'), 'utf8');
await copyFile(path.join(desktop, 'tests/fixtures/graphics-render.c'), path.join(project, 'graphics-render.c'));
const ids = [];
try {
  await runtime.start();
  for (const distribution of (process.env.SENTINEL_DISTRO_TEST_DISTRIBUTIONS || 'ubuntu,debian').split(',')) {
    const workspace = randomUUID(); ids.push(workspace);
    const exec = async (args, timeout = 120) => {
      const result = await runtime.request('exec', { workspace, arguments: args, timeout }, (timeout + 30) * 1000);
      assert.equal(result.exitCode, 0, result.stdout + '\n' + result.stderr);
      return result.stdout;
    };
    await runtime.request('start', { workspace, project, distribution, cpus: 2, memory_gib: 4, disk_gib: 20 }, 360_000);
    for (const step of workspaceSetupSteps(['desktop'], { distribution })) {
      console.log(distribution, step.message);
      console.log(await exec(step.arguments, step.timeout));
    }
    if (process.env.SENTINEL_WAIT_GRAPHICS) {
      const manifest = path.join(resources, 'graphics/mesa-linux-arm64-glibc.json');
      let ready = false;
      for (let i = 0; i < 900; i++) {
        try { JSON.parse(await readFile(manifest, 'utf8')); ready = true; break; } catch {}
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      assert.ok(ready, 'Timed out waiting for concurrent graphics build');
    }
    await graphics.install(workspace, distribution);
    await graphics.install(workspace, distribution); // reuse same verified ABI bundle
    await graphics.start(workspace);
    const started = JSON.parse(await exec(['bash', '-c', control, 'sentinel', JSON.stringify({ action: 'start', geometry: '1280x800' })]));
    assert.equal(started.ok, true, JSON.stringify(started));
    assert.equal(started.state, 'running');
    // Test compiler is installed only by this integration harness, never workspace setup.
    await exec(['sh', '-ec', 'apt-get install -y --no-install-recommends gcc libc6-dev libgl-dev libx11-dev'], 300);
    await exec(['cc', path.join(project, 'graphics-render.c'), '-o', '/tmp/graphics-render', '-lGL', '-lX11']);
    const env = 'DISPLAY=:1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=virpipe VTEST_SOCKET_NAME=/run/sentinel-graphics/renderer.sock LIBGL_DRIVERS_PATH=/opt/sentinel/graphics/lib/dri';
    console.log(distribution, await exec(['sh', '-ec', env + ' /tmp/graphics-render 640 480']));
    // No LD_LIBRARY_PATH here: Chromium GPU children also need the registered loader.
    await graphics.close();
    console.log('After viewer close:', await exec(['sh', '-ec', env + ' /tmp/graphics-render 640 480']));
    const smoke = await exec(['sh', '-ec', "chromium --headless --no-sandbox --disable-dev-shm-usage --dump-dom 'data:text/html,<title>desktop-ready</title>'"]);
    assert.match(smoke, /desktop-ready/);
    const webgl = `
(async()=>{
 const {chromium}=require('/opt/sentinel/browser-driver/node_modules/playwright');
 const browser=await chromium.launch({executablePath:'/usr/local/bin/chromium',headless:false,
  args:['--use-gl=angle','--use-angle=gles-egl','--disable-vulkan','--disable-software-rasterizer','--ignore-gpu-blocklist'],
  env:{...process.env,DISPLAY:':1',LIBGL_ALWAYS_SOFTWARE:'1',GALLIUM_DRIVER:'virpipe',VTEST_SOCKET_NAME:'/run/sentinel-graphics/renderer.sock',LIBGL_DRIVERS_PATH:'/opt/sentinel/graphics/lib/dri'}});
 try {const page=await browser.newPage();console.log(await page.evaluate(()=>{const g=document.createElement('canvas').getContext('webgl');return g?g.getParameter(g.getExtension('WEBGL_debug_renderer_info').UNMASKED_RENDERER_WEBGL):'NO WEBGL';}));}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
`;
    const webglResult = await exec(['node', '-e', webgl]);
    assert.match(webglResult, /virgl/i, webglResult);
    console.log('Chromium WebGL:', webglResult);
    await runtime.request('delete', { workspace });
    console.log('PASS accelerated desktop + distro loader:', distribution);
  }
} finally {
  for (const workspace of ids) if (runtime.isReady) await runtime.request('delete', { workspace }).catch(() => {});
  await runtime.stop();
  await rm(parent, { recursive: true, force: true });
}
