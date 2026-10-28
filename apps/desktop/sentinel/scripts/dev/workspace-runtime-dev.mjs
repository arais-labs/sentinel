// Builds the workspace runtime for development: sentineld (with sentineld-media, the guest share and
// the VM libraries it builds from packages/sentinel-workspace). A make project with pinned inputs
// that builds into its build/ directory; make skips whatever is current.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

if (process.platform !== 'darwin' || process.arch !== 'arm64') {
  throw new Error('Workspace development requires an Apple silicon Mac with macOS 26 or newer.');
}
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..');
const dir = path.join(repo, 'packages/sentineld');
if (spawnSync('make', ['-q', '-C', dir, 'all'], { stdio: 'ignore' }).status === 0) process.exit(0);
const result = spawnSync('make', ['-C', dir, 'all'], { stdio: 'inherit' });
if (result.error || result.status !== 0) {
  throw new Error(`Building packages/sentineld failed: ${result.error?.message ?? `exit ${result.status}`}`);
}
