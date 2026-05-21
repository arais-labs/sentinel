import { existsSync, readFileSync } from 'node:fs';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

export const runtimeBuildVersion = 2;

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

function output(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: process.env,
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr}`);
  }
  return result.stdout.trim();
}

function outputMaybe(command, args) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    env: process.env,
  });
  return result.status === 0 ? result.stdout.trim() : '';
}

function commandExists(command) {
  return Boolean(outputMaybe('which', [command]));
}

export function resolveBuildTools() {
  return {};
}

export function verifyBuildTools() {
  // `git` is needed to clone the source bundle; the native build tools are
  // for Postgres/pgvector, which still build from source on the VM.
  const required = ['clang', 'curl', 'ditto', 'git', 'make', 'ninja', 'pkg-config', 'shasum', 'tar'];
  const missing = required.filter((command) => !commandExists(command));
  if (missing.length) {
    throw new Error(`Missing macOS runtime build tools: ${missing.join(', ')}`);
  }
}

export function runtimeRequirements() {
  return {
    'runtime-seed': [
      'python/bin/python3',
      'node/bin/node',
      'node/bin/npm',
      'git/bin/git',
      'gh/bin/gh',
      'uv',
      'source.git.tar',
      'wheels/.complete',
      'node_modules-cache.tar.gz',
    ],
    postgres: [
      'bin/postgres',
      'bin/initdb',
      'bin/pg_ctl',
      'bin/createdb',
      'bin/psql',
      'bin/pg_isready',
      'bin/pg_dump',
      'bin/pg_restore',
      'share/extension/vector.control',
      'lib/vector.dylib',
    ],
  };
}

export function runtimeComponents({ config, paths }) {
  const requirements = runtimeRequirements();
  const backendDir = path.join(paths.repoRoot, 'apps/backend/sentinel');
  const frontendDir = path.join(paths.repoRoot, 'apps/frontend/sentinel');
  return [
    {
      name: 'runtime-seed',
      config: {
        python: pythonRuntimeConfig(config),
        node: nodeRuntimeConfig(config),
        uv: uvRuntimeConfig(config),
        git: gitRuntimeConfig(config),
        gh: ghRuntimeConfig(config),
        sourceClone: sourceCloneConfig(config, paths),
      },
      required: requirements['runtime-seed'],
      inputs: [
        path.join(backendDir, 'pyproject.toml'),
        path.join(backendDir, 'uv.lock'),
        path.join(frontendDir, 'package.json'),
        path.join(frontendDir, 'package-lock.json'),
      ],
      build: buildRuntimeSeed,
    },
    {
      name: 'postgres',
      config: {
        postgres: postgresConfig(config),
        pgvector: pgvectorConfig(config),
      },
      required: requirements.postgres,
      build: buildPostgresRuntime,
    },
  ];
}

function pythonRuntimeConfig(config) {
  if (!config.python || typeof config.python !== 'object') {
    throw new Error('macos-arm64 runtime lock must define python.{version,buildTag,sourceUrl,sourceSha256}.');
  }
  for (const key of ['version', 'buildTag', 'sourceUrl', 'sourceSha256']) {
    if (!config.python[key]) throw new Error(`macos-arm64 runtime lock is missing python.${key}.`);
  }
  return config.python;
}

function nodeRuntimeConfig(config) {
  if (!config.node || typeof config.node !== 'object') {
    throw new Error('macos-arm64 runtime lock must define node.{version,sourceUrl,sourceSha256}.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.node[key]) throw new Error(`macos-arm64 runtime lock is missing node.${key}.`);
  }
  return config.node;
}

function uvRuntimeConfig(config) {
  if (!config.uv || typeof config.uv !== 'object') {
    throw new Error('macos-arm64 runtime lock must define uv.{version,sourceUrl,sourceSha256}.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.uv[key]) throw new Error(`macos-arm64 runtime lock is missing uv.${key}.`);
  }
  return config.uv;
}

function gitRuntimeConfig(config) {
  if (!config.git || typeof config.git !== 'object') {
    throw new Error('macos-arm64 runtime lock must define git.{source,systemBinPath,...}.');
  }
  if (config.git.source !== 'system-vendored') {
    throw new Error('macos-arm64 git.source must be "system-vendored" (we copy from CLT, not download).');
  }
  for (const key of ['systemBinPath', 'systemLibexecPath', 'systemSharePath', 'expectedVersionPrefix']) {
    if (!config.git[key]) throw new Error(`macos-arm64 runtime lock is missing git.${key}.`);
  }
  return config.git;
}

function ghRuntimeConfig(config) {
  if (!config.gh || typeof config.gh !== 'object') {
    throw new Error('macos-arm64 runtime lock must define gh.{version,sourceUrl,sourceSha256}.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.gh[key]) throw new Error(`macos-arm64 runtime lock is missing gh.${key}.`);
  }
  return config.gh;
}

function sourceCloneConfig(config, paths) {
  if (!config.sourceClone || typeof config.sourceClone !== 'object') {
    throw new Error('macos-arm64 runtime lock must define sourceClone.{url,depth,channels}.');
  }
  if (!config.sourceClone.url) throw new Error('macos-arm64 runtime lock is missing sourceClone.url.');
  // Folded into the cache key so upstream pushes bust the snapshot.
  const heads = resolveUpstreamHeads(config.sourceClone, paths);
  return { ...config.sourceClone, heads };
}

// Mirrors stampDir/runtimeStampPath in desktop-build.mjs.
function runtimeSeedStampPath(paths) {
  return path.join(paths.targetDir, 'stamps', 'runtime-runtime-seed.json');
}

function readPreviousUpstreamHeads(paths) {
  if (!paths || !paths.targetDir) return null;
  const stampPath = runtimeSeedStampPath(paths);
  if (!existsSync(stampPath)) return null;
  try {
    const stamp = JSON.parse(readFileSync(stampPath, 'utf8'));
    const heads = stamp?.config?.sourceClone?.heads;
    if (heads && typeof heads === 'object' && Object.keys(heads).length > 0) {
      return heads;
    }
  } catch {
    // corrupt stamp
  }
  return null;
}

function resolveUpstreamHeads(sourceCfg, paths) {
  const channels = Array.isArray(sourceCfg.channels) ? sourceCfg.channels : [];
  if (channels.length === 0) return {};
  const refs = channels.map((channel) => `refs/heads/${channel}`);
  const result = spawnSync('git', ['ls-remote', sourceCfg.url, ...refs], {
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0', GIT_ASKPASS: 'echo' },
    timeout: 10_000,
  });
  if (result.status !== 0) {
    const reason = result.error?.message || result.stderr?.trim() || `exit ${result.status}`;
    // Offline: reuse prior stamp so the cache key matches the last online build.
    const prevHeads = readPreviousUpstreamHeads(paths);
    if (prevHeads) {
      console.warn(
        `[runtime-seed] git ls-remote ${sourceCfg.url} failed (${reason}). ` +
          `Reusing upstream HEADs from the previous build's stamp; the bundled source snapshot may be stale.`,
      );
      return prevHeads;
    }
    throw new Error(
      `git ls-remote ${sourceCfg.url} failed (${reason}) and no previous runtime-seed stamp exists at ${runtimeSeedStampPath(paths)}. ` +
        `Run an online build first to populate the cache, then offline builds can reuse it.`,
    );
  }
  const heads = {};
  for (const line of result.stdout.split('\n')) {
    const [sha, ref] = line.trim().split(/\s+/);
    if (!sha || !ref) continue;
    const channel = ref.replace(/^refs\/heads\//, '');
    if (channels.includes(channel)) heads[channel] = sha;
  }
  return heads;
}

