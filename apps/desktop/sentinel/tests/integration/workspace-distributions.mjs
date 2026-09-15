// Boots only disposable workspaces. Never discovers or changes a user's VM.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WorkspaceRuntime } from '../../.test-dist/main/workspace/workspaceRuntime.js';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';
import { aptPackages, aptToolPackages } from '../../.test-dist/main/workspace/workspaceDistributions.js';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const resources = process.env.SENTINEL_RUNTIME_RESOURCES || path.join(desktop, 'build/macos-arm64/runtime/workspace-runtime');
const config = JSON.parse(await readFile(path.join(resources, 'manifest.json'), 'utf8'));
const root = process.env.SENTINEL_DISTRO_TEST_ROOT || await mkdtemp('/tmp/sentinel-distro-vms-');
const project = await mkdtemp('/tmp/sentinel-distro-project-');
const runtime = new WorkspaceRuntime({ command: process.env.SENTINEL_DISTRO_HELPER || path.join(resources, 'sentinel-workspace-runtime'),
  args: [root, process.env.SENTINEL_DISTRO_KERNEL || path.join(desktop, 'build/graphics-sources/guest', config.kernelFileSha256), config.initImage, config.workspaceImage],
  onProgress: console.log, onFailure: console.error, log: () => {} });
const ids = [];
try {
  await runtime.start();
  for (const distribution of process.env.SENTINEL_DISTRO_TEST_DISTRIBUTIONS?.split(',') ?? ['ubuntu', 'debian']) {
    const workspace = randomUUID(); ids.push(workspace);
    console.log(`Starting ${distribution}`);
    await runtime.request('start', { workspace, project, distribution, cpus: 2, memory_gib: 2, disk_gib: 8 }, 360_000);
    const exec = async (arguments_, timeout = 120) => {
      const result = await runtime.request('exec', { workspace, arguments: arguments_, timeout }, (timeout + 30) * 1000);
      assert.equal(result.exitCode, 0, result.stdout + '\n' + result.stderr);
      return result.stdout;
    };
    assert.match(await exec(['sh', '-ec', '. /etc/os-release; printf "%s" "$ID"']), new RegExp(`^${distribution}$`));
    for (const step of workspaceSetupSteps(['git', 'python', 'node'], { distribution })) {
      console.log(distribution, step.message);
      await exec(step.arguments, step.timeout);
    }
    // Resolve every advertised tool against the actual distro repository.
    await exec(['apt-get', '-s', '--no-install-recommends', 'install', ...aptPackages(Object.keys(aptToolPackages), distribution)]);
    const checks = 'python3 --version; node --version; tmux -V; docker compose version; docker run --rm alpine:3.23 echo nested-docker-ok; printf preserved > /root/distro-check';
    console.log(await exec(['sh', '-ec', checks], 180));
    await runtime.request('stop', { workspace });
    await assert.rejects(runtime.request('start', { workspace, project, distribution: 'alpine', cpus: 2, memory_gib: 2, disk_gib: 8 }), /distribution/);
    await runtime.request('start', { workspace, project, distribution, cpus: 2, memory_gib: 2, disk_gib: 8 }, 360_000);
    for (const step of workspaceSetupSteps(['git', 'python', 'node'], { distribution })) await exec(step.arguments, step.timeout);
    assert.equal(await exec(['cat', '/root/distro-check']), 'preserved');
    await runtime.request('delete', { workspace });
    console.log(`PASS ${distribution}: rootfs, tools, package catalog, Docker, restart, disk preservation and distro guard`);
  }
} finally {
  for (const workspace of ids) if (runtime.isReady) await runtime.request('delete', { workspace }).catch(() => {});
  await runtime.stop();
  if (!process.env.SENTINEL_DISTRO_TEST_ROOT) await rm(root, { recursive: true, force: true });
  await rm(project, { recursive: true, force: true });
}
