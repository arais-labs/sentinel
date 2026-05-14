import { existsSync } from 'node:fs';
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

export const runtimeBuildVersion = 1;

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
  const required = ['clang', 'curl', 'git', 'install_name_tool', 'make', 'ninja', 'pkg-config', 'shasum', 'tar', 'uv'];
  const missing = required.filter((command) => !commandExists(command));
  if (missing.length) {
    throw new Error(`Missing macOS runtime build tools: ${missing.join(', ')}`);
  }
}

export function runtimeRequirements() {
  return {
    python: ['bin/python3'],
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
    qemu: [
      'bin/qemu-system-aarch64',
      'bin/qemu-img',
      'share/qemu/edk2-aarch64-code.fd',
      'share/qemu/edk2-arm-vars.fd',
      'build-base-image.sh',
      'validate-base-image.sh',
      'cloud-init/user-data.tpl',
      'cloud-init/meta-data',
      'provision/runtime-base.sh',
    ],
  };
}

export function runtimeComponents({ config, paths }) {
  const requirements = runtimeRequirements();
  const backendDir = path.join(paths.repoRoot, 'apps/backend/sentinel');
  const qemuRuntimeDir = path.join(paths.repoRoot, 'infra/runtime/qemu');
  return [
    {
      name: 'python',
      config: pythonConfig(config),
      required: requirements.python,
      inputs: [
        path.join(backendDir, 'pyproject.toml'),
        path.join(backendDir, 'uv.lock'),
      ],
      build: buildPythonRuntime,
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
    {
      name: 'qemu',
      config: qemuConfig(config),
      required: requirements.qemu,
      inputs: [
        path.join(qemuRuntimeDir, 'build-base-image.sh'),
        path.join(qemuRuntimeDir, 'validate-base-image.sh'),
        path.join(qemuRuntimeDir, 'cloud-init'),
        path.join(qemuRuntimeDir, 'provision'),
      ],
      build: buildQemuRuntime,
    },
  ];
}

function pythonConfig(config) {
  if (!config.python || typeof config.python !== 'object') {
    throw new Error('macos-arm64 runtime lock must define python.version, sourceUrl, and sourceSha256.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.python[key]) throw new Error(`macos-arm64 runtime lock is missing python.${key}.`);
  }
  return config.python;
}

function pythonMajorMinor(version) {
  return version.split('.').slice(0, 2).join('.');
}

async function prunePythonRuntime(runtimeDir) {
  const binDir = path.join(runtimeDir, 'bin');
  for (const entry of await readdir(binDir)) {
    if (!/^python(\d+(\.\d+)?)?$|^pip(\d+(\.\d+)?)?$/.test(entry)) {
      await rm(path.join(binDir, entry), { recursive: true, force: true });
    }
  }
  run('find', [runtimeDir, '-type', 'd', '-name', '__pycache__', '-prune', '-exec', 'rm', '-rf', '{}', '+']);
  run('find', [runtimeDir, '-name', '*.pyc', '-delete']);
  run('find', [runtimeDir, '-name', 'direct_url.json', '-delete']);
}

function verifyArchiveSha256(archivePath, expected, label) {
  const actual = output('shasum', ['-a', '256', archivePath]).split(/\s+/)[0];
  if (actual !== expected) {
    throw new Error(`${label} checksum mismatch. Expected ${expected}, got ${actual}.`);
  }
}

function installNameChange(binary, from, to) {
  const linked = output('otool', ['-L', binary]);
  if (!linked.includes(from)) return;
  run('install_name_tool', ['-change', from, to, binary]);
}

function installNameAddRpath(binary, rpath) {
  const current = output('otool', ['-l', binary]);
  if (current.includes(rpath)) return;
  run('install_name_tool', ['-add_rpath', rpath, binary]);
}

function loaderPathToLib(file, libDir) {
  const relative = path.relative(path.dirname(file), libDir) || '.';
  return `@loader_path/${relative.split(path.sep).join('/')}`;
}

function loaderPathToFile(fromFile, toFile) {
  const relative = path.relative(path.dirname(fromFile), toFile);
  return `@loader_path/${relative.split(path.sep).join('/')}`;
}

function relocatePythonRuntime(outputDir, version) {
  const majorMinor = pythonMajorMinor(version);
  const libName = `libpython${majorMinor}.dylib`;
  const libPath = path.join(outputDir, 'lib', libName);
  const pythonBinary = path.join(outputDir, 'bin', `python${majorMinor}`);
  const linkedLib = path.join(outputDir, 'lib', libName);
  if (!existsSync(libPath)) throw new Error(`Built Python is missing ${libPath}`);
  if (!existsSync(pythonBinary)) throw new Error(`Built Python is missing ${pythonBinary}`);
  run('install_name_tool', ['-id', `@rpath/${libName}`, libPath]);
  installNameChange(pythonBinary, linkedLib, `@rpath/${libName}`);
  installNameAddRpath(pythonBinary, '@executable_path/../lib');
  const extensionFiles = output('find', [path.join(outputDir, 'lib'), '-name', '*.so']).split('\n').filter(Boolean);
  for (const extension of extensionFiles) {
    installNameChange(extension, linkedLib, `@rpath/${libName}`);
  }
}

async function rewritePythonInstallMetadata(outputDir, version) {
  const majorMinor = pythonMajorMinor(version);
  const sysconfigData = path.join(outputDir, 'lib', `python${majorMinor}`, '_sysconfigdata__darwin_darwin.py');
  if (existsSync(sysconfigData)) {
    const current = await readFile(sysconfigData, 'utf8');
    const placeholder = '__SENTINEL_BUNDLED_PYTHON__';
    const relocationPatch = `

# Sentinel packages CPython as a relocatable app resource. Configure-time
# prefixes point at the temporary build tree, so rewrite them at import time.
import sys as _sentinel_sys
_sentinel_prefix = _sentinel_sys.base_prefix
for _sentinel_key, _sentinel_value in list(build_time_vars.items()):
    if isinstance(_sentinel_value, str):
        build_time_vars[_sentinel_key] = _sentinel_value.replace(${JSON.stringify(placeholder)}, _sentinel_prefix)
`;
    await writeFile(sysconfigData, `${current.replaceAll(outputDir, placeholder)}${relocationPatch}`);
  }

  const configDir = path.join(outputDir, 'lib', `python${majorMinor}`, `config-${majorMinor}-darwin`);
  const pythonConfig = path.join(configDir, 'python-config.py');
  if (existsSync(pythonConfig)) {
    const current = await readFile(pythonConfig, 'utf8');
    await writeFile(pythonConfig, current.replace(/^#!.*python[^\n]*/u, '#!/usr/bin/env python3'));
  }
  const makefile = path.join(configDir, 'Makefile');
  if (existsSync(makefile)) {
    const current = await readFile(makefile, 'utf8');
    await writeFile(makefile, current.replaceAll(outputDir, '__SENTINEL_BUNDLED_PYTHON__'));
  }
}

function pythonEnv(outputDir) {
  return {
    PYTHONHOME: outputDir,
    PYTHONNOUSERSITE: '1',
    PATH: `${path.join(outputDir, 'bin')}:${process.env.PATH ?? ''}`,
  };
}

async function buildPythonRuntime({ config, paths }) {
  const python = pythonConfig(config);
  const backendDir = path.join(paths.repoRoot, 'apps/backend/sentinel');
  const workDir = path.join(paths.targetDir, 'work/python');
  const outputDir = path.join(paths.runtimeDir, 'python');
  const archivePath = path.join(workDir, `Python-${python.version}.tgz`);
  const sourceDir = path.join(workDir, `Python-${python.version}`);
  const requirementsPath = path.join(workDir, 'requirements.txt');
  await rm(workDir, { recursive: true, force: true });
  await mkdir(workDir, { recursive: true });
  await rm(outputDir, { recursive: true, force: true });

  run('curl', ['-fsSL', python.sourceUrl, '-o', archivePath]);
  verifyArchiveSha256(archivePath, python.sourceSha256, 'Python source');
  run('tar', ['-xzf', archivePath, '-C', workDir]);
  run('./configure', [`--prefix=${outputDir}`, '--enable-shared', '--with-ensurepip=install'], {
    cwd: sourceDir,
    env: {
      MACOSX_DEPLOYMENT_TARGET: '13.0',
      LDFLAGS: '-Wl,-rpath,@executable_path/../lib',
    },
  });
  run('make', ['-j', output('sysctl', ['-n', 'hw.ncpu'])], { cwd: sourceDir });
  run('make', ['install'], { cwd: sourceDir });
  relocatePythonRuntime(outputDir, python.version);
  await rewritePythonInstallMetadata(outputDir, python.version);

  const runtimePython = path.join(outputDir, 'bin/python3');
  run('uv', [
    'export',
    '--frozen',
    '--no-dev',
    '--no-editable',
    '--format',
    'requirements.txt',
    '--output-file',
    requirementsPath,
  ], { cwd: backendDir });
  run('uv', ['pip', 'sync', '--python', runtimePython, '--system', '--strict', requirementsPath], {
    cwd: backendDir,
    env: pythonEnv(outputDir),
  });
  run(runtimePython, [
    '-c',
    "import sys, sysconfig, asyncpg, fastapi, pgvector, uvicorn; assert sys.prefix == sys.base_prefix; assert sysconfig.get_config_var('LIBDIR').startswith(sys.base_prefix)",
  ], { env: pythonEnv(outputDir) });
  await vendorPythonDylibs(outputDir);
  assertNoExternalDylibs(outputDir, 'Python runtime');
  await prunePythonRuntime(outputDir);
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

function qemuConfig(config) {
  if (!config.qemu || typeof config.qemu !== 'object') {
    throw new Error('macos-arm64 runtime lock must define qemu.version, sourceUrl, and sourceSha256.');
  }
  for (const key of ['version', 'sourceUrl', 'sourceSha256']) {
    if (!config.qemu[key]) throw new Error(`macos-arm64 runtime lock is missing qemu.${key}.`);
  }
  return config.qemu;
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
  assertSystemOnlyDylibs(outputDir);
}

function parseOtoolDependencies(binary) {
  const linked = outputMaybe('otool', ['-L', binary]);
  if (!linked) return [];
  return linked
    .split('\n')
    .slice(1)
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

async function vendorPythonDylibs(outputDir) {
  const files = output('find', [outputDir, '-type', 'f']).split('\n').filter(Boolean);
  rewritePythonSelfInstallNames(files);
  rewritePythonWheelLocalDylibs(files);
  const entrypoints = files.filter((file) => !file.endsWith('.a') && parseOtoolDependencies(file).length > 0);
  await vendorDylibClosure(entrypoints, path.join(outputDir, 'lib'));
}

function rewritePythonSelfInstallNames(files) {
  for (const file of files) {
    for (const reference of parseOtoolDependencies(file)) {
      if (!reference.startsWith('/')) continue;
      if (path.resolve(reference) !== path.resolve(file)) continue;
      installNameChange(file, reference, `@loader_path/${path.basename(file)}`);
    }
  }
}

function rewritePythonWheelLocalDylibs(files) {
  for (const file of files) {
    for (const reference of parseOtoolDependencies(file)) {
      if (!reference.startsWith('/DLC/')) continue;
      const suffix = reference.slice('/DLC/'.length);
      const matches = files.filter((candidate) => candidate.endsWith(suffix));
      if (matches.length !== 1) {
        throw new Error(`Python dependency ${reference} maps to ${matches.length} bundled files`);
      }
      installNameChange(file, reference, loaderPathToFile(file, matches[0]));
    }
  }
}

async function vendorDylibClosure(entrypoints, libDir) {
  await mkdir(libDir, { recursive: true });
  const originalToBundled = new Map();
  const bundledByName = new Map();
  const queue = [...entrypoints];

  while (queue.length) {
    const current = queue.shift();
    for (const reference of parseOtoolDependencies(current)) {
      if (!reference.startsWith('/') || isSystemDylib(reference)) continue;
      const name = path.basename(reference);
      const bundled = path.join(libDir, name);
      const existing = bundledByName.get(name);
      if (existing && existing !== reference) {
        throw new Error(`QEMU dependency basename collision for ${name}: ${existing} and ${reference}`);
      }
      if (!originalToBundled.has(reference)) {
        if (!existsSync(reference)) {
          throw new Error(`QEMU dependency does not exist on builder: ${reference}`);
        }
        await cp(reference, bundled, { dereference: true });
        originalToBundled.set(reference, bundled);
        bundledByName.set(name, reference);
        queue.push(bundled);
      }
    }
  }

  const bundledFiles = [...originalToBundled.values()];
  for (const dylib of bundledFiles) {
    run('install_name_tool', ['-id', `@rpath/${path.basename(dylib)}`, dylib]);
  }

  for (const binary of entrypoints) {
    installNameAddRpath(binary, loaderPathToLib(binary, libDir));
  }
  for (const file of [...entrypoints, ...bundledFiles]) {
    const replacementPrefix = bundledFiles.includes(file) ? '@loader_path' : '@rpath';
    for (const [original, bundled] of originalToBundled.entries()) {
      installNameChange(file, original, `${replacementPrefix}/${path.basename(bundled)}`);
    }
  }
}

async function pruneQemuRuntime(outputDir) {
  const binDir = path.join(outputDir, 'bin');
  const keep = new Set(['qemu-system-aarch64', 'qemu-img']);
  if (existsSync(binDir)) {
    for (const entry of await readdir(binDir)) {
      if (!keep.has(entry)) {
        await rm(path.join(binDir, entry), { recursive: true, force: true });
      }
    }
  }
  await rm(path.join(outputDir, 'share/doc'), { recursive: true, force: true });
  await rm(path.join(outputDir, 'share/man'), { recursive: true, force: true });
}

async function buildQemuRuntime({ config, paths }) {
  const qemu = qemuConfig(config);
  const outputDir = path.join(paths.runtimeDir, 'qemu');
  const sourceDir = path.join(paths.repoRoot, 'infra/runtime/qemu');
  const workDir = path.join(paths.targetDir, 'work/qemu');
  const archivePath = path.join(workDir, `qemu-${qemu.version}.tar.xz`);
  const extractedDir = path.join(workDir, `qemu-${qemu.version}`);
  const buildDir = path.join(extractedDir, 'build-sentinel');
  await rm(outputDir, { recursive: true, force: true });
  await rm(workDir, { recursive: true, force: true });
  await mkdir(workDir, { recursive: true });

  run('curl', ['-fsSL', qemu.sourceUrl, '-o', archivePath]);
  verifyArchiveSha256(archivePath, qemu.sourceSha256, 'QEMU source');
  run('tar', ['-xf', archivePath, '-C', workDir]);
  await mkdir(buildDir, { recursive: true });
  run('../configure', [
    `--prefix=${outputDir}`,
    '--target-list=aarch64-softmmu',
    '--without-default-features',
    '--enable-system',
    '--enable-tools',
    '--enable-hvf',
    '--enable-tcg',
    '--enable-pixman',
    '--enable-slirp',
    '--enable-fdt=internal',
    '--enable-qcow1',
    '--enable-vdi',
    '--enable-vmdk',
    '--enable-vpc',
    '--enable-vhdx',
    '--disable-docs',
    '--disable-gtk',
    '--disable-sdl',
    '--disable-cocoa',
    '--disable-vnc',
    '--disable-curl',
    '--disable-libssh',
    '--disable-gnutls',
    '--disable-nettle',
    '--disable-lzo',
    '--disable-snappy',
    '--disable-zstd',
    '--disable-png',
    '--disable-capstone',
    '--disable-libusb',
    '--disable-vde',
    '--disable-bzip2',
    '--disable-lzfse',
    '--disable-dmg',
  ], {
    cwd: buildDir,
    env: {
      MACOSX_DEPLOYMENT_TARGET: '13.0',
    },
  });
  run('make', ['-j', output('sysctl', ['-n', 'hw.ncpu'])], { cwd: buildDir });
  run('make', ['install'], { cwd: buildDir });
  await pruneQemuRuntime(outputDir);

  const qemuSystem = path.join(outputDir, 'bin/qemu-system-aarch64');
  const qemuImg = path.join(outputDir, 'bin/qemu-img');
  if (!existsSync(qemuSystem) || !existsSync(qemuImg)) {
    throw new Error('Built QEMU runtime is missing qemu-system-aarch64 or qemu-img.');
  }
  const versionLine = output(qemuSystem, ['--version']).split('\n')[0] || '';
  if (!versionLine.includes(qemu.version)) {
    throw new Error(`QEMU version mismatch. Expected ${qemu.version}, got: ${versionLine}`);
  }
  for (const firmware of ['edk2-aarch64-code.fd', 'edk2-arm-vars.fd']) {
    const built = path.join(outputDir, 'share/qemu', firmware);
    if (!existsSync(built)) throw new Error(`Built QEMU runtime is missing firmware: ${built}`);
  }
  await vendorDylibClosure([qemuSystem, qemuImg], path.join(outputDir, 'lib'));
  assertNoExternalDylibs(outputDir, 'QEMU runtime');

  for (const script of ['build-base-image.sh', 'validate-base-image.sh']) {
    await cp(path.join(sourceDir, script), path.join(outputDir, script), { dereference: true });
  }
  await cp(path.join(sourceDir, 'cloud-init'), path.join(outputDir, 'cloud-init'), { recursive: true, dereference: true });
  await cp(path.join(sourceDir, 'provision'), path.join(outputDir, 'provision'), { recursive: true, dereference: true });
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
  const components = [
    ['Python runtime', path.join(runtimeDir, 'python')],
    ['Postgres runtime', path.join(runtimeDir, 'postgres')],
    ['QEMU runtime', path.join(runtimeDir, 'qemu')],
  ];
  for (const [label, componentDir] of components) {
    if (existsSync(componentDir)) {
      assertNoExternalDylibs(componentDir, label);
    }
  }
}

export function electronBuilderConfig({ paths, baseConfig }) {
  return {
    ...baseConfig,
    directories: {
      ...(baseConfig.directories || {}),
      output: 'dist',
    },
    extraResources: [
      ...(baseConfig.extraResources || []),
      {
        from: path.join(paths.runtimeDir, 'python'),
        to: 'python',
      },
      {
        from: path.join(paths.runtimeDir, 'postgres'),
        to: 'postgres',
      },
      {
        from: path.join(paths.runtimeDir, 'qemu'),
        to: 'runtime/qemu',
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
