import { existsSync } from 'node:fs';
import { cp, mkdir, readdir, rename, rm, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { stageWorkspaceImages, verifyWorkspaceImages, workspaceImageSource } from '../workspace-images/stage.mjs';

export const runtimeBuildVersion = 7;

export function workspaceRuntimeManifest(config, kernel, images) {
  // Compiler containers are build inputs, never workspace boot requirements.
  const { buildImage, glibcBuildImage, ...runtimeConfig } = config;
  return { protocol: 1, ...runtimeConfig, ...kernel, workspaceImages: images };
}

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
  const required = ['curl', 'ditto', 'shasum', 'tar'];
  const missing = required.filter((command) => !commandExists(command));
  if (missing.length) {
    throw new Error(`Missing macOS runtime build tools: ${missing.join(', ')}`);
  }
}

export function runtimeRequirements() {
  return {
    'workspace-runtime': ['sentinel-workspace-runtime', 'manifest.json', 'kernel',
      'workspace-images/manifest.json',
      ...['alpine', 'ubuntu', 'debian'].flatMap(name => [
        `workspace-images/${name}/index.json`, `workspace-images/${name}/oci-layout`,
      ]),
      'graphics/sentinel-desktop-renderer',
      'graphics/libEGL.dylib', 'graphics/libgallium-26.3.0-devel.dylib',
      'graphics/libepoxy.0.dylib', 'graphics/libvirglrenderer.1.dylib',
      'graphics/libvulkan_kosmickrisp.dylib', 'graphics/libvulkan.1.dylib',
      'graphics/libSPIRV-Tools.dylib', 'graphics/icd.json',
      'graphics/install-guest.py', 'graphics/gpu-start.sh',
      'graphics/install-browser-graphics.py', 'graphics/gpu-2404.snap', 'graphics/gpu-2404.json',
      'graphics/mesa-linux-arm64.tar.xz', 'graphics/mesa-linux-arm64.json',
      'graphics/mesa-linux-arm64-glibc.tar.xz', 'graphics/mesa-linux-arm64-glibc.json',
      'graphics/kernel-linux-arm64.tar.xz', 'graphics/kernel-linux-arm64.json',
      'graphics/desktop-runtime.tar.xz', 'graphics/desktop-runtime.json'],
    'runtime-seed': [
      'python/bin/python3',
      'git/bin/git',
      'gh/bin/gh',
    ],
  };
}

export function runtimeComponents({ config, paths }) {
  const requirements = runtimeRequirements();
  const images = workspaceImageSource(paths.desktopDir);
  return [
    {
      name: 'workspace-runtime',
      version: 2,
      config: config.workspaceRuntime,
      required: requirements['workspace-runtime'],
      inputs: [
        ...['native/macos/Package.swift', 'native/macos/Package.resolved', 'native/macos/Sources',
          'native/graphics', 'scripts/packaging/graphics', 'native/workspace-images',
          'scripts/packaging/workspace-images', 'runtime.lock.json',
          'packaging/macos/workspace-runtime.plist'].map(file => path.join(paths.desktopDir, file)),
        ...images.manifests,
      ],
      build: buildWorkspaceRuntime,
    },
    {
      name: 'runtime-seed',
      config: {
        python: pythonRuntimeConfig(config),
        git: gitRuntimeConfig(config),
        gh: ghRuntimeConfig(config),
      },
      required: requirements['runtime-seed'],
      inputs: [],
      build: buildRuntimeSeed,
    },
  ];
}

