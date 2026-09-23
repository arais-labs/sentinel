// Disposable native-session diagnosis, never an existing workspace.
// SENTINEL_TEST_DESKTOP=xfce SENTINEL_TEST_DISTRIBUTION=alpine node tests/integration/desktop-debug.mjs
// After DEBUG_READY send one JSON object per line:
// {"action":"session","request":{"action":"start","geometry":"1280x800"}}
// {"action":"exec","arguments":["loginctl","list-sessions"],"timeout":20}
// {"action":"upload","source":"native/graphics/guest/desktop_login.py","destination":"/opt/sentinel/desktop/desktop_login.py"}
// {"action":"diagnostics"} or {"action":"close"}. EOF/SIGINT also tears down the owned VM.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { WorkspaceGraphics } from '../../.test-dist/main/workspace/workspaceGraphics.js';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = path.resolve(process.env.SENTINEL_TEST_RUNTIME ||
  path.join(desktop,'build/macos-arm64/runtime/workspace-runtime'));
const config = JSON.parse(await readFile(path.join(resources,'manifest.json'),'utf8'));
const selection = process.env.SENTINEL_TEST_DESKTOP || 'xfce';
const distribution = process.env.SENTINEL_TEST_DISTRIBUTION || 'alpine';
assert.ok(['xfce','lxqt','gnome','plasma'].includes(selection));
assert.ok(['alpine','ubuntu','debian'].includes(distribution));
const root = await mkdtemp('/tmp/sentinel-desktop-debug-');
const project = await realpath(await mkdtemp('/tmp/sentinel-desktop-debug-project-'));
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({
  command:path.join(resources,'sentinel-workspace-runtime'),
  args:[root,path.join(resources,'kernel'),config.initImage],
  log:console.error,onProgress:console.error,onFailure:console.error,
});
const input = createInterface({input:process.stdin,terminal:false});
let stopping,interrupted=false;
const stop = () => stopping ??= runtime.stop();
const interrupt = () => {interrupted=true;input.close();void stop().catch(console.error);};
process.on('SIGINT',interrupt);
process.on('SIGTERM',interrupt);
async function exec(arguments_,timeout=60) {
  return runtime.request('exec',{workspace,arguments:arguments_,timeout},(timeout+30)*1000);
}
try {
  await runtime.start();
  await runtime.request('start',{workspace,project,distribution,cpus:4,memory_gib:4,disk_gib:16},600000);
  await new WorkspaceGraphics(runtime).install(workspace,selection,distribution);
  const display = await runtime.request('display_start',{workspace,width:1280,height:800},60000);
  console.log('DEBUG_READY',JSON.stringify({workspace,project,root,selection,distribution,...display}));
  for await (const line of input) {
    try {
      const command=JSON.parse(line);
      let reply;
      if(command.action==='close') break;
      if(command.action==='exec') reply=await exec(command.arguments,command.timeout);
      else if(command.action==='session') reply=await exec(['python3','/opt/sentinel/desktop/desktop-session.py',JSON.stringify(command.request)],90);
      else if(command.action==='upload') {
        const source=path.resolve(desktop,command.source);
        assert.ok(source.startsWith(desktop+path.sep),'Only repository-owned test source is delivered');
        assert.ok(command.destination.startsWith('/opt/sentinel/desktop/') && !command.destination.split('/').includes('..'));
        const payload=(await readFile(source)).toString('base64');
        reply=await exec(['python3','-c','import base64,sys;from pathlib import Path;p=Path(sys.argv[1]);assert p.is_file() and not p.is_symlink();p.write_bytes(base64.b64decode(sys.argv[2]))',command.destination,payload]);
      } else if(command.action==='diagnostics') {
        const source=(await readFile(path.join(desktop,'tests/fixtures/desktop-diagnostics.py'))).toString('base64');
        reply=await exec(['python3','-c','import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),"desktop-diagnostics.py","exec"))',source],25);
      } else throw new Error('Unknown debug action');
      console.log('DEBUG_RESULT',JSON.stringify(reply));
    } catch(error) {console.error('DEBUG_ERROR',String(error));}
  }
} finally {
  input.close();
  try {
    if(!interrupted && runtime.isReady) await runtime.request('delete',{workspace},60000).catch(console.error);
    await stop();
    await Promise.all([rm(root,{recursive:true,force:true}),rm(project,{recursive:true,force:true})]);
  } finally {
    process.off('SIGINT',interrupt);
    process.off('SIGTERM',interrupt);
  }
}
