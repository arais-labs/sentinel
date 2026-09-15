// Uses a disposable Apple-container VM; never touches a registered user workspace.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const cache = path.join(desktop, 'build/graphics-sources/guest');
const root = await mkdtemp(path.join(cache, 'clusters-'));
const project = await mkdtemp('/tmp/sentinel-cluster-test-');
const workspace = randomUUID();
const runtime = new WorkspaceRuntime({command:path.join(resources,'sentinel-workspace-runtime'), args:[root,path.join(cache,config.kernelFileSha256),config.initImage,config.workspaceImage],onProgress:console.log,onFailure:console.error,log:()=>{}});
async function exec(arguments_, timeout=300) {
 const result = await runtime.request('exec',{workspace,arguments:arguments_,timeout},(timeout+30)*1000);
 assert.equal(result.exitCode,0,result.stderr+'\n'+result.stdout);
 return result.stdout;
}
const start = () => runtime.request('start',{workspace,project,cpus:4,memory_gib:8,disk_gib:32});
try {
 await runtime.start(); await start();
 console.log('Disposable workspace:',workspace,root);
 for (const step of workspaceSetupSteps(['kind','k3s','helm','docker-builder'])) {
  console.log(step.message);
  console.log(await exec(step.arguments,step.timeout));
 }
 console.log(await exec(['sh','-ec', `
mkdir -p /tmp/build-check
printf 'FROM busybox:1.37\nCMD ["sh", "-c", "echo sentinel-cluster-ok; sleep 3600"]\n' > /tmp/build-check/Dockerfile
docker buildx build --builder sentinel --push -t localhost:5001/sentinel-check:v1 /tmp/build-check
for context in kind-sentinel-kind k3d-sentinel-k3s; do
 kubectl --context "$context" run sentinel-check --image=localhost:5001/sentinel-check:v1
 kubectl --context "$context" wait pod/sentinel-check --for=condition=Ready --timeout=120s
 kubectl --context "$context" logs sentinel-check | grep sentinel-cluster-ok
done
kubectl config use-context kind-sentinel-kind
`],600));
 await runtime.request('stop',{workspace}); await start();
 for (const step of workspaceSetupSteps(['kind','k3s','helm','docker-builder'])) console.log(await exec(step.arguments,step.timeout));
 console.log(await exec(['sh','-ec', `
test "$(kubectl config current-context)" = kind-sentinel-kind
for context in kind-sentinel-kind k3d-sentinel-k3s; do
 kubectl --context "$context" wait pod/sentinel-check --for=condition=Ready --timeout=120s
 kubectl --context "$context" logs sentinel-check | grep sentinel-cluster-ok
done
`],300));
 console.log('PASS: both clusters, BuildKit push, registry pull, VM restart, data and context persistence');
} catch (error) {
 console.error(error);
 try { console.log(await exec(['sh','-c','docker ps -a; docker logs --tail 50 sentinel-kind-control-plane 2>&1; docker logs --tail 50 k3d-sentinel-k3s-server-0 2>&1'],15)); } catch {}
 process.exitCode=1;
} finally {
 try { if(runtime.isReady) await runtime.request('delete',{workspace}); }
 finally {await runtime.stop();await rm(project,{recursive:true,force:true});await rm(root,{recursive:true,force:true});}
}
