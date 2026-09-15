import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { workspaceSetupSteps, toolPackages } from '../../.test-dist/main/workspace/workspaceTools.js';

test('all selected packages are installed together and scripts have valid shell syntax', () => {
  const steps = workspaceSetupSteps(Object.keys(toolPackages));
  const packages = steps[0].arguments.slice(4);
  assert.equal(packages.length, new Set(packages).size);
  for (const value of Object.values(toolPackages).flat()) assert.ok(packages.includes(value), value);
  for (const step of steps) {
    const result = spawnSync('/bin/sh', ['-n'], { input: step.arguments[2], encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  }
  assert.throws(() => workspaceSetupSteps(['node; touch /host']), /Unknown/);
});

test('Bun is checked before installation and its archive checksum before extraction', () => {
  const script = workspaceSetupSteps(['bun']).find(step => step.message === 'Installing Bun…').arguments[2];
  assert.match(script, /bun --version/);
  assert.match(script, /1\.4\.2/);
  assert.ok(script.indexOf('sha256sum -c') < script.indexOf('unzip -q'));
  assert.match(script, /bun-v1\.4\.2/);
  assert.match(script, /trap 'rm -rf/);
  assert.ok(!workspaceSetupSteps(['node']).some(step => step.message === 'Installing Bun…'));
});

test('services are opt-in, use persistent private volumes, loopback ports, and preserved credentials', () => {
  assert.equal(workspaceSetupSteps([]).length, 2);
  const steps = workspaceSetupSteps(['postgres', 'redis']);
  const script = steps.at(-1).arguments[2];
  assert.match(script, /127\.0\.0\.1:5432:5432/);
  assert.match(script, /127\.0\.0\.1:6379:6379/);
  assert.doesNotMatch(script, /mysql:8\.4|3306:3306/);
  assert.match(script, /postgres-data/);
  assert.match(script, /if \[ ! -f credentials.env \]/);
  assert.match(script, /umask 077/);
  assert.match(script, /--wait --wait-timeout/);
  assert.doesNotMatch(script, /\/var\/run\/docker.sock.*:/);
});


test('Desktop is optional and provisioning installs packages without starting graphical services', () => {
  const baseline = workspaceSetupSteps([]);
  assert.ok(!baseline[0].arguments.includes('tigervnc'));
  assert.ok(!baseline[0].arguments.includes('xfce4'));
  const desktop = workspaceSetupSteps(['desktop']);
  for (const name of ['tigervnc', 'xfce4', 'xfce4-terminal', 'chromium', 'dbus-x11']) assert.ok(desktop[0].arguments.includes(name));
  assert.equal(desktop.length, baseline.length);
  assert.ok(desktop.every(step => !step.arguments[2].includes('Xvnc')));
});

test('cluster bundles are independent and use distinct contexts and API ports', () => {
  const kind = workspaceSetupSteps(['kind']);
  const k3s = workspaceSetupSteps(['k3s']);
  assert.ok(kind.some(step => step.message === 'Starting your kind cluster…'));
  assert.ok(!kind.some(step => step.message.includes('K3s')));
  assert.ok(k3s.some(step => step.message === 'Starting your K3s cluster…'));
  assert.ok(!k3s.some(step => step.message.includes('kind')));
  const combined = workspaceSetupSteps(['kind', 'k3s', 'docker-builder']);
  assert.equal(combined.filter(step => step.message.includes('image registry')).length, 1);
  assert.equal(combined.filter(step => step.message.includes('Docker builder')).length, 1);
  const scripts = combined.map(step => step.arguments[2]).join('\n');
  assert.match(scripts, /127\.0\.0\.1:6445/);
  assert.match(scripts, /apiServerPort: 6443/);
  assert.match(scripts, /kind-sentinel-kind/);
  assert.match(scripts, /k3d-sentinel-k3s/);
  assert.match(scripts, /restore_context/);
  assert.doesNotMatch(scripts, /delete cluster|cluster delete/);
});

for (const distribution of ['ubuntu', 'debian']) {
  test(`${distribution} uses apt for distro package names`, () => {
    const steps = workspaceSetupSteps(['git', 'python', 'node'], { distribution });
    assert.ok(steps[0].arguments.includes('docker.io'));
    assert.equal(steps[0].arguments.includes('docker-cli'), distribution === 'debian');
    assert.ok(steps[0].arguments.includes(distribution === 'ubuntu' ? 'docker-compose-v2' : 'docker-compose'));
    assert.match(steps[0].arguments[2], /sentinel-docker-ready/);
    assert.doesNotMatch(steps[0].arguments[2], /apk|musl/);
    for (const step of steps) assert.equal(spawnSync('/bin/sh', ['-n'], { input: step.arguments[2], encoding: 'utf8' }).status, 0);
    for (const tool of ['unknown']) assert.throws(() => workspaceSetupSteps([tool], { distribution }), /not supported/);
  });
}

test('Ubuntu and Debian retain every tool and use the shared cluster lifecycle', async () => {
  const { aptToolPackages } = await import('../../.test-dist/main/workspace/workspaceDistributions.js');
  assert.deepEqual(Object.keys(aptToolPackages).sort(), Object.keys(toolPackages).sort());
  for (const distribution of ['ubuntu', 'debian']) {
    const steps = workspaceSetupSteps(Object.keys(aptToolPackages), { distribution });
    for (const name of ['Installing Node.js…', 'Installing bun…', 'Installing uv…', 'Installing .NET…', 'Installing Chromium…', 'Installing Docker Buildx…', 'Starting your kind cluster…', 'Starting your K3s cluster…']) assert.ok(steps.some(step => step.message === name), name);
    for (const step of steps) {
      const parsed = spawnSync('/bin/sh', ['-n'], { input: step.arguments[2], encoding: 'utf8' });
      assert.equal(parsed.status, 0, step.message + ': ' + parsed.stderr);
    }
    const scripts = steps.map(step => step.arguments[2]).join('\n');
    assert.doesNotMatch(scripts, /bun-linux-.*musl/);
    assert.match(scripts, /sha512sum -c/);
    assert.match(scripts, /playwright install --with-deps --no-shell chromium/);
    assert.match(scripts, /docker buildx inspect sentinel --bootstrap/);
  }
});