function verifyArchiveSha256(archivePath, expected, label) {
  const actual = output('shasum', ['-a', '256', archivePath]).split(/\s+/)[0];
  if (actual !== expected) {
    throw new Error(`${label} checksum mismatch. Expected ${expected}, got ${actual}.`);
  }
}

async function buildRuntimeSeed({ config, paths }) {
  const cfg = {
    python: pythonRuntimeConfig(config),
    node: nodeRuntimeConfig(config),
    uv: uvRuntimeConfig(config),
    git: gitRuntimeConfig(config),
    gh: ghRuntimeConfig(config),
    sourceClone: sourceCloneConfig(config, paths),
  };
  const workDir = path.join(paths.targetDir, 'work/runtime-seed');
  const downloadCacheDir = path.join(paths.targetDir, 'work/runtime-seed-cache');
  const outputDir = path.join(paths.runtimeDir, 'runtime-seed');
  await rm(workDir, { recursive: true, force: true });
  await mkdir(workDir, { recursive: true });
  await mkdir(downloadCacheDir, { recursive: true });
  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });

  await stagePython(cfg.python, outputDir, downloadCacheDir, workDir);
  await stageNode(cfg.node, outputDir, downloadCacheDir, workDir);
  await stageUv(cfg.uv, outputDir, downloadCacheDir, workDir);
  await stageGit(cfg.git, outputDir);
  await stageGh(cfg.gh, outputDir, downloadCacheDir, workDir);
  await stageSourceClone(cfg.sourceClone, outputDir, workDir);
  await stageWheels(cfg.python, outputDir, paths);
  await stageNodeModulesCache(outputDir, paths, workDir);
}