export async function buildWorkspaceRuntime({ config, paths, configuration = "release", workspaceImages }) {
  const cfg = config.workspaceRuntime;
  if (!cfg?.kernelFileSha256 || !cfg?.kernelSha256 || !cfg?.initImage) {
    throw new Error('runtime.lock.json must pin the workspace runtime assets.');
  }
  // Fail before compilation if the native Linux image bundle is missing/stale.
  const dest = path.join(paths.runtimeDir, 'workspace-runtime');
  const images = stageWorkspaceImages(paths.desktopDir, dest, workspaceImages);
  const source = path.join(paths.desktopDir, 'native/macos');
  const manifest = await readFile(path.join(source, 'Package.swift'), 'utf8');
  if (!manifest.includes(`exact: "${cfg.containerizationVersion}"`)) {
    throw new Error('The Swift package and runtime lock must pin the same Containerization release.');
  }
  const scratch = path.join(paths.targetDir, 'swift');
  run('swift', ['build', '--package-path', source, '--scratch-path', scratch, '-c', configuration, '--jobs', '4', '--force-resolved-versions']);
  const binDir = output('swift', ['build', '--package-path', source, '--scratch-path', scratch, '-c', configuration, '--show-bin-path']);
  await mkdir(dest, { recursive: true });
  const binary = path.join(dest, 'sentinel-workspace-runtime');
  const staging = binary + '.next';
  await cp(path.join(binDir, 'sentinel-workspace-runtime'), staging);
  // Development verification needs this entitlement too. electron-builder signs
  // the packaged helper again using the app's identity and matching entitlement.
  run('codesign', ['--force', '--sign', '-', '--entitlements', path.join(paths.desktopDir, 'packaging/macos/workspace-runtime.plist'), staging]);
  // A running VM keeps the previous signed executable until its owner exits.
  await rename(staging, binary);
  run('python3', [path.join(paths.desktopDir, 'scripts/packaging/graphics/build.py'), path.join(dest, 'graphics')]);
  const kernel = JSON.parse(await readFile(path.join(dest, 'kernel-manifest.json'), 'utf8'));
  await writeFile(path.join(dest, 'manifest.json'), JSON.stringify(workspaceRuntimeManifest(cfg, kernel, images), null, 2) + '\n');
  assertNoExternalDylibs(dest, 'Workspace runtime');
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

function verifyArchiveSha256(archivePath, expected, label) {
  const actual = output('shasum', ['-a', '256', archivePath]).split(/\s+/)[0];
  if (actual !== expected) {
    throw new Error(`${label} checksum mismatch. Expected ${expected}, got ${actual}.`);
  }
}

async function buildRuntimeSeed({ config, paths }) {
  // The shell DMG bundles only version-independent tools: the Python
  // interpreter (runs the frozen payload), git + gh (agent runtime tools).
  // The updatable app code lives in the payload, not here.
  const cfg = {
    python: pythonRuntimeConfig(config),
    git: gitRuntimeConfig(config),
    gh: ghRuntimeConfig(config),
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
  await stageGit(cfg.git, outputDir);
  await stageGh(cfg.gh, outputDir, downloadCacheDir, workDir);
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
  verifyPythonSQLite(path.join(outputDir, 'python/bin/python3'));
  assertNoExternalDylibs(path.join(outputDir, 'python'), 'Python runtime');
}

// runtime.lock.json pins git to the Command Line Tools layout. GitHub's macOS
// runners ship full Xcode whose active developer dir is elsewhere (and the bare
// CLT path may be absent), so when the pinned paths don't exist we fall back to
// `xcode-select -p`. Both layouts expose the same usr/{bin,libexec,share} tree
// of system-linked git binaries, so vendoring stays fully relocatable.
function resolveGitPaths(gitCfg) {
  if (existsSync(gitCfg.systemBinPath)) {
    return {
      bin: gitCfg.systemBinPath,
      libexec: gitCfg.systemLibexecPath,
      share: gitCfg.systemSharePath,
    };
  }
  const devDir = outputMaybe('xcode-select', ['-p']);
  const candidateBin = devDir ? path.join(devDir, 'usr/bin/git') : '';
  if (!candidateBin || !existsSync(candidateBin)) {
    throw new Error(
      `Cannot vendor git: ${gitCfg.systemBinPath} not found and no git under the active ` +
      `developer dir (${devDir || 'xcode-select -p returned nothing'}). ` +
      `Install Xcode Command Line Tools (\`xcode-select --install\`).`,
    );
  }
  return {
    bin: candidateBin,
    libexec: path.join(devDir, 'usr/libexec/git-core'),
    share: path.join(devDir, 'usr/share/git-core'),
  };
}

async function stageGit(gitCfg, outputDir) {
  // Apple's Command Line Tools git only links system dylibs (libz, libiconv,
  // libSystem, CoreServices, CoreFoundation — all in /usr/lib or /System), so
  // copying it straight is fully relocatable. No download, no dylib vendoring.
  const gitPaths = resolveGitPaths(gitCfg);
  // bin + libexec/git-core (the subcommands) are essential. share/git-core only
  // holds commit-message templates and localized strings — git runs fine
  // without it, and Xcode's developer dir doesn't always ship it — so it's
  // best-effort below rather than required here.
  for (const sourcePath of [gitPaths.bin, gitPaths.libexec]) {
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
  const cltBinDir = path.dirname(gitPaths.bin);
  const wantedBin = (await readdir(cltBinDir)).filter(
    (name) => name === 'git' || name.startsWith('git-') || name === 'scalar',
  );
  if (!wantedBin.includes('git')) {
    throw new Error(`Cannot vendor git: ${gitPaths.bin} not found.`);
  }
  run('cp', ['-RP', ...wantedBin.map((n) => path.join(cltBinDir, n)), path.join(gitOut, 'bin/')]);
  run('ditto', [gitPaths.libexec, path.join(gitOut, 'libexec/git-core')]);
  if (existsSync(gitPaths.share)) {
    run('ditto', [gitPaths.share, path.join(gitOut, 'share/git-core')]);
  }
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

function verifyPythonSQLite(python) {
  run(python, ['-c', 'import sqlite3; db = sqlite3.connect(":memory:"); db.enable_load_extension(True); db.enable_load_extension(False); db.close()']);
}

export async function verifyRuntime({ paths }) {
  const runtimeDir = paths.runtimeDir;
  // Cached component stamps cannot detect removed/corrupted OCI layer bytes.
  verifyWorkspaceImages(paths.desktopDir, path.join(runtimeDir, 'workspace-runtime'));
  const pythonDir = path.join(runtimeDir, 'runtime-seed/python');
  if (existsSync(pythonDir)) {
    verifyPythonSQLite(path.join(pythonDir, 'bin/python3'));
  }
  // Skip Python's bundled site-packages — they carry vendored C extensions
  // (e.g. cffi) that reference dylibs by absolute path inside python's own lib/
  // dir. `assertNoExternalDylibs` already permits @loader_path/@rpath, so the
  // root-level check below catches anything escaping the bundle.
  const components = [
    ['Runtime seed (python)', path.join(runtimeDir, 'runtime-seed/python')],
    ['Runtime seed (git)', path.join(runtimeDir, 'runtime-seed/git')],
    ['Runtime seed (gh)', path.join(runtimeDir, 'runtime-seed/gh')],
  ];
  for (const [label, componentDir] of components) {
    if (existsSync(componentDir)) {
      assertNoExternalDylibs(componentDir, label);
    }
  }
}

export function electronBuilderConfig({ paths, baseConfig }) {
  const extraResources = (baseConfig.extraResources || []).filter(
    (resource) => resource?.to !== 'backend' && resource?.to !== 'runtime-seed' && resource?.to !== 'workspace-runtime',
  );
  return {
    ...baseConfig,
    directories: {
      ...(baseConfig.directories || {}),
      output: 'release',
    },
    afterSign: path.join(paths.desktopDir, 'scripts/publishing/verify-app-signature.cjs'),
    extraResources: [
      ...extraResources,
      { from: path.join(paths.runtimeDir, 'workspace-runtime'), to: 'workspace-runtime' },
      {
        from: path.join(paths.runtimeDir, 'runtime-seed'),
        to: 'runtime-seed',
      },
    ],
    mac: {
      ...(baseConfig.mac || {}),
      ...(process.env.SENTINEL_INTERNAL_BUILD === '1' ? { identity: '-' } : {}),
      icon: path.join(paths.targetDir, 'icon.icns'),
      entitlements: path.join(paths.desktopDir, 'packaging/macos/mac.plist'),
      entitlementsInherit: path.join(paths.desktopDir, 'packaging/macos/mac.inherit.plist'),
      // osx-sign otherwise invokes codesign on hundreds of Python bytecode and
      // other data files. These are sealed as resources by the app signature;
      // only executable code needs an individual signature.
      signIgnore: ['\\.(pyc|pyo|whl|gif|ico|png|pickle|exe|gz|xz|pak|dat|asar|icns|woff2?|ttf)$'],
      binaries: ['Contents/Resources/workspace-runtime/sentinel-workspace-runtime', 'Contents/Resources/workspace-runtime/graphics/sentinel-desktop-renderer'],
      extendInfo: { ...(baseConfig.mac?.extendInfo || {}), LSMinimumSystemVersion: '26.0' },
    },
    dmg: {
      ...(baseConfig.dmg || {}),
      icon: path.join(paths.targetDir, 'icon.icns'),
      badgeIcon: path.join(paths.targetDir, 'icon.icns'),
      // Sentinel updates through its signed payload/index channel, not
      // electron-updater. Avoid hashing the entire DMG for an unused blockmap.
      writeUpdateInfo: false,
      // Sentinel requires macOS 26. LZFSE avoids legacy zlib compression cost.
      format: 'ULFO',
    },
  };
}

export function electronBuilderArgs() {
  return ['--mac', 'dmg', '--arm64'];
}
