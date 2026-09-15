// Extended distro provisioning check. Uses only disposable UUIDs and private root.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = process.env.SENTINEL_RUNTIME_RESOURCES || path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const root = process.env.SENTINEL_STACK_TEST_ROOT || await mkdtemp('/tmp/sentinel-stack-vms-');
const project = await mkdtemp('/tmp/sentinel-stack-project-');
const runtime = new WorkspaceRuntime({ command: process.env.SENTINEL_DISTRO_HELPER || path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, process.env.SENTINEL_DISTRO_KERNEL || path.join(desktop, 'build/graphics-sources/guest', config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: console.log, onFailure: console.error, log: message => { if (message.includes('error')) console.log(message); } });
const ids = [];
let currentWorkspace;
// Private scratch-only diagnostic endpoint allows progress inspection without
// stopping the guest/package manager when the integration test is waiting.
const server = createServer(async (request, response) => {
  if (request.url !== '/status' || !currentWorkspace) { response.writeHead(404).end(); return; }
  try {
    const result = await runtime.request('exec', { workspace: currentWorkspace, arguments: ['sh', '-c', 'ps -eo pid,stat,comm; tail -5 /var/log/dpkg.log 2>/dev/null || true'], timeout: 5 }, 10_000);
    response.end(result.stdout || result.stderr);
  } catch (error) { response.writeHead(500).end(String(error)); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
console.log('Diagnostic port', server.address().port);
try {
  await runtime.start();
  for (const distribution of (process.env.SENTINEL_DISTRO_TEST_DISTRIBUTIONS || 'ubuntu,debian').split(',')) {
    const workspace = randomUUID(); ids.push(workspace); currentWorkspace = workspace;
    await runtime.request('start', { workspace, project, distribution, cpus: 4, memory_gib: 8, disk_gib: 24 }, 360_000);
    const exec = async (arguments_, timeout = 120) => {
      const result = await runtime.request('exec', { workspace, arguments: arguments_, timeout }, (timeout + 30) * 1000);
      assert.equal(result.exitCode, 0, result.stdout + '\n' + result.stderr);
      return result.stdout;
    };
    const tools = ['git','node','pnpm','typescript','yarn','bun','uv','deno','dotnet','chromium','kubectl','helm','docker-builder',
      ...(process.env.SENTINEL_STACK_SKIP_CLUSTERS ? [] : ['kind','k3s'])];
    for (const step of workspaceSetupSteps(tools, { distribution })) {
      console.log(distribution, step.message);
      console.log(await exec(step.arguments, step.timeout));
    }
    const checks = `
node --version; pnpm --version; tsc --version; yarn --version
bun -e 'console.log(2+2)'; deno eval 'console.log(2+2)'
uv venv --python /usr/bin/python3 /tmp/uv-check
mkdir -p /tmp/dotnet-check
DOTNET_CLI_TELEMETRY_OPTOUT=1 dotnet new console -o /tmp/dotnet-check --no-restore
DOTNET_CLI_TELEMETRY_OPTOUT=1 dotnet run --project /tmp/dotnet-check
chromium --headless --no-sandbox --disable-dev-shm-usage --dump-dom 'data:text/html,<title>sentinel-browser-check</title>' | grep sentinel-browser-check
kubectl version --client; helm version --short; docker buildx version
docker buildx inspect sentinel --bootstrap
`;
    console.log(await exec(['sh','-ec',checks],300));
    if (!process.env.SENTINEL_STACK_SKIP_CLUSTERS) console.log(await exec(['sh','-ec','kubectl --context kind-sentinel-kind get nodes; kubectl --context k3d-sentinel-k3s get nodes']));
    await runtime.request('delete', { workspace }); currentWorkspace = undefined;
    console.log('PASS complete development stacks:', distribution);
  }
} finally {
  for (const workspace of ids) if (runtime.isReady) await runtime.request('delete', { workspace }).catch(() => {});
  await runtime.stop();
  server.close();
  if (!process.env.SENTINEL_STACK_TEST_ROOT) await rm(root, { recursive: true, force: true });
  await rm(project, { recursive: true, force: true });
}