async function stagePython(pythonCfg, outputDir, cacheDir, workDir) {
  const archive = await downloadWithSha(
    pythonCfg.sourceUrl,
    pythonCfg.sourceSha256,
    path.join(cacheDir, `python-${pythonCfg.version}-${pythonCfg.buildTag}.tar.gz`),
    'python-build-standalone',
  );
  const extractDir = path.join(workDir, 'python-extract');
  await rm(extractDir, { recursive: true, force: true });
  await mkdir(extractDir, { recursive: true });
  run('tar', ['-xzf', archive, '-C', extractDir]);
  // python-build-standalone install_only ships a `python/` top-level dir.
  const innerPython = path.join(extractDir, 'python');
  if (!existsSync(innerPython)) {
    throw new Error('python-build-standalone tarball did not contain expected `python/` directory.');
  }
  // verbatimSymlinks keeps relative symlinks intact (otherwise Node rewrites
  // them to absolute paths pointing at the build-time source dir).
  await cp(innerPython, path.join(outputDir, 'python'), {
    recursive: true,
    dereference: false,
    verbatimSymlinks: true,
  });
  run(path.join(outputDir, 'python/bin/python3'), ['--version']);
  assertNoExternalDylibs(path.join(outputDir, 'python'), 'Python runtime');
}

async function stageNode(nodeCfg, outputDir, cacheDir, workDir) {
  const archive = await downloadWithSha(
    nodeCfg.sourceUrl,
    nodeCfg.sourceSha256,
    path.join(cacheDir, `node-${nodeCfg.version}.tar.xz`),
    'Node.js',
  );
  const extractDir = path.join(workDir, 'node-extract');
  await rm(extractDir, { recursive: true, force: true });
  await mkdir(extractDir, { recursive: true });
  run('tar', ['-xJf', archive, '-C', extractDir]);
  // Node tarball ships as node-vXX.YY.ZZ-darwin-arm64/
  const entries = await readdir(extractDir);
  const inner = entries.find((entry) => entry.startsWith('node-'));
  if (!inner) throw new Error('Node tarball did not contain expected `node-*` directory.');
  await cp(path.join(extractDir, inner), path.join(outputDir, 'node'), {
    recursive: true,
    dereference: false,
    verbatimSymlinks: true,
  });
  run(path.join(outputDir, 'node/bin/node'), ['--version']);
  run(path.join(outputDir, 'node/bin/npm'), ['--version']);
  assertNoExternalDylibs(path.join(outputDir, 'node'), 'Node runtime');
}

async function stageUv(uvCfg, outputDir, cacheDir, workDir) {
  const archive = await downloadWithSha(
    uvCfg.sourceUrl,
    uvCfg.sourceSha256,
    path.join(cacheDir, `uv-${uvCfg.version}.tar.gz`),
    'uv',
  );
  const extractDir = path.join(workDir, 'uv-extract');
  await rm(extractDir, { recursive: true, force: true });
  await mkdir(extractDir, { recursive: true });
  run('tar', ['-xzf', archive, '-C', extractDir]);
  // uv tarball ships `uv-aarch64-apple-darwin/uv` (newer releases) or just `uv` at top level.
  const candidates = [
    path.join(extractDir, 'uv'),
    path.join(extractDir, 'uv-aarch64-apple-darwin', 'uv'),
  ];
  const uvBinary = candidates.find((candidate) => existsSync(candidate));
  if (!uvBinary) {
    throw new Error(`uv binary not found in tarball; expected one of: ${candidates.join(', ')}`);
  }
  await cp(uvBinary, path.join(outputDir, 'uv'), { dereference: false });
  run(path.join(outputDir, 'uv'), ['--version']);
}

