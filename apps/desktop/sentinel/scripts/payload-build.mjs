#!/usr/bin/env node
// Builds the updatable Sentinel payload: the frozen backend dependencies, the
// backend source, the prebuilt web frontend, and a manifest describing the
// build. The payload is version-bearing and ships SEPARATELY from the thin
// shell DMG (which only carries python/git/gh + postgres). At runtime the shell
// launches `<Resources>/runtime-seed/python -m app.desktop_entry` with
// cwd=<payload>/backend and PYTHONPATH=<payload>/site-packages.
//
// Runs on the clean build VM so the wheels match the bundled interpreter's
// platform (arm64) and no host paths leak in. pip --target gives a fully
// relocatable site-packages with no venv and no absolute shebangs.
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(__filename);
const desktopDir = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopDir, '../../..');
const backendDir = path.join(repoRoot, 'apps/backend/sentinel');
const frontendDir = path.join(repoRoot, 'apps/frontend/sentinel');
const packageJsonPath = path.join(desktopDir, 'package.json');
const distRoot = path.join(desktopDir, 'dist');
// The shell build stages the interpreter here; the payload must be frozen
// against THIS python so the wheels are ABI/platform-correct.
const bundledPython = path.join(
  desktopDir,
  'build/macos-arm64/runtime/runtime-seed/python/bin/python3',
);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    stdio: 'inherit',
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit ${result.status}`);
  }
}

function output(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr || result.error?.message || `exit ${result.status}`}`);
  }
  return result.stdout.trim();
}

function resolveChannel() {
  const channel = process.env.SENTINEL_BUILD_CHANNEL || 'stable';
  if (channel !== 'stable' && channel !== 'beta') {
    throw new Error(`SENTINEL_BUILD_CHANNEL must be "stable" or "beta", got "${channel}".`);
  }
  return channel;
}

async function readVersion() {
  const pkg = JSON.parse(await readFile(packageJsonPath, 'utf8'));
  return pkg.version;
}

// Heads = revisions that no other revision lists as its down_revision. We parse
// the migration files directly so the build needs no DB and no alembic import.
async function alembicHeads(versionsDir) {
  if (!existsSync(versionsDir)) return [];
  const revisions = new Set();
  const downRefs = new Set();
  for (const entry of await readdir(versionsDir)) {
    if (!entry.endsWith('.py')) continue;
    const text = await readFile(path.join(versionsDir, entry), 'utf8');
    const rev = text.match(/^revision\s*(?::\s*str)?\s*=\s*['"]([^'"]+)['"]/m);
    if (rev) revisions.add(rev[1]);
    const down = text.match(/^down_revision\s*(?::[^=]+)?=\s*(.+)$/m);
    if (down) {
      for (const m of down[1].matchAll(/['"]([^'"]+)['"]/g)) downRefs.add(m[1]);
    }
  }
  return [...revisions].filter((rev) => !downRefs.has(rev)).sort();
}

async function stageSitePackages(stagingDir) {
  const sitePackages = path.join(stagingDir, 'site-packages');
  await mkdir(sitePackages, { recursive: true });
  const requirementsPath = path.join(stagingDir, 'requirements.txt');
  // uv export flattens the lockfile into hash-pinned requirements.
  // --no-emit-project drops the editable backend itself (its source ships
  // separately as backend/, importable via cwd).
  run('uv', [
    'export',
    '--frozen',
    '--no-dev',
    '--no-emit-project',
    '--format', 'requirements-txt',
    '--output-file', requirementsPath,
  ], { cwd: backendDir });
  // Freeze deps against the bundled interpreter. --target gives a relocatable
  // tree (no venv, no pyvenv.cfg, no absolute shebangs).
  run(bundledPython, [
    '-m', 'pip', 'install',
    '--no-cache-dir',
    '--target', sitePackages,
    '-r', requirementsPath,
  ], { cwd: backendDir });
  await rm(requirementsPath, { force: true });
  // pip --target may create a bin/ with shebangs pointing at the build-time
  // python; the runtime invokes everything via `-m`, so drop it.
  await rm(path.join(sitePackages, 'bin'), { recursive: true, force: true });
}

