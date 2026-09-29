// Opt-in crash-lifecycle regression. Owns a disposable Debian VM; no user data.
// Run from apps/desktop/sentinel after compiling the .test-dist adapter.
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {lstat, mkdtemp, readFile, realpath, rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {WorkspaceRuntime} from '../../.test-dist/main/workspace/workspaceRuntime.js';

const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources=path.resolve(process.env.SENTINEL_TEST_RUNTIME || path.join(desktop,'build/macos-arm64/runtime/workspace-runtime'));
const config=JSON.parse(await readFile(path.join(resources,'manifest.json'),'utf8'));
const root=await mkdtemp('/tmp/sentinel-renderer-crash-');
const project=await realpath(await mkdtemp('/tmp/sentinel-renderer-crash-project-'));
const workspace=randomUUID();
const runtime=new WorkspaceRuntime({command:path.join(resources,'sentinel-workspace-runtime'),
  args:[root,path.join(resources,'kernel'),config.initImage],
  onProgress:console.log,onFailure:console.error,log:console.error});
let stopping, interrupted=false;
const stopRuntime=()=>stopping ??= runtime.stop();
function interrupt() {
  interrupted=true;
  process.exitCode=130;
  void stopRuntime().catch(console.error);
}
process.on('SIGINT',interrupt);
process.on('SIGTERM',interrupt);
try {
  await runtime.start();
  await runtime.request('start',{workspace,project,distribution:'debian',cpus:2,memory_gib:2,disk_gib:16},600000);
  // Use normal provisioning so this also works on a fresh disk with no GUI deps.
  await runtime.request('graphics_install',{workspace,distribution:'debian',desktop:'xfce'},600000);
  const {socket}=await runtime.request('display_start',{workspace,width:1280,height:800},120000);
  assert.ok((await lstat(socket)).isSocket());
  const executable=path.join(resources,'graphics/sentinel-desktop-renderer');
  const processes=execFileSync('/bin/ps',['-axo','pid=,command='],{encoding:'utf8'})
    .trim().split('\n').map(line=>/^\s*(\d+)\s+(.*)$/.exec(line)).filter(Boolean);
  const matches=processes.filter(([, , command])=>command.startsWith(executable+' ')
    && command.includes(' '+socket+' ') && command.endsWith(' --serve'));
  assert.equal(matches.length,1,'Resolve exactly this disposable VM’s renderer');
  // Renderer-owned temporary socket paths contain no whitespace. The executable
  // path may, so remove its exact prefix before parsing the positional arguments.
  const args=matches[0][2].slice(executable.length+1).split(/\s+/);
  assert.equal(args[2],socket);
  const control=args[3];
  assert.ok((await lstat(control)).isSocket());
  process.kill(Number(matches[0][1]),'SIGKILL');
  await runtime.request('stop',{workspace},90000);
  for(const endpoint of [socket,control]) await assert.rejects(lstat(endpoint),{code:'ENOENT'});
  console.log('PASS: abrupt renderer exit leaves no owned video/control endpoints after workspace stop');
} finally {
  try {
    try {
      if(!interrupted && runtime.isReady) await runtime.request('delete',{workspace},90000);
    } finally {
      await stopRuntime();
    }
    await Promise.all([rm(root,{recursive:true,force:true}),rm(project,{recursive:true,force:true})]);
  } finally {
    process.off('SIGINT',interrupt);
    process.off('SIGTERM',interrupt);
  }
}
