// Opt-in disposable VM test: node tests/integration/host-desktop.mjs
// SENTINEL_TEST_DESKTOP=xfce|lxqt|gnome|plasma|weston selects the desktop.
// SENTINEL_TEST_DISTRIBUTION=alpine|ubuntu|debian selects its pinned base image.
// SENTINEL_TEST_BROWSER=chromium|firefox|chrome selects production browser setup.
// Each invocation qualifies one disposable combination; an install or runtime
// failure is a failed combination, not evidence of support from package names.
// SENTINEL_DESKTOP_SCREENSHOT=/tmp/desktop.png captures the real disposable desktop.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { cp, mkdtemp, readFile, realpath, rm, stat, writeFile } from 'node:fs/promises';
import { createConnection } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = path.resolve(process.env.SENTINEL_TEST_RUNTIME ||
  path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime'));
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const selection = process.env.SENTINEL_TEST_DESKTOP || 'xfce';
const distribution = process.env.SENTINEL_TEST_DISTRIBUTION || 'alpine';
const browser = process.env.SENTINEL_TEST_BROWSER || 'chromium';
// Production setup validates unsupported selections before any runtime is started.
const browserSetup = workspaceSetupSteps([], {distribution, browser});
const automationBrowser = browser === 'chrome' ? 'chrome' : 'chromium';
// Explicit diagnostic mode records the stock Plasma startup selection race but
// never turns it into a passing qualification or catches unrelated failures.
const diagnoseClipboard = process.env.SENTINEL_DESKTOP_DIAGNOSE_CLIPBOARD === '1';
assert.ok(!diagnoseClipboard || selection === 'plasma','Clipboard diagnostic mode is Plasma-only');
assert.ok(['xfce', 'weston', 'lxqt', 'gnome', 'plasma'].includes(selection), `Unknown desktop: ${selection}`);
assert.ok(['alpine', 'ubuntu', 'debian'].includes(distribution), `Unknown distribution: ${distribution}`);
const previewCommands = {
  xfce: [['thunar'], ['xfce4-appfinder']],
  weston: [['thunar'], ['xfce4-appfinder']],
  lxqt: [['pcmanfm-qt'], ['lxqt-runner']],
  gnome: [['nautilus']],
  plasma: [['dolphin']],
};
// Exercise Sentinel's actual first-run appearance contract, not a second test
// theme. XFCE integration also requires the repository's backend virtualenv.
const backend = path.resolve(desktop, '../../backend/sentinel');
const defaults = selection === 'xfce' ? JSON.parse(execFileSync(path.join(backend, '.venv/bin/python'), ['-c',
  'import json; from app.services.runtime.desktop_appearance import desktop_default_files; print(json.dumps(desktop_default_files()))',
], {cwd:backend,encoding:'utf8'})) : {};
const root = await mkdtemp('/tmp/sentinel-display-test-');
// macOS /tmp aliases /private/tmp. Share the physical path so Linux's own
// native /tmp mount cannot mask this disposable test project.
const project = await realpath(await mkdtemp('/tmp/sentinel-display-project-'));
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({
  command: path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, path.join(resources, 'kernel'), config.initImage],
  onProgress: console.log, onFailure: console.error, log: console.error,
});
const graphics = new WorkspaceGraphics(runtime);
const contractFailures = [];
let interrupted = false;
let stopping;
const stopRuntime = () => stopping ??= runtime.stop();
function interrupt() {
  if (interrupted) return;
  interrupted = true;
  process.exitCode = 130;
  // Closing the owned runtime rejects pending operations and tears down its VM.
  // Do not process.exit(): the enclosing finally must remove disposable files.
  void stopRuntime().catch(console.error);
}
process.on('SIGINT', interrupt);
process.on('SIGTERM', interrupt);
async function exec(arguments_, timeout = 120) {
  const reply = await runtime.request('exec', { workspace, arguments: arguments_, timeout }, (timeout + 30) * 1000);
  assert.equal(reply.exitCode, 0, reply.stderr || reply.stdout);
  return reply.stdout;
}
async function session(action, geometry='1280x800') {
  return JSON.parse(await exec(['python3', '/opt/sentinel/desktop/desktop-session.py', JSON.stringify({action,geometry,
    ...(action === 'start' && selection === 'xfce' ? {defaults} : {}),
  })], 90));
}
async function fixture(name, args=[], timeout=30) {
  const source = (await readFile(path.join(desktop, 'tests/fixtures', name))).toString('base64');
  const loader = 'import base64,sys;name=sys.argv.pop(1);source=sys.argv.pop(1);sys.path.insert(0,"/opt/sentinel/desktop");exec(compile(base64.b64decode(source),name,"exec"))';
  return exec(['python3','-c',loader,name,source,...args],timeout);
}
async function nativeSession(dimensions) {
  if (selection === 'weston') return null; // Explicit internal non-login lab path.
  const result = JSON.parse(await fixture('desktop-native-contract.py',['running']));
  assert.equal(result.login.Type, selection === 'xfce' ? 'x11' : 'wayland');
  assert.deepEqual([result.output.width,result.output.height],dimensions);
  assert.equal(result.output.geometry_met,true);
  assert.ok(Number.isFinite(result.output.refresh_hz) && result.output.refresh_hz > 0);
  assert.equal(result.output.refresh_met,true);
  assert.equal(result.output.target_met,true);
  assert.ok(Math.abs(result.output.refresh_hz - result.output.requested_refresh_hz) < 0.1,
    `Virtual display did not select its advertised target mode: ${JSON.stringify(result.output)}`);
  console.log('Real native login and selected display mode:',result);
  return result;
}
async function stream(socketPath) {
  const socket = createConnection(socketPath);
  let pending = Buffer.alloc(0), dimensions, audio = 0, frames = 0;
  const timer = setTimeout(() => socket.destroy(new Error(
    `Desktop stream readiness timed out: ${JSON.stringify({dimensions, frames, audio, pendingBytes: pending.length})}`,
  )), 10000);
  try {
    for await (const chunk of socket) {
      pending = Buffer.concat([pending, chunk]);
      while (pending.length >= 16) {
        const size = pending.readUInt32BE(4);
        assert.ok(size <= 4 * 1024 * 1024);
        if (pending.length < size + 16) break;
        const type = pending[0], body = pending.subarray(16,16+size);
        if (type === 1) dimensions = [body.readUInt32BE(0),body.readUInt32BE(4)];
        if (type === 2) { assert.ok(dimensions); frames++; }
        if (type === 5) { assert.ok(body.length > 0 && body.length <= 1275); audio++; }
        pending = pending.subarray(16+size);
      }
      if (frames && audio >= 3) return {dimensions,frames,audio};
    }
    throw new Error('Desktop disconnected');
  } finally { clearTimeout(timer); socket.destroy(); }
}
try {
  console.log('Qualifying disposable desktop combination:', {desktop:selection,distribution,browser});
  await runtime.start();
  await runtime.request('start', {workspace,project,distribution,cpus:4,memory_gib:4,disk_gib:16},600000);
  const firstBoot = (await exec(['cat','/proc/sys/kernel/random/boot_id'])).trim();
  // Validate the shipped VM mount namespace, not just kernel build options.
  // Native init may skip securityfs when it detects a container environment.
  assert.equal((await exec(['sh', '-ec',
    'test "$(cat /sys/module/apparmor/parameters/enabled)" = Y; test -d /sys/kernel/security/apparmor; printf ready',
  ])).trim(), 'ready', 'AppArmor must be enabled and visible to workspace services');
  const init = (await exec(['cat', '/proc/1/comm'])).trim();
  assert.equal(init, distribution === 'alpine' ? 'openrc-init' : 'systemd');
  await exec(['sh', '-ec', 'test -S /run/dbus/system_bus_socket; test -S /run/udev/control; loginctl list-seats; ! command -v docker; ! command -v dockerd; ! command -v k3s']);
  console.log('Native init/system services ready, optional stacks absent:', init);
  // Test-only applications; these are not additions to the desktop bundle.
  if (distribution === 'alpine') {
    await exec(['apk','add','--no-cache','python3','py3-gobject3','py3-cairo','py3-pillow','gtk+3.0'],180);
  } else {
    await exec(['apt-get','-o','Acquire::Retries=3','-o','APT::Update::Error-Mode=any','update'],180);
    await exec(['env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','--no-install-recommends',
      'python3','python3-gi','python3-gi-cairo','python3-cairo','python3-pil','gir1.2-gtk-3.0','libglib2.0-bin'],300);
  }
  console.log('Actual disposable guest release:', await exec(['cat','/etc/os-release']));
  console.log('Kernel namespace contract:', await fixture('namespace-contract.py'));
  await graphics.install(workspace,selection,distribution);
  const reinstallStarted = performance.now();
  await graphics.install(workspace,selection,distribution);
  console.log('Verified graphics installation reuse (ms):', performance.now() - reinstallStarted);
  await exec(['sh', '-ec', 'test -s /opt/sentinel/graphics/bundle.sha256; ! command -v cc; ! command -v meson']);
  if (distribution === 'ubuntu' && process.env.SENTINEL_TEST_SNAP_CACHE) {
    const cache = path.join(project, 'snap-download-cache');
    await cp(path.resolve(process.env.SENTINEL_TEST_SNAP_CACHE), cache, {recursive:true, dereference:false});
    console.log('Verified Snap download cache:', await fixture('snap-cache.py', [cache, '/var/lib/snapd/cache'], 120));
  }
  for (const step of browserSetup) {
    console.log(step.message);
    await exec(step.arguments, step.timeout);
  }
  if (distribution === 'ubuntu') {
    await runtime.request('browser_graphics_install', {workspace, distribution}, 660000);
  }
  if (selection === 'gnome') console.log('GNOME version:', await exec(['gnome-shell', '--version']));
  if (selection === 'lxqt') {
    console.log('Wayland desktop package versions:', await exec(['sh','-c',
      'labwc --version; Xwayland -version 2>&1; ' + (distribution === 'alpine'
        ? 'apk info -v | sort | sed -n "/^labwc-/p; /^wlroots/p; /^xwayland-/p"'
        : 'dpkg-query -W labwc xwayland "libwlroots*"')]));
  }
  const ownership = (await fixture('desktop-lifecycle.py',['/opt/sentinel/desktop/desktop-session.py'],15)).trim().split('\n').map(line => JSON.parse(line));
  assert.equal(ownership.length,3);
  for (const result of ownership) {
    assert.equal(result.adopted_sets_id_child,true);
    assert.equal(result.lock_released,true);
    assert.equal(result.reaped,true);
  }
  console.log('Owned desktop descendants reaped and locks released:',ownership);
  const {socket} = await runtime.request('display_start',{workspace,width:1280,height:800},60000);
  assert.equal((await stat(socket)).mode & 0o777,0o600);
  assert.equal((await runtime.request('display_start',{workspace,width:1280,height:800})).socket,socket);
  assert.equal((await session('start')).state,'running');
  console.log('Desktop session started:',selection);
  const firstLogin = await nativeSession([1280,800]);
  console.log('Selected desktop browser metadata:', await fixture('browser-desktop.py'));
  // Execute the exact backend workers, not a second browser launch recipe.
  const browserWorkers = [];
  for (const action of ['start', 'stop']) {
    const destination = path.join(project, `browser-${action}.py`);
    await writeFile(destination, await readFile(path.join(backend,
      `app/services/runtime/guest_commands/linux/browser/${action}.py`)));
    browserWorkers.push(destination);
  }
  for (const display of ['', 'native']) {
    console.log('Browser lifecycle qualification:', await fixture(
      'browser-lifecycle.py', [...browserWorkers, display, browser], 180));
  }
  if (selection === 'lxqt' && distribution === 'alpine') {
    console.log('Patched compositor artifact is the actual regular-user session:', await exec(['python3','-c',[
      'import json,os,subprocess',
      'from pathlib import Path',
      'expected="/opt/sentinel/graphics/bin/labwc"',
      'config=json.loads(Path("/etc/sentinel/desktop.json").read_text())',
      'assert config["command"]==[expected,"-S","lxqt-session"], config["command"]',
      'session=json.loads(Path("/run/sentinel-desktop/session.json").read_text()); user=session["user"]',
      'owners=[]',
      'for path in Path("/proc").glob("[0-9]*/exe"):',
      ' try:',
      '  if os.readlink(path)==expected: owners.append(dict(pid=int(path.parent.name),uid=path.parent.stat().st_uid))',
      ' except (FileNotFoundError,ProcessLookupError): pass',
      'assert len(owners)==1 and owners[0]["uid"]==user["uid"]>0, owners',
      'version=subprocess.check_output([expected,"--version"],text=True,env=session["environment"],user=user["uid"],group=user["gid"],extra_groups=user["groups"]).strip()',
      'assert version.startswith("labwc 0.20.0 ") and "wlroots-0.20." in version, version',
      'print(json.dumps(dict(executable=expected,process=owners[0],version=version)))',
    ].join('\n')]));
  }
  console.log('Distro GL dispatch and EGL vendor share the same hardware context:',
    await fixture('desktop-gl-dispatch.py', [distribution === 'alpine' ? 'mesa' : 'glvnd']));
  const account = JSON.parse(await exec(['python3', '-c', [
    'import json,os,subprocess',
    'from pathlib import Path',
    's=json.loads(Path("/run/sentinel-desktop/session.json").read_text())',
    'u=s["user"]; assert u["uid"] > 0 and u["gid"] > 0',
    'p=json.loads(Path("/run/sentinel-desktop/state.json").read_text())["session"]["pid"]',
    'assert Path(f"/proc/{p}").stat().st_uid == u["uid"]',
    'credentials=dict(user=u["uid"],group=u["gid"],extra_groups=u["groups"])',
    'assert subprocess.check_output(["sudo","-n","id","-u"],env=s["environment"],**credentials).strip()==b"0"',
    'subprocess.run(["/opt/sentinel/graphics/bin/sentinel-mapped-memory-check"],check=True,stdout=subprocess.DEVNULL,**credentials)',
    'print(json.dumps(u))',
  ].join('\n')]));
  console.log('Regular desktop UID, sudo access and GPU dirty tracking verified:', account);
  const projectToken = randomUUID();
  const sourceName = `sentinel-host-${projectToken}.txt`, savedName = `sentinel-guest-${projectToken}.txt`;
  await writeFile(path.join(project,sourceName),projectToken,{flag:'wx',mode:0o600});
  // The host directory keeps mkdtemp's 0700 mode. Changing it would hide a
  // broken UID mapping and tell us nothing about ordinary private projects.
  try {
    console.log('Normal GUI user mounted-project access:',await fixture('desktop-native-contract.py',
      ['project',project,sourceName,savedName,projectToken]));
    assert.equal(await readFile(path.join(project,savedName),'utf8'),projectToken,
      'The GUI save must reach the actual mounted host project, not a guest shadow directory');
  } catch (error) {
    const failure = new Error('Normal GUI user mounted-project read/write contract failed', {cause:error});
    contractFailures.push(failure);
    // Keep this a failing qualification, but independently exercise graphics,
    // input, audio, clipboard and restart within the same disposable VM.
    console.error('Independent mounted-project contract failure; permissions unchanged:',failure);
    console.error('Guest project ownership/mode:',await exec(['python3','-c',
      'import json,os,sys;s=os.stat(sys.argv[1]);print(json.dumps(dict(path=sys.argv[1],uid=s.st_uid,gid=s.st_gid,mode=oct(s.st_mode&0o777))))',project]).catch(String));
  }
  if (selection === 'xfce') {
    const appearance = JSON.parse(await exec(['python3','-c',[
      'import json,subprocess,sys',
      'from pathlib import Path',
      's=json.loads(Path("/run/sentinel-desktop/session.json").read_text())',
      'u=s["user"]; home=Path(u["home"])',
      'defaults=json.loads(sys.argv[1])',
      'credentials=dict(user=u["uid"],group=u["gid"],extra_groups=u["groups"])',
      'def panel_property(name):',
      ' return subprocess.check_output(["xfconf-query","-c","xfce4-panel","-p","/plugins/plugin-1/"+name],env=s["environment"],text=True,**credentials).strip()',
      // Whisker Menu 2.9 imports the seed .rc into Xfconf and deletes it.
      // Verify the running user's resulting settings, not retention of input.
      'whisker=".config/xfce4/panel/whiskermenu-1.rc"',
      'menu=dict(line.split("=",1) for line in defaults[whisker].splitlines() if "=" in line)',
      'for key in ("button-title","button-icon","show-button-title"):',
      ' assert panel_property(key)==menu[key], key',
      'favorites=menu["favorites"].split(",")',
      'assert panel_property("favorites").splitlines()[-len(favorites):]==favorites',
      'for relative in defaults:',
      ' if relative==whisker: continue',
      ' p=home/relative; assert p.is_file(), relative',
      ' assert p.stat().st_uid==u["uid"], relative',
      ' assert "__HOME__" not in p.read_text() and "__MONITOR__" not in p.read_text(), relative',
      'wallpaper=home/".config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml"',
      'assert str(home/".local/share/backgrounds/sentinel/default.svg") in wallpaper.read_text()',
      'print(json.dumps({"home":str(home),"files":len(json.loads(sys.argv[1]))}))',
    ].join('\n'),JSON.stringify(defaults)]));
    console.log('Production XFCE defaults belong to the regular desktop user:',appearance);
  }
  const first = await stream(socket);
  assert.deepEqual(first.dimensions,[1280,800]);
  console.log('Initial video and audio received:',first);
  // Reconnect an idle desktop: encoder must produce an IDR without new input.
  assert.deepEqual((await stream(socket)).dimensions,first.dimensions);
  console.log('Idle viewer reconnect passed');
  const result = JSON.parse(await exec(['python3','-c',[
    'import json,sys',
    'sys.path.insert(0,"/opt/sentinel/desktop")',
    'from sentinel_display import Desktop,Clipboard',
    'd=Desktop()',
    'd.execute({"type":"move","x":100,"y":100})',
    'd.execute({"type":"keypress","keys":["Escape"]})',
    'shot=d.screenshot();d.close()',
    'assert shot["screenshot"].startswith("data:image/")',
    'c=Clipboard();text="Bonjour 世界 👋";c.write(text);assert c.read()==text',
    'print(json.dumps({"viewport":shot["viewport"],"cursor":shot["cursor"]}))',
  ].join('\n')]));
  assert.deepEqual(result.viewport,{width:1280,height:800});
  console.log('Computer-control capture and clipboard passed:',result);
  // Deliver test source as an exec argument, not through a host /tmp mount:
  // distro tmpfs overlays and macOS /tmp aliases need not share that namespace.
  const oracle = (await readFile(path.join(desktop,'tests/fixtures/desktop-contract.py'))).toString('base64');
  const loadOracle = 'import base64,sys;source=sys.argv.pop(1);exec(compile(base64.b64decode(source),"desktop-contract.py","exec"))';
  // Every Wayland desktop must support both native clients and ordinary X11
  // clients via its published DISPLAY. XFCE exercises the same X11 oracle.
  async function checkApplication(dimensions) {
    // A fresh stock desktop may open its overview on every login. Dismiss it
    // through real input before testing app coordinates, including after the
    // resize/restart below. Do not change the user's desktop defaults.
    await exec(['python3','-c',
      'import sys;sys.path.insert(0,"/opt/sentinel/desktop");from sentinel_display import Desktop;d=Desktop();d.execute({"type":"keypress","keys":["Escape"]});d.close()']);
    for (const backend of selection !== 'xfce' ? ['wayland','x11'] : ['x11']) {
      const pixels = JSON.parse(await exec(['python3','-c',loadOracle,oracle,backend,
        ...(diagnoseClipboard ? ['--diagnose-clipboard-copy-mismatch'] : [])],75));
      assert.ok(pixels.backend.includes(backend));
      assert.deepEqual(pixels.viewport,dimensions);
      assert.equal(pixels.visible_colors,3);
      assert.equal(pixels.click,true);
      assert.equal(pixels.key,true);
      if (diagnoseClipboard && pixels.clipboard.failure?.kind === 'application-selection-mismatch') {
        assert.equal(pixels.clipboard.application_to_sentinel,false);
        assert.equal(pixels.clipboard.sentinel_to_application,null);
        const failure = new Error(`Clipboard gate failed (${backend}, ${dimensions.join('x')}): ${JSON.stringify(pixels.clipboard.failure)}`);
        contractFailures.push(failure);
        console.error('Recorded clipboard failure; remaining independent gates continue:',failure.message);
      } else {
        assert.equal(pixels.clipboard.application_to_sentinel,true);
        assert.equal(pixels.clipboard.sentinel_to_application,true);
      }
      console.log('Actual desktop pixels and application input passed:',pixels);
      console.log('Application frame-clock cadence (not physical display FPS):',pixels.timing);
    }
  }
  if (distribution === 'alpine' && selection === 'gnome') {
    const indexing = JSON.parse(await fixture('desktop-localsearch.py', [], 90));
    assert.ok(indexing.uid > 0);
    assert.equal(indexing.world_unpinned, true);
    assert.equal(indexing.sandboxed_extraction, true);
    assert.equal(indexing.content_indexed, true);
    console.log('Native sandboxed LocalSearch content indexing passed:', indexing);
  }
  // Qualify the cold native→X11 transition before launching preview apps.
  // Opening and closing X11 apps first can mask first-client pointer bugs.
  await checkApplication([1280,800]);
  // Exercise the same production browser workers with actual presented WebGL
  // pixels and input. A working CDP endpoint alone does not qualify rendering.
  for (const name of ['app-parity.py', 'browser-parity.html']) {
    await writeFile(path.join(project, name), await readFile(path.join(desktop, 'tests/fixtures', name)));
  }
  for (const app of browser === 'firefox' ? [automationBrowser, 'firefox'] : [automationBrowser]) {
    const browserEvidence = path.join(project, 'browser-gpu', app);
    try {
      console.log(`${app} GPU/pixels/input/confinement:`, await exec([
        'python3', path.join(project, 'app-parity.py'), app, selection === 'xfce' ? 'x11' : 'wayland',
        '--browser-start', browserWorkers[0], '--browser-stop', browserWorkers[1], '--output', browserEvidence,
      ], 180));
    } finally {
      if (process.env.SENTINEL_DESKTOP_EVIDENCE_DIR && await stat(browserEvidence).catch(() => null)) {
        await cp(browserEvidence, path.join(process.env.SENTINEL_DESKTOP_EVIDENCE_DIR, 'browser-gpu', app), {recursive:true});
      }
    }
  }
  if (process.env.SENTINEL_DESKTOP_SCREENSHOT) {
    // Stock applications on an empty, disposable guest only. The screenshot is
    // taken through the same production capture path used by computer control.
    const screenshot = JSON.parse(await exec(['python3','-c',[
      'import json,os,subprocess,sys,tempfile,time',
      'from pathlib import Path',
      'sys.path.insert(0,"/opt/sentinel/desktop")',
      'from sentinel_display import Desktop',
      's=json.loads(Path("/run/sentinel-desktop/session.json").read_text())',
      'u=s["user"]; assert u["uid"] > 0 and u["gid"] > 0',
      'environment=s["environment"]',
      'commands=json.loads(sys.argv[1])',
      'commands[0].append(environment["HOME"])',
      'applications=[]',
      'logs=[]',
      'try:',
      ' for command in commands:',
      '  log=tempfile.TemporaryFile(mode="w+t"); logs.append(log)',
      '  applications.append(subprocess.Popen(command,env=environment,cwd=environment["HOME"],user=u["uid"],group=u["gid"],extra_groups=u["groups"],stdin=subprocess.DEVNULL,stdout=log,stderr=log))',
      '  time.sleep(2)',
      ' d=Desktop()',
      ' try:',
      '  shot=d.screenshot(); shot["applications"]=[]',
      '  for command,application,log in zip(commands,applications,logs):',
      '   log.seek(0); shot["applications"].append({"command":command,"exit_code":application.poll(),"log":log.read()[-8192:]})',
      '  print(json.dumps(shot))',
      ' finally: d.close()',
      'finally:',
      ' for application in applications:',
      '  if application.poll() is None:',
      '   application.terminate()',
      '   try: application.wait(timeout=5)',
      '   except subprocess.TimeoutExpired: application.kill(); application.wait()',
      ' for log in logs: log.close()',
    ].join('\n'),JSON.stringify(previewCommands[selection])],30));
    assert.deepEqual(screenshot.viewport,{width:1280,height:800});
    console.log('Actual desktop preview application diagnostics:',screenshot.applications);
    for (const application of screenshot.applications) {
      assert.ok(application.exit_code === null || application.exit_code === 0,
        `${application.command[0]} failed to start: ${application.log}`);
    }
    const prefix = 'data:image/png;base64,';
    assert.ok(screenshot.screenshot.startsWith(prefix),'Production screenshot must be PNG');
    // Refuse to overwrite an existing image accidentally. Pick a new path for
    // every capture so the reviewed preview has an unambiguous source run.
    const destination = path.resolve(process.env.SENTINEL_DESKTOP_SCREENSHOT);
    await writeFile(destination,Buffer.from(screenshot.screenshot.slice(prefix.length),'base64'),{flag:'wx',mode:0o600});
    console.log('Captured actual regular-user desktop preview:',selection,destination);
  }
  assert.equal((await session('stop')).state,'stopped');
  if (firstLogin) console.log('Previous native login stopped:',await fixture('desktop-native-contract.py',
    ['stopped',JSON.stringify(firstLogin)]));
  assert.equal((await session('start','1920x1200')).state,'running');
  const resizedLogin = await nativeSession([1920,1200]);
  if (firstLogin) assert.notEqual(resizedLogin.session_id,firstLogin.session_id,'Restart must own a fresh Linux login');
  if (firstLogin) assert.deepEqual(resizedLogin.gpu_proxy,firstLogin.gpu_proxy,'Resize must preserve the GPU proxy');
  assert.deepEqual((await stream(socket)).dimensions,[1920,1200]);
  await checkApplication([1920,1200]);
  assert.equal((await session('stop')).state,'stopped');
  assert.equal((await session('start','1280x800')).state,'running');
  const restoredLogin = await nativeSession([1280,800]);
  if (firstLogin) assert.deepEqual(restoredLogin.gpu_proxy,firstLogin.gpu_proxy,'Returning to the initial size must preserve the GPU proxy');
  assert.deepEqual((await stream(socket)).dimensions,[1280,800]);
  await checkApplication([1280,800]);
  await runtime.request('stop',{workspace},60000);
  await assert.rejects(stat(socket),{code:'ENOENT'});
  // Package installation can load volatile kernel policy and hide missing boot
  // integration. Reuse the same disk after a real VM restart, without provisioning.
  await runtime.request('start',{workspace,project,distribution,cpus:4,memory_gib:4,disk_gib:16},600000);
  assert.notEqual((await exec(['cat','/proc/sys/kernel/random/boot_id'])).trim(),firstBoot,
    'Cold-restart gate must boot a new kernel, not merely restart the desktop');
  console.log('Browser lifecycle after cold VM restart:',await fixture(
    'browser-lifecycle.py',[...browserWorkers,'',browser],180));
  await runtime.request('stop',{workspace},60000);
  if (contractFailures.length) throw new AggregateError(contractFailures,'Disposable desktop failed independent contract checks');
  console.log('Packaged GPU desktop, audio, reconnect, input, clipboard, resize and shutdown passed:', {desktop:selection,distribution,browser});
} catch (error) {
  console.error('Desktop combination did not qualify:', {desktop:selection,distribution,browser,interrupted});
  // Record the original failure before diagnostics or teardown can stall.
  console.error(error);
  if (!interrupted && runtime.isReady) {
    const captures = JSON.parse(await exec(['python3', '-c',
      'import base64,json,pwd;from pathlib import Path;root=Path("/run/user")/str(pwd.getpwnam("sentinel").pw_uid);print(json.dumps({name:base64.b64encode(p.read_bytes()).decode() for name in ("oracle-failure.png","oracle-failure.json","oracle-last-frame.png") if (p:=root/("sentinel-"+name)).is_file()}))',
    ], 5).catch(() => '{}'));
    for (const [name, capture] of Object.entries(captures)) {
      const destination = process.env.SENTINEL_DESKTOP_EVIDENCE_DIR
        ? path.join(process.env.SENTINEL_DESKTOP_EVIDENCE_DIR, name)
        : path.join(desktop, 'build', `${distribution}-${selection}-${name}`);
      await writeFile(destination, Buffer.from(capture, 'base64'));
      console.error('Failed oracle evidence:', destination);
    }
    const logs = await fixture('desktop-diagnostics.py',[],25).catch(String);
    console.error('Desktop failure diagnostics:', logs);
  }
  if (!interrupted) throw error;
  console.error('Desktop qualification interrupted; cleaning up its disposable runtime.');
} finally {
  try {
    if (!interrupted && runtime.isReady) await runtime.request('delete',{workspace},60000).catch(console.error);
    // Remove owned directories only after the helper has closed its VM/mounts.
    // If shutdown fails, retain them for diagnosis instead of deleting live data.
    await stopRuntime();
    await Promise.all([rm(project,{recursive:true,force:true}),rm(root,{recursive:true,force:true})]);
  } finally {
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', interrupt);
  }
}