async function stageBackend(stagingDir) {
  const backendOut = path.join(stagingDir, 'backend');
  await mkdir(backendOut, { recursive: true });
  // tar pipe copy with excludes: preserves package-data (ansible *.cfg/*.yml,
  // alembic migrations) while dropping dev/venv/cache cruft and anything that
  // could carry developer identity or secrets.
  const excludes = [
    '.venv', '__pycache__', '*.pyc', '*.egg-info', 'tests', '.pytest_cache',
    'node_modules', '.git', '.env', '.env.*', 'uv.lock',
  ];
  run('bash', ['-c',
    `tar -cf - ${excludes.map((e) => `--exclude='${e}'`).join(' ')} -C '${backendDir}' . | tar -xf - -C '${backendOut}'`,
  ]);
}

async function stageFrontend(stagingDir) {
  const lockFile = path.join(frontendDir, 'package-lock.json');
  if (!existsSync(lockFile)) {
    throw new Error(`Missing package-lock.json in ${path.relative(repoRoot, frontendDir)}; payload builds require deterministic npm ci.`);
  }
  run('npm', ['ci', '--no-audit', '--no-fund'], { cwd: frontendDir });
  run('npm', ['run', 'build'], { cwd: frontendDir });
  const distSrc = path.join(frontendDir, 'dist');
  if (!existsSync(path.join(distSrc, 'index.html'))) {
    throw new Error(`Frontend build did not produce ${path.join(distSrc, 'index.html')}.`);
  }
  await cp(distSrc, path.join(stagingDir, 'frontend/dist'), { recursive: true });
}

async function writeManifest(stagingDir, manifest) {
  await writeFile(path.join(stagingDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}

async function sha256File(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

async function buildPayload() {
  if (!existsSync(bundledPython)) {
    throw new Error(
      `Bundled python not found at ${bundledPython}. Run \`npm run desktop:build\` first ` +
      `so the shell runtime stages the interpreter the payload is frozen against.`,
    );
  }
  const channel = resolveChannel();
  const version = await readVersion();
  const commit = output('git', ['rev-parse', 'HEAD'], { cwd: repoRoot });
  const pythonVersion = output(bundledPython, ['-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])']);

  const stagingDir = path.join(os.tmpdir(), 'sentinel-payload-build', 'staging');
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });

  console.log('◆ freezing backend dependencies (pip --target)...');
  await stageSitePackages(stagingDir);
  console.log('◆ staging backend source...');
  await stageBackend(stagingDir);
  console.log('◆ building web frontend (vite)...');
  await stageFrontend(stagingDir);

  const manifest = {
    schema: 1,
    version,
    channel,
    commit,
    python: pythonVersion,
    builtAt: new Date().toISOString(),
    alembicHeads: {
      manager: await alembicHeads(path.join(backendDir, 'db/alembic/manager/versions')),
      instance: await alembicHeads(path.join(backendDir, 'db/alembic/instance/versions')),
    },
  };
  await writeManifest(stagingDir, manifest);

  await mkdir(distRoot, { recursive: true });
  const tarName = `sentinel-payload-${channel}-${version}.tar.gz`;
  const tarPath = path.join(distRoot, tarName);
  await rm(tarPath, { force: true });
  console.log('◆ packing payload tarball...');
  run('tar', ['-czf', tarPath, '-C', stagingDir, '.']);

  const sha256 = await sha256File(tarPath);
  // Release index: what the updater fetches to learn the newest payload per
  // channel and to verify the download. Tarball can't carry its own hash, so
  // it lives here. Uploaded alongside the tarball to GitHub Releases later.
  const indexPath = path.join(distRoot, `latest-${channel}.json`);
  await writeFile(indexPath, `${JSON.stringify({
    schema: 1,
    channel,
    version,
    commit,
    file: tarName,
    sha256,
    alembicHeads: manifest.alembicHeads,
  }, null, 2)}\n`);

  await rm(stagingDir, { recursive: true, force: true });
  console.log(`\n✓ payload: ${path.relative(repoRoot, tarPath)}`);
  console.log(`✓ index:   ${path.relative(repoRoot, indexPath)}`);
  console.log(`  channel=${channel} version=${version} commit=${commit.slice(0, 12)} sha256=${sha256.slice(0, 16)}…`);
}

async function main() {
  const command = process.argv[2] || 'build';
  if (command === 'build') {
    await buildPayload();
    return;
  }
  console.error('Usage: npm run payload:build');
  process.exit(1);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
