import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { workspaceSetupSteps } from '../../.test-dist/main/workspace/workspaceTools.js';

const source = workspaceSetupSteps(['kind']).find(step => step.message === 'Starting your kind cluster…').arguments[2];
const recovery = source.slice(source.indexOf('export KIND_EXPERIMENTAL_PROVIDER='), source.indexOf('if ! kind get clusters'));

for (const [bootstrap, previous, nodes, inspectFails, removed, success] of [
  ['empty', false, 'sentinel-kind-control-plane', false, true, true],
  ['initialized', false, 'sentinel-kind-control-plane', false, false, true],
  ['partial', false, 'sentinel-kind-control-plane', false, false, false],
  ['empty', true, 'sentinel-kind-control-plane', false, false, false],
  ['empty', false, 'sentinel-kind-control-plane\nsentinel-kind-worker', false, false, false],
  ['empty', false, 'sentinel-kind-control-plane', true, false, false],
]) {
  test(`kind recovery: ${bootstrap}, previous=${previous}, multiple=${nodes.includes('\n')}, inspection failure=${inspectFails}`, async t => {
    const root = await mkdtemp(path.join(tmpdir(), 'sentinel-kind-recovery-'));
    t.after(() => rm(root, { recursive: true, force: true }));
    const bin = path.join(root, 'bin'); await mkdir(bin);
    const marker = path.join(root, 'kind-ready');
    if (previous) await writeFile(marker, '');
    await writeFile(path.join(bin, 'docker'), `#!/bin/sh
printf '%s\\n' "$*" >> "$RECOVERY_LOG"
case "$1" in
 exec) test "$INSPECT_FAILS" != true || exit 1; printf '%s\\n' "$BOOTSTRAP" ;;
 inspect) case "$*" in *io.x-k8s.kind.cluster*) echo sentinel-kind ;; *) echo control-plane ;; esac ;;
esac
`, { mode: 0o700 });
    await writeFile(path.join(bin, 'kind'), '#!/bin/sh\nprintf "%s\\n" "$NODES"\n', { mode: 0o700 });
    await writeFile(path.join(bin, 'timeout'), '#!/bin/sh\nshift\nexec "$@"\n', { mode: 0o700 });
    const log = path.join(root, 'calls');
    const result = spawnSync('sh', ['-ec', recovery.replaceAll('/etc/sentinel/containers/kind-ready', marker)], {
      encoding: 'utf8', env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, RECOVERY_LOG: log, BOOTSTRAP: bootstrap, INSPECT_FAILS: String(inspectFails), NODES: nodes },
    });
    assert.equal(result.status === 0, success, result.stderr);
    const calls = await readFile(log, 'utf8');
    assert.equal(calls.includes('rm -f sentinel-kind-control-plane'), removed);
  });
}

test('Kubernetes API probes and readiness have outer wall-clock limits', () => {
  assert.match(source, /timeout 8 kubectl/);
  assert.match(source, /timeout 130 kubectl/);
  assert.match(source, /seq 1 12/);
});
