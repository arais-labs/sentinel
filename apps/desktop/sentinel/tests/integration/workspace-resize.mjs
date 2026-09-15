// Destructive only to its own disposable VM; verifies real filesystem capacity and data.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const resources=path.join(desktop,'build/macos-arm64/runtime/workspace-runtime');
const config=JSON.parse(await readFile(path.join(resources,'manifest.json'),'utf8'));
const cache=path.join(desktop,'build/graphics-sources/guest');
const root=await mkdtemp(path.join(cache,'resize-'));
const project=await mkdtemp('/tmp/sentinel-resize-');
const workspace=randomUUID();
const runtime=new WorkspaceRuntime({command:path.join(resources,'sentinel-workspace-runtime'),args:[root,path.join(cache,config.kernelFileSha256),config.initImage,config.workspaceImage],onProgress:console.log,onFailure:console.error,log:()=>{}});
const disk=path.join(root,'store/containers',workspace,'rootfs.ext4');
async function exec(script) {
 const r=await runtime.request('exec',{workspace,arguments:['sh','-ec',script],timeout:120},150_000);
 assert.equal(r.exitCode,0,r.stderr+'\n'+r.stdout);return r.stdout;
}
try {
 await runtime.start();
 await runtime.request('start',{workspace,project,cpus:2,memory_gib:2,disk_gib:8});
 const beforeCPUs=Number((await exec('nproc')).trim());
 console.log(await exec('echo preserved > /root/resize-check; df -k /; nproc; cat /proc/meminfo | head -1; sync'));
 await runtime.request('stop',{workspace});
 await runtime.request('start',{workspace,project,cpus:4,memory_gib:4,disk_gib:16,grow_disk:true},360_000);
 console.log(await exec('test "$(cat /root/resize-check)" = preserved; nproc; df -k /; test "$(df -k / | tail -1 | awk \'{print $2}\')" -gt 12000000; test "$(awk \'/MemTotal/ {print $2}\' /proc/meminfo)" -gt 3500000'));
 assert.equal(Number((await exec('nproc')).trim()),beforeCPUs+2);
 assert.equal((await stat(disk)).size,16*1024**3);
 await runtime.request('stop',{workspace});
 await assert.rejects(runtime.request('start',{workspace,project,cpus:2,memory_gib:2,disk_gib:8,grow_disk:true}),/cannot be shrunk/);
 assert.equal((await stat(disk)).size,16*1024**3);
 // Repeating an interrupted resize must be harmless, and reducing CPU/RAM is supported.
 await runtime.request('start',{workspace,project,cpus:2,memory_gib:2,disk_gib:16,grow_disk:true},360_000);
 console.log(await exec('test "$(cat /root/resize-check)" = preserved; nproc; df -k /'));
 console.log('PASS: disk capacity doubled, data preserved, CPU/RAM changed, shrink rejected, resize retry safe');
} finally {
 try {if(runtime.isReady) await runtime.request('delete',{workspace});}
 finally {await runtime.stop();await rm(project,{recursive:true,force:true});await rm(root,{recursive:true,force:true});}
}