async function stageGit(gitCfg, outputDir) {
  // Apple's Command Line Tools git only links system dylibs (libz, libiconv,
  // libSystem, CoreServices, CoreFoundation — all in /usr/lib or /System), so
  // copying it straight is fully relocatable. No download, no dylib vendoring.
  for (const sourcePath of [gitCfg.systemBinPath, gitCfg.systemLibexecPath, gitCfg.systemSharePath]) {
    if (!existsSync(sourcePath)) {
      throw new Error(
        `Cannot vendor git: ${sourcePath} not found on build host. ` +
        `Install Xcode Command Line Tools (\`xcode-select --install\`).`,
      );
    }
  }
  const gitOut = path.join(outputDir, 'git');
  await mkdir(path.join(gitOut, 'bin'), { recursive: true });
  await mkdir(path.join(gitOut, 'libexec'), { recursive: true });
  await mkdir(path.join(gitOut, 'share'), { recursive: true });
  // Apple's CLT ships several real git binaries in usr/bin (git, git-shell,
  // scalar) plus a few symlinks (git-receive-pack, git-upload-archive,
  // git-upload-pack -> git). The libexec/git-core tree contains ~190
  // subcommands as hardlinks to a single ~7 MB binary, plus relative
  // symlinks into ../../bin/{git,git-shell,scalar}.
  //
  // Use `ditto` (macOS native) — it preserves hardlinks, relative symlinks,
  // perms, and xattrs. Node's fs.cp doesn't preserve hardlinks, which
  // would bloat libexec from ~10 MB to ~1 GB.
  //
  // We copy individual entries from bin (rather than the whole CLT bin dir,
  // which would drag in ~500 MB of unrelated tools). `cp -RP` on the
  // explicit list preserves the symlinks within the set.
  const cltBinDir = path.dirname(gitCfg.systemBinPath);
  const wantedBin = (await readdir(cltBinDir)).filter(
    (name) => name === 'git' || name.startsWith('git-') || name === 'scalar',
  );
  if (!wantedBin.includes('git')) {
    throw new Error(`Cannot vendor git: ${gitCfg.systemBinPath} not found.`);
  }
  run('cp', ['-RP', ...wantedBin.map((n) => path.join(cltBinDir, n)), path.join(gitOut, 'bin/')]);
  run('ditto', [gitCfg.systemLibexecPath, path.join(gitOut, 'libexec/git-core')]);
  run('ditto', [gitCfg.systemSharePath, path.join(gitOut, 'share/git-core')]);
  const versionLine = output(path.join(gitOut, 'bin/git'), ['--version']).split('\n')[0] || '';
  if (!versionLine.startsWith(gitCfg.expectedVersionPrefix)) {
    throw new Error(`Vendored git version mismatch. Expected prefix "${gitCfg.expectedVersionPrefix}", got: ${versionLine}`);
  }
  assertNoExternalDylibs(gitOut, 'Git runtime');
}

async function stageGh(ghCfg, outputDir, cacheDir, workDir) {
  const archive = await downloadWithSha(
    ghCfg.sourceUrl,
    ghCfg.sourceSha256,
    path.join(cacheDir, `gh-${ghCfg.version}-macos-arm64.zip`),
    'GitHub CLI',
  );
  const extractDir = path.join(workDir, 'gh-extract');
  await rm(extractDir, { recursive: true, force: true });
  await mkdir(extractDir, { recursive: true });
  run('ditto', ['-x', '-k', archive, extractDir]);
  const entries = await readdir(extractDir);
  const inner = entries.find((entry) => entry.startsWith(`gh_${ghCfg.version}_macOS_arm64`));
  if (!inner) {
    throw new Error(`GitHub CLI archive did not contain expected gh_${ghCfg.version}_macOS_arm64 directory.`);
  }
  const ghBinary = path.join(extractDir, inner, 'bin/gh');
  if (!existsSync(ghBinary)) {
    throw new Error('GitHub CLI archive did not contain expected bin/gh executable.');
  }
  const ghOut = path.join(outputDir, 'gh');
  await mkdir(path.join(ghOut, 'bin'), { recursive: true });
  await cp(ghBinary, path.join(ghOut, 'bin/gh'), { dereference: false });
  run(path.join(ghOut, 'bin/gh'), ['--version']);
  assertNoExternalDylibs(ghOut, 'GitHub CLI runtime');
}

