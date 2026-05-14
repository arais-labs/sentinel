#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(__filename);
const desktopDir = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopDir, '../../..');
const frontendDir = path.join(repoRoot, 'apps/frontend/sentinel');
const lockPath = path.join(desktopDir, 'runtime.lock.json');
const packageJsonPath = path.join(desktopDir, 'package.json');
const buildRoot = path.join(desktopDir, 'build');
const distRoot = path.join(desktopDir, 'dist');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: desktopDir,
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
    cwd: desktopDir,
    encoding: 'utf8',
    env: process.env,
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr || result.error?.message || 'unknown error'}`);
  }
  return result.stdout.trim();
}

function currentTarget() {
  if (process.platform === 'darwin') return `macos-${process.arch}`;
  return `${process.platform}-${process.arch}`;
}

function parseArgs(argv) {
  const args = { command: argv[2], target: currentTarget(), forceRuntime: false };
  for (let i = 3; i < argv.length; i += 1) {
    const value = argv[i];
    if (value === '--target') {
      args.target = argv[i + 1] || '';
      i += 1;
    } else if (value.startsWith('--target=')) {
      args.target = value.slice('--target='.length);
    } else if (value === '--force-runtime') {
      args.forceRuntime = true;
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!args.target) throw new Error('Target cannot be empty');
  return args;
}

function targetDir(target) {
  return path.join(buildRoot, target);
}

function runtimeDir(target) {
  return path.join(targetDir(target), 'runtime');
}

function stampDir(target) {
  return path.join(targetDir(target), 'stamps');
}

function runtimeStampPath(target, componentName) {
  return path.join(stampDir(target), `runtime-${componentName}.json`);
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function hashObject(value) {
  return createHash('sha256').update(stableJson(value)).digest('hex');
}

async function hashFile(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

async function listInputFiles(inputPath) {
  const info = await stat(inputPath);
  if (info.isFile()) return [inputPath];
  if (!info.isDirectory()) return [];
  const files = [];
  const entries = await readdir(inputPath, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === '__pycache__' || entry.name === '.DS_Store' || entry.name.endsWith('.pyc')) {
      continue;
    }
    const entryPath = path.join(inputPath, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listInputFiles(entryPath));
    } else if (entry.isFile()) {
      files.push(entryPath);
    }
  }
  return files.sort();
}

async function hashInputs(inputs, paths) {
  const files = [];
  for (const inputPath of inputs) {
    if (!existsSync(inputPath)) {
      throw new Error(`Runtime input is missing: ${path.relative(paths.repoRoot, inputPath)}`);
    }
    files.push(...await listInputFiles(inputPath));
  }
  const hashed = [];
  for (const file of files.sort()) {
    hashed.push({
      path: path.relative(paths.repoRoot, file),
      sha256: await hashFile(file),
    });
  }
  return createHash('sha256').update(stableJson(hashed)).digest('hex');
}

async function readJsonIfExists(filePath) {
  if (!existsSync(filePath)) return null;
  return JSON.parse(await readFile(filePath, 'utf8'));
}

function requiredFilesExist(baseDir, required) {
  for (const relative of required) {
    if (!existsSync(path.join(baseDir, relative))) return false;
  }
  return true;
}

function componentRuntimeDir(target, componentName) {
  return path.join(runtimeDir(target), componentName);
}

function buildPaths(target) {
  return {
    desktopDir,
    frontendDir,
    repoRoot,
    packageJsonPath,
    buildRoot,
    distRoot,
    targetDir: targetDir(target),
    runtimeDir: runtimeDir(target),
  };
}

function installNodeDependencies() {
  for (const directory of [frontendDir, desktopDir]) {
    if (!existsSync(path.join(directory, 'package-lock.json'))) {
      throw new Error(`Missing package-lock.json in ${path.relative(repoRoot, directory)}; desktop builds require deterministic npm ci installs.`);
    }
    run('npm', ['ci'], { cwd: directory });
  }
}

async function readLock(target) {
  const lock = JSON.parse(await readFile(lockPath, 'utf8'));
  const config = lock?.platforms?.[target];
  if (lock?.version !== 1 || !config) {
    throw new Error(`No runtime lock entry for target '${target}' in ${path.relative(repoRoot, lockPath)}`);
  }
  return { lock, config };
}

async function loadPlatform(target) {
  const platformPath = path.join(scriptDir, 'platforms', `${target}.mjs`);
  if (!existsSync(platformPath)) {
    throw new Error(`Unsupported desktop target '${target}'. Add scripts/platforms/${target}.mjs first.`);
  }
  const platform = await import(pathToFileURL(platformPath).href);
  return { ...platform, platformPath };
}

function resolveBuildTools(config, platform) {
  const tools = platform.resolveBuildTools({ config });
  if (platform.verifyBuildTools) {
    platform.verifyBuildTools({ config, tools });
  }
  return tools;
}

async function runtimeStampPayload(target, platform, component, paths) {
  return {
    version: component.version,
    target,
    component: component.name,
    config: component.config,
    required: component.required,
    inputsHash: await hashInputs([platform.platformPath, ...(component.inputs || [])], paths),
  };
}

async function runtimeIsCurrent(target, config, platform, component) {
  const expected = await runtimeStampPayload(target, platform, component, buildPaths(target));
  const stamp = await readJsonIfExists(runtimeStampPath(target, component.name));
  if (
    stamp &&
      stamp.hash === hashObject(expected) &&
      requiredFilesExist(componentRuntimeDir(target, component.name), component.required)
  ) {
    return true;
  }

  return false;
}

async function writeRuntimeStamp(target, platform, component) {
  const payload = await runtimeStampPayload(target, platform, component, buildPaths(target));
  await mkdir(stampDir(target), { recursive: true });
  await writeFile(runtimeStampPath(target, component.name), `${JSON.stringify({ ...payload, hash: hashObject(payload) }, null, 2)}\n`);
}

async function buildRuntime(target, config, platform, force = false) {
  const paths = buildPaths(target);
  const tools = resolveBuildTools(config, platform);
  const components = platform.runtimeComponents({ config, paths }).map((component) => ({
    version: platform.runtimeBuildVersion,
    ...component,
  }));
  const componentNames = new Set(components.map((component) => component.name));
  if (force) {
    await rm(paths.runtimeDir, { recursive: true, force: true });
  }
  await mkdir(paths.runtimeDir, { recursive: true });
  for (const entry of await readdir(paths.runtimeDir, { withFileTypes: true })) {
    if (entry.isDirectory() && !componentNames.has(entry.name)) {
      await rm(path.join(paths.runtimeDir, entry.name), { recursive: true, force: true });
    }
  }
  for (const component of components) {
    if (!force && (await runtimeIsCurrent(target, config, platform, component))) {
      console.log(`Runtime component ${component.name} is current for ${target}; skipping rebuild.`);
      continue;
    }
    await component.build({ target, config, paths, tools });
    await writeRuntimeStamp(target, platform, component);
  }
}

async function writeElectronBuilderConfig(target, platform) {
  const paths = buildPaths(target);
  const configPath = path.join(paths.targetDir, 'electron-builder.json');
  await mkdir(paths.targetDir, { recursive: true });
  const packageJson = JSON.parse(await readFile(packageJsonPath, 'utf8'));
  const config = platform.electronBuilderConfig({ target, paths, baseConfig: packageJson.build || {} });
  await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`);
  return configPath;
}

