// Exercise tmux redraws through the native PTY and the same xterm parser as the UI.
import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import xterm from '../../../../frontend/sentinel/node_modules/@xterm/xterm/lib/xterm.js';
process.chdir(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..'));
const temp = await mkdtemp(path.join(tmpdir(), 'sentinel-render-test-'));
const root = path.join(temp, 'runtime');
const project = path.join(temp, 'project');
const cache = path.join(homedir(), 'Library/Application Support/Sentinel Dev/state/workspace-runtime');
const resources = path.resolve('apps/desktop/sentinel/build/macos-arm64/runtime/workspace-runtime');
const manifest = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
await mkdir(path.join(root, 'store'), {recursive:true});
await mkdir(project);
for (const name of ['content', 'state.json', 'initfs.ext4']) await cp(path.join(cache, 'store', name), path.join(root, 'store', name), {recursive:true, mode:2});
await writeFile(path.join(project, 'output.txt'), Array.from({length:100}, (_,i)=>`LINE_${i} ${'command text '.repeat(i%4 ? 1 : 15)}`).join('\n')+'\nPython 3.14.7\nNode 24\nnpm 11\nDONE\n');
const workspace = randomUUID();
const processId = randomUUID();
const runtime = new WorkspaceRuntime({command:path.join(resources,'sentinel-workspace-runtime'), args:[root,path.join(cache,'kernels',manifest.kernelFileSha256,'kernel'),manifest.initImage],onProgress:console.log,onFailure:console.error,log:()=>{}});
const terminal = new xterm.Terminal({cols:100,rows:60,allowProposedApi:true});
let writes = Promise.resolve();
let input = Promise.resolve();
let raw = [];
const pause = ms => new Promise(r=>setTimeout(r,ms));
async function exec(args) {
 const result = await runtime.request('exec',{workspace,arguments:args,timeout:120});
 assert.equal(result.exitCode,0,result.stderr); return result.stdout;
}
async function tmux(...args) { return exec(['tmux','-u','-S','/tmp/render.sock',...args]); }
async function check(label) {
 await pause(150); await writes;
 const screen = Array.from({length:terminal.rows}, (_,i)=>terminal.buffer.active.getLine(terminal.buffer.active.baseY+i)?.translateToString(true)||'').join('\n');
 const count = screen.split('Python 3.14.7').length-1;
 console.log(label,'version copies:',count);
 if (count>1) { await writeFile('/tmp/sentinel-render-failure.txt',screen); await writeFile('/tmp/sentinel-render-stream.bin',Buffer.concat(raw)); }
 assert.ok(count<=1,`${label}: output repeated ${count} times`);
}
try {
 await runtime.start();
 await runtime.request('start',{workspace,project,cpus:2,memory_gib:2,disk_gib:4});
 await exec(['sh','-ec','apk add --no-cache tmux bash']);
 await tmux('new-session','-d','-s','render','-x','200','-y','50','bash --noprofile --norc');
 await tmux('set','-g','mouse','on');
 await tmux('set','-g','status','off');
 await tmux('set','-g','pane-border-status','top');
 await tmux('send-keys','-l','cat output.txt'); await tmux('send-keys','Enter');
 await pause(300);
 runtime.events.on(processId,event=>{
  if (event.data) { const data=Buffer.from(event.data,'base64'); raw.push(data); writes=writes.then(()=>new Promise(resolve=>terminal.write(data,resolve))); }
 });
 terminal.onData(data=>{input=input.then(()=>runtime.request('process_input',{workspace,process:processId,data:Buffer.from(data).toString('base64')}));});
 await runtime.request('process_start',{workspace,process:processId,terminal:true,cols:80,rows:24,arguments:['tmux','-u','-S','/tmp/render.sock','attach-session','-t','render']});
 await runtime.request('process_resize',{workspace,process:processId,cols:100,rows:60});
 await pause(250);
 await pause(300); await check('live');
 await tmux('copy-mode'); await check('copy mode');
 for(let i=0;i<20;i++){await tmux('send-keys','-X','scroll-up');await check(`scroll ${i}`);}
 for(const [cols,rows] of [[140,70],[80,24],[110,50]]) {
  terminal.resize(cols,rows);
  await runtime.request('process_resize',{workspace,process:processId,cols,rows});
  await check(`resize ${cols}x${rows}`);
 }
 console.log('PASS: scrollback redraws do not duplicate output');
} finally {
 await input.catch(()=>{});
 await runtime.stop(); terminal.dispose(); await rm(temp,{recursive:true});
}