async function stageSourceClone(sourceCfg, outputDir, workDir) {
  const cloneDir = path.join(outputDir, 'source.git');
  await rm(cloneDir, { recursive: true, force: true });
  // --no-local --no-hardlinks because we're cloning from a URL, not a local path,
  // but the flags make the intent explicit and harmless. --bare keeps the clone
  // free of a working tree (we'll materialize one at first launch).
  run('git', [
    'clone',
    '--bare',
    '--no-local',
    `--depth=${sourceCfg.depth ?? 50}`,
    sourceCfg.url,
    cloneDir,
  ], { cwd: workDir });
  // Also fetch the other release branches so channel switching works offline-fast.
  if (Array.isArray(sourceCfg.channels)) {
    for (const channel of sourceCfg.channels) {
      run('git', ['fetch', '--depth=' + (sourceCfg.depth ?? 50), 'origin', `${channel}:${channel}`], { cwd: cloneDir });
    }
  }
  await sanitizeBareClone(cloneDir);
  run('git', ['gc', '--aggressive', '--prune=now'], { cwd: cloneDir });
  // Ship the bare clone as a single tar archive. Two reasons:
  //   1. electron-builder's extraResources copy strips empty directories, and
  //      a bare repo after `git gc` has empty refs/ and hooks/. Without those
  //      git refuses to recognize the directory as a repo ("fatal: not a git
  //      repository"). Tar preserves them.
  //   2. The supervisor's bootstrap is agnostic to repo internals — it just
  //      extracts the archive and proceeds.
  const archivePath = path.join(outputDir, 'source.git.tar');
  run('tar', ['-cf', archivePath, '-C', outputDir, 'source.git']);
  await rm(cloneDir, { recursive: true, force: true });
  // Stamp which release channel this build defaults to on first bootstrap.
  // Channel name (stable|beta), not git branch. The supervisor maps channel→
  // branch (stable→main, beta→beta) when cloning.
  const defaultChannel = process.env.SENTINEL_BUILD_CHANNEL || 'stable';
  if (defaultChannel !== 'stable' && defaultChannel !== 'beta') {
    throw new Error(`SENTINEL_BUILD_CHANNEL must be "stable" or "beta", got "${defaultChannel}".`);
  }
  await writeFile(path.join(outputDir, 'default-channel'), `${defaultChannel}\n`);
  // Stamp the canonical upstream URL so the supervisor can redirect the
  // working tree's `origin` to it after the local clone — otherwise `git
  // fetch` would hit the frozen bundled bare instead of the live remote.
  await writeFile(path.join(outputDir, 'upstream-url'), `${sourceCfg.url}\n`);
}

async function sanitizeBareClone(cloneDir) {
  // Strip developer identity, hooks, and reflog so the shipped clone has no
  // ties to whatever account ran the build.
  await rm(path.join(cloneDir, 'hooks'), { recursive: true, force: true });
  await mkdir(path.join(cloneDir, 'hooks'), { recursive: true });
  await rm(path.join(cloneDir, 'logs'), { recursive: true, force: true });
  for (const key of ['user.name', 'user.email', 'user.signingkey', 'commit.gpgsign']) {
    spawnSync('git', ['config', '--unset', key], { cwd: cloneDir });
  }
  // Drop any credential entries that might have been copied via global config inheritance.
  spawnSync('git', ['config', '--remove-section', 'credential'], { cwd: cloneDir });
}

async function stageWheels(pythonCfg, outputDir, paths) {
  const backendDir = path.join(paths.repoRoot, 'apps/backend/sentinel');
  const wheelsDir = path.join(outputDir, 'wheels');
  const bundledPython = path.join(outputDir, 'python/bin/python3');
  await rm(wheelsDir, { recursive: true, force: true });
  await mkdir(wheelsDir, { recursive: true });
  // uv export collapses the lockfile into a flat requirements.txt with hashes.
  // --no-emit-project drops the editable install of the backend itself (pip
  // can't combine editables with hash-pinned requirements).
  run(path.join(outputDir, 'uv'), [
    'export',
    '--frozen',
    '--no-dev',
    '--no-emit-project',
    '--format', 'requirements-txt',
    '--output-file', path.join(wheelsDir, 'requirements.txt'),
  ], { cwd: backendDir });
  // uv 0.5 has no `pip download` subcommand; use bundled python's pip directly.
  // The bundled python (python-build-standalone) ships with pip preinstalled.
  run(bundledPython, [
    '-m', 'pip', 'download',
    '--no-cache-dir',
    '--dest', wheelsDir,
    '-r', path.join(wheelsDir, 'requirements.txt'),
  ], { cwd: backendDir });
  await writeFile(path.join(wheelsDir, '.complete'), `${new Date().toISOString()}\n`);
}

