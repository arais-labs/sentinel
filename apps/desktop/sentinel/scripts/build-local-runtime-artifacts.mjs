#!/usr/bin/env node
import { existsSync } from 'node:fs';
import { cp, mkdir, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { artifactDir, artifactPath, desktopDir, ensureArtifactDir, fileUrl, repoRoot, sha256 } from './artifact-common.mjs';

const arch = process.arch === 'arm64' ? 'arm64' : process.arch;
const pythonArtifact = `sentinel-python-runtime-macos-${arch}.tar.gz`;
const postgresArtifact = `sentinel-postgres-pgvector-macos-${arch}.tar.gz`;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    env: process.env,
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit ${result.status}`);
  }
}

function output(command, args) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    env: process.env,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr}`);
  }
  return result.stdout.trim();
}

function outputWith(command, args, env) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    env: { ...process.env, ...env },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr}`);
  }
  return result.stdout.trim();
}

async function writeTextFile(target, body) {
  await writeFile(target, body, { mode: 0o644 });
}

function tarCreate(archivePath, cwd, entry, options = {}) {
  const args = [
    '--uid',
    '0',
    '--gid',
    '0',
    '--uname',
    'root',
    '--gname',
    'wheel',
    '-czf',
    archivePath,
    '-C',
    cwd,
    entry,
  ];
  if (options.dereference) args.splice(8, 0, '-h');
  run('tar', args, { env: { COPYFILE_DISABLE: '1' } });
}

function tarContainsLocalData(archivePath) {
  const needles = [
    process.env.HOME,
    repoRoot,
    process.env.USER,
    process.env.LOGNAME,
  ].filter(Boolean);
  if (needles.length === 0) return null;

  const result = spawnSync('tar', ['-xOzf', archivePath], {
    encoding: 'utf8',
    errors: 'replace',
    maxBuffer: 512 * 1024 * 1024,
  });
  if (result.status !== 0) {
    throw new Error(`Unable to scan artifact ${archivePath}: ${result.stderr}`);
  }
  for (const needle of needles) {
    if (result.stdout.includes(needle)) return needle;
  }
  return null;
}

function assertNoLocalData(archivePath) {
  const listing = output('tar', ['-tzvf', archivePath]);
  const unsafeOwner = [process.env.USER, process.env.LOGNAME].filter(Boolean).find((value) => listing.includes(value));
  if (unsafeOwner) {
    throw new Error(`Refusing artifact with local owner metadata: ${unsafeOwner}`);
  }
  const unsafeContent = tarContainsLocalData(archivePath);
  if (unsafeContent) {
    throw new Error(`Refusing artifact containing local data marker: ${unsafeContent}`);
  }
}

function assertNoSymlinks(archivePath) {
  const listing = output('tar', ['-tzvf', archivePath]);
  if (listing.split('\n').some((line) => line.startsWith('l'))) {
    throw new Error(`Refusing artifact containing symlinks: ${archivePath}`);
  }
}

async function finalizeArtifact(archivePath, cwd, entry, options = {}) {
  tarCreate(archivePath, cwd, entry, options);
  try {
    assertNoLocalData(archivePath);
    if (options.rejectSymlinks) assertNoSymlinks(archivePath);
  } catch (error) {
    await rm(archivePath, { force: true });
    throw error;
  }
}

async function prunePythonRuntime(venvDir) {
  const binDir = path.join(venvDir, 'bin');
  for (const entry of await readdir(binDir)) {
    if (!/^python(\d+(\.\d+)?)?$/.test(entry)) {
      await rm(path.join(binDir, entry), { recursive: true, force: true });
    }
  }
  run('find', [venvDir, '-type', 'd', '-name', '__pycache__', '-prune', '-exec', 'rm', '-rf', '{}', '+']);
  run('find', [venvDir, '-name', '*.pyc', '-delete']);
  run('find', [venvDir, '-name', 'direct_url.json', '-delete']);
}

function candidatePgConfigs() {
  return [
    process.env.SENTINEL_DESKTOP_PG_CONFIG,
    '/opt/homebrew/opt/postgresql@17/bin/pg_config',
    '/opt/homebrew/opt/postgresql@18/bin/pg_config',
    '/opt/homebrew/opt/postgresql@16/bin/pg_config',
    '/opt/homebrew/opt/postgresql@15/bin/pg_config',
    '/opt/homebrew/opt/postgresql@14/bin/pg_config',
    output('which', ['pg_config']),
  ].filter(Boolean);
}

function selectPgConfig() {
  const checked = [];
  for (const pgConfig of candidatePgConfigs()) {
    if (!existsSync(pgConfig)) continue;
    const pgBin = output(pgConfig, ['--bindir']);
    const pgPkgLib = output(pgConfig, ['--pkglibdir']);
    const pgShare = output(pgConfig, ['--sharedir']);
    const vectorControl = path.join(pgShare, 'extension/vector.control');
    const vectorLib = path.join(pgPkgLib, 'vector.dylib');
    checked.push(`${pgConfig} (${output(pgConfig, ['--version'])})`);
    if (existsSync(vectorControl) && existsSync(vectorLib)) {
      return { pgConfig, pgBin, pgPkgLib, pgShare };
    }
  }
  throw new Error(
    `No Postgres installation with pgvector was found. Checked: ${checked.join(', ') || 'none'}. ` +
      'Install a matching pgvector package or set SENTINEL_DESKTOP_PG_CONFIG.',
  );
}

async function buildPythonArtifact() {
  const backendDir = path.join(repoRoot, 'apps/backend/sentinel');
  const workDir = path.join(artifactDir, 'work/python');
  const venvDir = path.join(workDir, 'runtime');
  await rm(workDir, { recursive: true, force: true });
  await mkdir(workDir, { recursive: true });

  run('python3', ['-m', 'venv', venvDir]);
  const python = path.join(venvDir, 'bin/python3');
  run('uv', ['sync', '--frozen', '--no-dev', '--no-editable', '--active'], {
    cwd: backendDir,
    env: {
      VIRTUAL_ENV: venvDir,
      PATH: `${path.join(venvDir, 'bin')}:${process.env.PATH ?? ''}`,
      UV_PROJECT_ENVIRONMENT: venvDir,
    },
  });
  run(python, ['-c', 'import asyncpg, fastapi, pgvector, uvicorn']);

  await rm(path.join(venvDir, 'pip-selfcheck.json'), { force: true });
  await prunePythonRuntime(venvDir);
  await writeTextFile(
    path.join(venvDir, 'pyvenv.cfg'),
    `home = /opt/homebrew/bin\ninclude-system-site-packages = false\nversion = ${output(python, ['-c', 'import platform; print(platform.python_version())'])}\n`,
  );
  await finalizeArtifact(artifactPath(pythonArtifact), workDir, 'runtime', { dereference: true, rejectSymlinks: true });
  return {
    id: 'python-runtime',
    platform: `macos-${arch}`,
    url: fileUrl(artifactPath(pythonArtifact)),
    sha256: await sha256(artifactPath(pythonArtifact)),
    unpackTo: 'python',
  };
}

async function buildPostgresArtifact() {
  const workDir = path.join(artifactDir, 'work/postgres');
  const runtimeDir = path.join(workDir, 'runtime');
  await rm(workDir, { recursive: true, force: true });
  await mkdir(path.join(runtimeDir, 'bin'), { recursive: true });
  await mkdir(path.join(runtimeDir, 'lib'), { recursive: true });
  await mkdir(path.join(runtimeDir, 'share/extension'), { recursive: true });

  const { pgConfig, pgBin, pgPkgLib, pgShare } = selectPgConfig();
  const requiredBins = ['postgres', 'initdb', 'pg_ctl', 'createdb', 'psql', 'pg_isready'];
  console.log(`Using ${output(pgConfig, ['--version'])} from ${pgConfig}`);

  for (const bin of requiredBins) {
    const source = path.join(pgBin, bin);
    if (!existsSync(source)) throw new Error(`Missing Postgres binary: ${source}`);
    await symlink(source, path.join(runtimeDir, 'bin', bin));
  }

  const vectorControl = path.join(pgShare, 'extension/vector.control');
  if (!existsSync(vectorControl)) {
    throw new Error(
      `Missing pgvector extension at ${vectorControl}. Install pgvector for this Postgres before building local artifacts.`,
    );
  }

  const extensionFiles = outputWith('find', [path.join(pgShare, 'extension'), '-maxdepth', '1', '-name', 'vector*'], {})
    .split('\n')
    .filter(Boolean);
  for (const file of extensionFiles) {
    await cp(file, path.join(runtimeDir, 'share/extension', path.basename(file)), { dereference: true });
  }

  const vectorLibs = output('find', [pgPkgLib, '-maxdepth', '1', '-name', 'vector*']).split('\n').filter(Boolean);
  for (const lib of vectorLibs) {
    await symlink(lib, path.join(runtimeDir, 'lib', path.basename(lib)));
  }

  await writeTextFile(
    path.join(runtimeDir, 'README.txt'),
    'This local artifact is for development packaging only.\nRelease artifacts must contain self-contained binaries built in CI.\n',
  );

  await finalizeArtifact(artifactPath(postgresArtifact), workDir, 'runtime', { dereference: true, rejectSymlinks: true });
  return {
    id: 'postgres-pgvector',
    platform: `macos-${arch}`,
    url: fileUrl(artifactPath(postgresArtifact)),
    sha256: await sha256(artifactPath(postgresArtifact)),
    unpackTo: 'postgres',
  };
}

async function main() {
  await ensureArtifactDir();
  const artifacts = [await buildPythonArtifact(), await buildPostgresArtifact()];
  const manifest = {
    version: 1,
    generatedAt: new Date().toISOString(),
    artifacts,
  };
  const manifestPath = path.join(desktopDir, 'resources/runtime-manifest.local.json');
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`Wrote ${manifestPath}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