function buildFrontend() {
  run('npm', ['run', 'build'], { cwd: frontendDir });
}

async function verifyRuntime(target, platform) {
  if (platform.verifyRuntime) {
    await platform.verifyRuntime({ target, paths: buildPaths(target) });
  }
}

async function buildDesktop(args) {
  const platform = await loadPlatform(args.target);
  const { config } = await readLock(args.target);
  installNodeDependencies();
  await buildRuntime(args.target, config, platform, args.forceRuntime);
  await verifyRuntime(args.target, platform);
  buildFrontend();
  run('npx', ['tsc', '-p', 'tsconfig.json']);
  if (platform.preparePackageAssets) {
    await platform.preparePackageAssets({ target: args.target, paths: buildPaths(args.target) });
  }
  const builderConfig = await writeElectronBuilderConfig(args.target, platform);
  run('npx', ['electron-builder', ...platform.electronBuilderArgs(), '--config', builderConfig]);
}

async function cleanDesktop() {
  await rm(buildRoot, { recursive: true, force: true });
  await rm(distRoot, { recursive: true, force: true });
}

async function verifyDesktop(args) {
  const platform = await loadPlatform(args.target);
  run('npx', ['tsc', '-p', 'tsconfig.json', '--noEmit']);
  await verifyRuntime(args.target, platform);
}

function usage() {
  console.error('Usage: npm run desktop:build -- [--target macos-arm64] [--force-runtime]');
  console.error('       npm run desktop:verify -- [--target macos-arm64]');
  console.error('       npm run desktop:clean');
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.command === 'build') {
    await buildDesktop(args);
    return;
  }
  if (args.command === 'clean') {
    await cleanDesktop();
    return;
  }
  if (args.command === 'verify') {
    await verifyDesktop(args);
    return;
  }
  usage();
  process.exit(1);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