async function stageNodeModulesCache(outputDir, paths, workDir) {
  const frontendDir = path.join(paths.repoRoot, 'apps/frontend/sentinel');
  const stagingDir = path.join(workDir, 'frontend-cache');
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });
  // Copy only what npm ci needs to reproduce a deterministic install.
  for (const file of ['package.json', 'package-lock.json']) {
    await cp(path.join(frontendDir, file), path.join(stagingDir, file), { dereference: true });
  }
  // Use the bundled npm so the lockfile resolves against the same engine.
  const npmPath = path.join(outputDir, 'node/bin/npm');
  run(npmPath, ['ci', '--no-audit', '--no-fund', '--ignore-scripts'], { cwd: stagingDir });
  // Strip any auth files that might have been generated.
  await rm(path.join(stagingDir, '.npmrc'), { force: true });
  await rm(path.join(stagingDir, 'node_modules', '.package-lock.json'), { force: true });
  // gzip (not zstd) so BSD tar on the client can extract natively without
  // needing the zstd binary on PATH (which we don't ship and macOS doesn't
  // include by default).
  const archivePath = path.join(outputDir, 'node_modules-cache.tar.gz');
  run('tar', ['-czf', archivePath, '-C', stagingDir, 'node_modules']);
}

async function downloadWithSha(url, expectedSha, archivePath, label) {
  if (!expectedSha || expectedSha.startsWith('TODO')) {
    throw new Error(
      `[${label}] runtime.lock.json has no pinned sha256. Look up the SHA from the ` +
      `upstream release and commit it into runtime.lock.json before building.`,
    );
  }
  await mkdir(path.dirname(archivePath), { recursive: true });
  if (existsSync(archivePath)) {
    const actual = output('shasum', ['-a', '256', archivePath]).split(/\s+/)[0];
    if (actual === expectedSha) return archivePath;
    await rm(archivePath, { force: true });
  }
  run('curl', ['-fsSL', url, '-o', archivePath]);
  verifyArchiveSha256(archivePath, expectedSha, label);
  return archivePath;
}

