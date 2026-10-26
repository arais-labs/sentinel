// Builds the workspace runtime for development: sentineld (with sentineld-media and the guest share)
// and the workspace VM process. Both are make projects with pinned inputs that build into
// their own build/ directories; make skips whatever is current.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

if (process.platform !== 'darwin' || process.arch !== 'arm64') {
  throw new Error('Workspace development requires an Apple silicon Mac with macOS 26 or newer.');
}
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../..');
const builds = [['packages/sentineld', ['all']], ['packages/sentinel-workspace', ['all']]];
const current = ([dir, targets]) => spawnSync('make', ['-q', '-C', path.join(repo, dir), ...targets], { stdio: 'ignore' }).status === 0;
if (builds.every(current)) process.exit(0);
for (const [dir, targets] of builds) {
  const result = spawnSync('make', ['-C', path.join(repo, dir), ...targets], { stdio: 'inherit' });
  if (result.error || result.status !== 0) {
    throw new Error(`Building ${dir} failed: ${result.error?.message ?? `exit ${result.status}`}`);
  }
}
