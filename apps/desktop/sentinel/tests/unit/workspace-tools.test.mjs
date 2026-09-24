import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { workspaceSetupSteps, toolPackages } from '../../.test-dist/main/workspace/workspaceTools.js';

test('browser choices provision independently of desktop and preserve installed profiles', () => {
  for (const distribution of ['alpine', 'ubuntu', 'debian']) {
    for (const browser of ['chromium', 'firefox', 'chrome']) {
      if (distribution === 'alpine' && browser === 'chrome') {
        assert.throws(() => workspaceSetupSteps([], { distribution, browser }), /not Alpine/);
        continue;
      }
      const steps = workspaceSetupSteps([], { distribution, browser });
      for (const step of steps) {
        assert.equal(spawnSync('/bin/sh', ['-n'], { input: step.arguments[2] }).status, 0);
      }
      const configure = steps.at(-1).arguments[2];
      const preferences = configure.split("<<'PREFERENCES'\n")[1].split('\nPREFERENCES')[0];
      assert.equal(spawnSync('python3', ['-c', 'import ast,sys; ast.parse(sys.stdin.read())'], { input: preferences }).status, 0);
      assert.match(configure, /sentinel-automation-browser/);
      assert.match(configure, /browser-selection/);
      assert.doesNotMatch(configure, /then exit 0/, 'Browser setup must refresh both launchers even when the selection is unchanged');
      if (browser === 'chromium' && distribution === 'ubuntu') assert.ok(configure.includes('Icon=/snap/chromium/current/chromium.png'));
      if (browser === 'firefox' && distribution === 'debian') {
        assert.ok(configure.includes('Name=Firefox ESR'));
        assert.ok(configure.includes('Icon=firefox-esr'));
      }
      assert.match(configure, browser === 'chrome' ? /exec google-chrome-stable/ : distribution === 'ubuntu' ? /exec \/snap\/bin\/chromium/ : /exec chromium/);
      assert.doesNotMatch(configure, /rmtree|rm -rf|--no-sandbox/);
      if (distribution === 'alpine') {
        assert.ok(configure.includes('CHROMIUM_FLAGS="$CHROMIUM_FLAGS --disable-features=GpuPersistentCache"'));
        assert.ok(configure.indexOf('/etc/chromium/sentinel.conf') < configure.indexOf('browser-selection'));
      } else {
        assert.doesNotMatch(configure, /GpuPersistentCache|\/etc\/chromium\/sentinel.conf/);
      }
      assert.doesNotMatch(configure, /--disable-gpu(?:\s|$)|--disable-gpu-sandbox|--disable-gpu-shader-disk-cache/);
      assert.equal(steps.some(step => step.message === 'Installing Firefox…'), browser === 'firefox');
      if (distribution !== 'ubuntu') assert.ok(steps[0].arguments.includes('chromium'));
      else assert.ok(steps.some(step => step.message === 'Installing Chromium…'));
      if (distribution === 'alpine') assert.ok(steps[0].arguments.includes('chromium-swiftshader'));
    }
  }
  assert.throws(() => workspaceSetupSteps([], { browser: 'unknown' }), /Unknown workspace browser/);
});

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
  assert.equal(workspaceSetupSteps([]).length, 1);
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