function postgresConfig(config) {
  if (!config.postgres || typeof config.postgres !== 'object') {
    throw new Error('macos-arm64 runtime lock must define postgres.version, sourceUrl, and sourceSha256.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.postgres[key]) throw new Error(`macos-arm64 runtime lock is missing postgres.${key}.`);
  }
  return config.postgres;
}

function pgvectorConfig(config) {
  if (!config.pgvector || typeof config.pgvector !== 'object') {
    throw new Error('macos-arm64 runtime lock must define pgvector.version, repository, tag, and commitPrefix.');
  }
  for (const key of ['version', 'repository', 'tag', 'commitPrefix']) {
    if (!config.pgvector[key]) throw new Error(`macos-arm64 runtime lock is missing pgvector.${key}.`);
  }
  return config.pgvector;
}

async function patchPostgresDarwinInstallNames(sourceDir) {
  const makefile = path.join(sourceDir, 'src/Makefile.shlib');
  const current = await readFile(makefile, 'utf8');
  const bundledInstallNameRule =
    "LINK.shared\t\t= $(COMPILER) -dynamiclib -install_name '@rpath/lib$(NAME).$(SO_MAJOR_VERSION)$(DLSUFFIX)' $(version_link) $(exported_symbols_list)";
  const patched = current.replace(
    "LINK.shared\t\t= $(COMPILER) -dynamiclib -install_name '$(libdir)/lib$(NAME).$(SO_MAJOR_VERSION)$(DLSUFFIX)' $(version_link) $(exported_symbols_list)",
    bundledInstallNameRule,
  );
  if (patched === current) {
    throw new Error('Could not patch PostgreSQL Darwin install_name rule.');
  }
  await writeFile(makefile, patched);
}

function assertSystemOnlyDylibs(outputDir) {
  const files = output('find', [outputDir, '-type', 'f']).split('\n').filter(Boolean);
  const offenders = [];
  for (const file of files) {
    const deps = outputMaybe('otool', ['-L', file]);
    if (!deps) continue;
    for (const line of deps.split('\n')) {
      if (!line.startsWith('\t')) continue;
      const dep = line.trim().split(/\s+/)[0];
      if (!dep?.startsWith('/')) continue;
      if (!dep.startsWith('/usr/lib/') && !dep.startsWith('/System/Library/')) {
        offenders.push(`${file}: ${dep}`);
      }
    }
  }
  if (offenders.length) {
    throw new Error(`Postgres runtime has non-system absolute dylib references:\n${offenders.join('\n')}`);
  }
}

async function buildPostgresRuntime({ config, paths }) {
  const postgres = postgresConfig(config);
  const pgvector = pgvectorConfig(config);
  const workDir = path.join(paths.targetDir, 'work/postgres');
  const outputDir = path.join(paths.runtimeDir, 'postgres');
  const archivePath = path.join(workDir, `postgresql-${postgres.version}.tar.gz`);
  const sourceDir = path.join(workDir, `postgresql-${postgres.version}`);
  const pgvectorDir = path.join(workDir, 'pgvector');

  await rm(workDir, { recursive: true, force: true });
  await mkdir(workDir, { recursive: true });
  await rm(outputDir, { recursive: true, force: true });

  run('curl', ['-fsSL', postgres.sourceUrl, '-o', archivePath]);
  verifyArchiveSha256(archivePath, postgres.sourceSha256, 'PostgreSQL source');
  run('tar', ['-xzf', archivePath, '-C', workDir]);
  await patchPostgresDarwinInstallNames(sourceDir);
  run('./configure', [
    `--prefix=${outputDir}`,
    '--without-readline',
    '--without-zlib',
    '--without-icu',
    '--without-llvm',
    '--without-lz4',
    '--without-zstd',
  ], {
    cwd: sourceDir,
    env: {
      LDFLAGS: '-Wl,-rpath,@executable_path/../lib',
      LDFLAGS_SL: '-Wl,-rpath,@loader_path',
      MACOSX_DEPLOYMENT_TARGET: '13.0',
    },
  });
  const postgresBuildEnv = {
    LDFLAGS: '-Wl,-rpath,@executable_path/../lib',
    LDFLAGS_SL: '-Wl,-rpath,@loader_path',
    MACOSX_DEPLOYMENT_TARGET: '13.0',
  };
  run('make', ['-j', output('sysctl', ['-n', 'hw.ncpu'])], { cwd: sourceDir, env: postgresBuildEnv });
  run('make', ['install'], { cwd: sourceDir, env: postgresBuildEnv });

  run('git', ['clone', '--depth', '1', '--branch', pgvector.tag, pgvector.repository, pgvectorDir]);
  const pgvectorCommit = output('git', ['rev-parse', 'HEAD'], { cwd: pgvectorDir });
  if (!pgvectorCommit.startsWith(pgvector.commitPrefix)) {
    throw new Error(`pgvector ${pgvector.tag} resolved to ${pgvectorCommit}, expected prefix ${pgvector.commitPrefix}.`);
  }
  run('make', ['OPTFLAGS=', `PG_CONFIG=${path.join(outputDir, 'bin/pg_config')}`], {
    cwd: pgvectorDir,
    env: postgresBuildEnv,
  });
  run('make', ['install', `PG_CONFIG=${path.join(outputDir, 'bin/pg_config')}`], {
    cwd: pgvectorDir,
    env: postgresBuildEnv,
  });
  const vectorControl = path.join(outputDir, 'share/extension/vector.control');
  const vectorLib = path.join(outputDir, 'lib/vector.dylib');
  if (!existsSync(vectorControl)) {
    throw new Error(`Built Postgres runtime is missing ${vectorControl}`);
  }
  if (!existsSync(vectorLib)) {
    throw new Error(`Built Postgres runtime is missing ${vectorLib}`);
  }
  run(path.join(outputDir, 'bin/postgres'), ['--version']);
  await rm(path.join(outputDir, 'lib/pgxs'), { recursive: true, force: true });
  assertSystemOnlyDylibs(outputDir);
}

function parseOtoolDependencies(binary) {
  const linked = outputMaybe('otool', ['-L', binary]);
  if (!linked) return [];
  // Universal binaries produce one section per architecture, each starting with
  // `<path> (architecture X):` (no leading whitespace). Dependency lines are
  // indented with a tab. Keep only the indented lines.
  return linked
    .split('\n')
    .filter((line) => /^\s+\S/.test(line))
    .map((line) => line.trim().split(/\s+/)[0])
    .filter(Boolean);
}

function isSystemDylib(reference) {
  return reference.startsWith('/usr/lib/') || reference.startsWith('/System/Library/');
}

function assertNoExternalDylibs(outputDir, label = 'Runtime') {
  const files = output('find', [outputDir, '-type', 'f']).split('\n').filter(Boolean);
  const offenders = [];
  for (const file of files) {
    if (file.endsWith('.a')) continue;
    for (const reference of parseOtoolDependencies(file)) {
      if (!reference.startsWith('/')) continue;
      if (isSystemDylib(reference)) continue;
      offenders.push(`${file}: ${reference}`);
    }
  }
  if (offenders.length) {
    throw new Error(`${label} has non-system absolute dylib references:\n${offenders.join('\n')}`);
  }
}

export async function preparePackageAssets({ paths }) {
  const iconsetDir = path.join(paths.targetDir, 'icon.iconset');
  const iconPath = path.join(paths.targetDir, 'icon.icns');
  const source = path.join(paths.desktopDir, 'assets/app-icon.svg');
  await rm(iconsetDir, { recursive: true, force: true });
  await mkdir(iconsetDir, { recursive: true });
  await rm(iconPath, { force: true });

  const sizes = [
    [16, 'icon_16x16.png'],
    [32, 'icon_16x16@2x.png'],
    [32, 'icon_32x32.png'],
    [64, 'icon_32x32@2x.png'],
    [128, 'icon_128x128.png'],
    [256, 'icon_128x128@2x.png'],
    [256, 'icon_256x256.png'],
    [512, 'icon_256x256@2x.png'],
    [512, 'icon_512x512.png'],
    [1024, 'icon_512x512@2x.png'],
  ];
  for (const [size, name] of sizes) {
    run('sips', ['-s', 'format', 'png', '-z', String(size), String(size), source, '--out', path.join(iconsetDir, name)]);
  }
  run('iconutil', ['-c', 'icns', iconsetDir, '-o', iconPath]);
}

export async function verifyRuntime({ paths }) {
  const runtimeDir = paths.runtimeDir;
  // Skip Python's bundled site-packages — they carry vendored C extensions
  // (e.g. cffi) that reference dylibs by absolute path inside python's own lib/
  // dir. `assertNoExternalDylibs` already permits @loader_path/@rpath, so the
  // root-level check below catches anything escaping the bundle.
  const components = [
    ['Runtime seed (python)', path.join(runtimeDir, 'runtime-seed/python')],
    ['Runtime seed (node)', path.join(runtimeDir, 'runtime-seed/node')],
    ['Runtime seed (git)', path.join(runtimeDir, 'runtime-seed/git')],
    ['Runtime seed (gh)', path.join(runtimeDir, 'runtime-seed/gh')],
    ['Postgres runtime', path.join(runtimeDir, 'postgres')],
  ];
  for (const [label, componentDir] of components) {
    if (existsSync(componentDir)) {
      assertNoExternalDylibs(componentDir, label);
    }
  }
}

export function electronBuilderConfig({ paths, baseConfig }) {
  const extraResources = (baseConfig.extraResources || []).filter(
    (resource) => resource?.to !== 'backend' && resource?.to !== 'runtime-seed',
  );
  return {
    ...baseConfig,
    directories: {
      ...(baseConfig.directories || {}),
      output: 'dist',
    },
    extraResources: [
      ...extraResources,
      {
        from: path.join(paths.runtimeDir, 'runtime-seed'),
        to: 'runtime-seed',
      },
      {
        from: path.join(paths.runtimeDir, 'postgres'),
        to: 'postgres',
      },
    ],
    mac: {
      ...(baseConfig.mac || {}),
      icon: path.join(paths.targetDir, 'icon.icns'),
      entitlements: path.join(paths.desktopDir, 'scripts/entitlements/mac.plist'),
      entitlementsInherit: path.join(paths.desktopDir, 'scripts/entitlements/mac.inherit.plist'),
    },
    dmg: {
      ...(baseConfig.dmg || {}),
      icon: path.join(paths.targetDir, 'icon.icns'),
      badgeIcon: path.join(paths.targetDir, 'icon.icns'),
    },
  };
}

export function electronBuilderArgs() {
  return ['--mac', 'dmg', '--arm64'];
}
