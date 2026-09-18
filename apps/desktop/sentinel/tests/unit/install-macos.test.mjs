import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, symlinkSync, lstatSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const source = readFileSync(new URL('../../../../../scripts/install-macos.sh', import.meta.url), 'utf8');
// Exercise the real script in an isolated Applications directory, without network or macOS services.
const mocks = `
uname() { if [[ "$1" == -s ]]; then echo Darwin; else echo "$TEST_ARCH"; fi; }
sw_vers() { echo "$TEST_MACOS"; }
pgrep() { [[ "$TEST_CASE" == running ]]; }
curl() { while (( $# )); do if [[ "$1" == -o ]]; then : > "$2"; return; fi; shift; done; }
plutil() { case "$2" in version) echo 2.3.6;; commit) printf '%040d\\n' 1;; installerSha256) if [[ "$TEST_CASE" == metadata ]]; then echo bad; else printf '%064d\\n' 1; fi;; esac; }
shasum() { /bin/cat >/dev/null; [[ "$TEST_CASE" != checksum ]]; }
hdiutil() { if [[ "$1" == attach ]]; then while (( $# )); do if [[ "$1" == -mountpoint ]]; then mkdir -p "$2/Sentinel.app"; printf new > "$2/Sentinel.app/marker"; return; fi; shift; done; fi; }
codesign() { [[ "$TEST_CASE" != signature ]]; }
ditto() { if [[ "$TEST_CASE" == copy ]]; then return 1; fi; cp -R "$1" "$2"; }
mv() { if [[ "$TEST_CASE" == rollback && "$1" == */.sentinel-install.*/Sentinel.app ]]; then return 1; fi; /bin/mv "$@"; }
`;

for (const name of ['fresh', 'upgrade', 'rollback', 'checksum', 'signature', 'copy', 'metadata', 'running', 'architecture', 'macos', 'symlink']) {
  test(`Terminal installer: ${name}`, () => {
    const root = mkdtempSync(path.join(tmpdir(), 'sentinel-installer-test-'));
    try {
      const apps = path.join(root, 'Applications');
      const temp = path.join(root, 'temp');
      mkdirSync(apps);
      mkdirSync(temp);
      const target = path.join(apps, 'Sentinel.app');
      if (name !== 'fresh') {
        if (name === 'symlink') symlinkSync('/nonexistent', target);
        else { mkdirSync(target); writeFileSync(path.join(target, 'marker'), 'old'); }
      }
      const result = spawnSync('/bin/bash', ['-c', mocks + '\n' + source.replaceAll('/Applications', apps)], {
        env: {...process.env, TMPDIR:temp, TEST_CASE:name, TEST_ARCH:name === 'architecture' ? 'x86_64' : 'arm64', TEST_MACOS:name === 'macos' ? '15.0' : '26.0'},
        encoding:'utf8', timeout:10_000,
      });
      const success = ['fresh', 'upgrade'].includes(name);
      assert.equal(result.status === 0, success, `${result.stdout}\n${result.stderr}`);
      if (name === 'symlink') assert(lstatSync(target).isSymbolicLink());
      else assert.equal(readFileSync(path.join(target, 'marker'), 'utf8'), success ? 'new' : 'old');
      if (name === 'upgrade') {
        const backup = readdirSync(apps).find(item => item.startsWith('.sentinel-install.'));
        assert.equal(readFileSync(path.join(apps, backup, 'Previous Sentinel.app', 'marker'), 'utf8'), 'old');
      }
      assert.deepEqual(readdirSync(temp), []);
    } finally { rmSync(root, {recursive:true, force:true}); }
  });
}