test('Desktop selection is separate from development tool provisioning', () => {
  const baseline = workspaceSetupSteps([]);
  assert.ok(!baseline[0].arguments.includes('tigervnc'));
  assert.ok(!baseline[0].arguments.includes('xfce4'));
  assert.throws(() => workspaceSetupSteps(['desktop']), /Unknown/);
  assert.equal(toolPackages.desktop, undefined);
  assert.ok(baseline.every(step => !step.arguments[2].includes('Xvnc')));
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

test('cluster storage guidance distinguishes idempotent setup from destructive reinstall', () => {
  const steps = workspaceSetupSteps(['kind', 'k3s', 'docker-builder']);
  const docs = steps.find(step => step.message === 'Finishing container setup…').arguments[2];
  assert.match(docs, /Ordinary tool setup reuses existing clusters and preserves their data and versions/);
  assert.match(docs, /Reinstall erases the private Linux disk, including clusters, registry, build cache and credentials/);
  assert.match(docs, /Neither operation deletes the mounted host project folder/);
  assert.doesNotMatch(docs, /Reinstall preserves/);
});

for (const distribution of ['ubuntu', 'debian']) {
  test(`${distribution} uses apt for distro package names`, () => {
    const steps = workspaceSetupSteps(['git', 'python', 'node'], { distribution });
    assert.ok(!steps[0].arguments.includes('docker.io'));
    const dotnet = workspaceSetupSteps(['dotnet'], { distribution })[0].arguments;
    assert.ok(dotnet.includes(distribution === 'ubuntu' ? 'libicu78' : 'libicu76'));
    assert.equal(dotnet.includes('libicu74'), false);
    assert.doesNotMatch(steps[0].arguments[2], /sentinel-docker-ready/);
    assert.doesNotMatch(steps[0].arguments[2], /apk|musl/);
    for (const step of steps) assert.equal(spawnSync('/bin/sh', ['-n'], { input: step.arguments[2], encoding: 'utf8' }).status, 0);
    for (const tool of ['unknown']) assert.throws(() => workspaceSetupSteps([tool], { distribution }), /not supported/);
  });
}

for (const distribution of ['alpine', 'ubuntu', 'debian']) {
  test(`${distribution} installs and starts Docker only for selected dependent stacks`, () => {
    for (const tools of [[], ['git'], ['python'], ['kubectl', 'helm']]) {
      const steps = workspaceSetupSteps(tools, { distribution });
      assert.ok(!steps[0].arguments.some(arg => /^(docker|containerd)([.-]|$)/.test(arg)));
      assert.ok(!steps.some(step => /docker info|docker start|docker\.service/.test(step.arguments[2])));
    }
    for (const tool of ['docker-builder', 'kind', 'k3s', 'postgres', 'mysql', 'redis']) {
      const steps = workspaceSetupSteps([tool], { distribution });
      assert.ok(steps[0].arguments.includes(distribution === 'alpine' ? 'docker' : 'docker.io'));
      const service = steps.find(step => step.message === 'Starting Docker for your selected stack…');
      assert.ok(service);
      assert.match(service.arguments[2], distribution === 'alpine' ? /rc-service docker start/ : /systemctl enable --now docker.service/);
      assert.ok(steps.indexOf(service) < steps.findIndex(step => /Preparing your database|Starting your.*cluster|Preparing your image registry/.test(step.message)) || tool === 'docker-builder');
    }
  });
}

test('Ubuntu and Debian retain every tool and use the shared cluster lifecycle', async () => {
  const { aptToolPackages } = await import('../../.test-dist/main/workspace/workspaceDistributions.js');
  assert.deepEqual(Object.keys(aptToolPackages).sort(), Object.keys(toolPackages).sort());
  for (const distribution of ['ubuntu', 'debian']) {
    const steps = workspaceSetupSteps(Object.keys(aptToolPackages), { distribution });
    for (const name of ['Installing Node.js…', 'Installing bun…', 'Installing uv…', 'Installing .NET…', 'Installing Docker Buildx…', 'Starting your kind cluster…', 'Starting your K3s cluster…']) assert.ok(steps.some(step => step.message === name), name);
    for (const step of steps) {
      const parsed = spawnSync('/bin/sh', ['-n'], { input: step.arguments[2], encoding: 'utf8' });
      assert.equal(parsed.status, 0, step.message + ': ' + parsed.stderr);
    }
    const scripts = steps.map(step => step.arguments[2]).join('\n');
    assert.doesNotMatch(scripts, /bun-linux-.*musl/);
    assert.match(scripts, /sha512sum -c/);
    if (distribution === 'ubuntu') {
      assert.match(scripts, /snap install chromium --channel=latest\/stable/);
      assert.ok(steps[0].arguments.includes('snapd'));
      assert.ok(steps[0].arguments.includes('apparmor'));
      assert.doesNotMatch(scripts, /playwright|SENTINEL_CHROMIUM_WRAPPER|--devmode/);
    }
    else {
      assert.ok(steps[0].arguments.includes('chromium'));
      assert.doesNotMatch(scripts, /playwright|SENTINEL_CHROMIUM_WRAPPER/);
    }
    assert.match(scripts, /docker buildx inspect sentinel --bootstrap/);
  }
});
