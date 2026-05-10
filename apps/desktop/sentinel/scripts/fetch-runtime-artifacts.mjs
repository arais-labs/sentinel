#!/usr/bin/env node
import { createWriteStream, existsSync } from 'node:fs';
import { mkdir, readFile, rm } from 'node:fs/promises';
import http from 'node:http';
import https from 'node:https';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { artifactDir, desktopDir, ensureArtifactDir, repoRoot, sha256 } from './artifact-common.mjs';

const manifestPath = process.env.SENTINEL_DESKTOP_RUNTIME_MANIFEST
  ? path.resolve(process.env.SENTINEL_DESKTOP_RUNTIME_MANIFEST)
  : path.join(desktopDir, 'resources/runtime-manifest.local.json');

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit' });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit ${result.status}`);
  }
}

async function download(url, destination) {
  if (url.startsWith('file://')) {
    return new URL(url).pathname;
  }
  await mkdir(path.dirname(destination), { recursive: true });
  const client = url.startsWith('https://') ? https : http;
  await new Promise((resolve, reject) => {
    const request = client.get(url, (response) => {
      if (response.statusCode && response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        download(response.headers.location, destination).then(resolve, reject);
        return;
      }
      if (response.statusCode !== 200) {
        reject(new Error(`Download failed ${response.statusCode}: ${url}`));
        return;
      }
      const file = createWriteStream(destination, { mode: 0o600 });
      response.pipe(file);
      file.on('finish', () => file.close(resolve));
      file.on('error', reject);
    });
    request.on('error', reject);
  });
  return destination;
}

function safeUnpackTarget(unpackTo) {
  if (!/^[a-zA-Z0-9._/-]+$/.test(unpackTo) || unpackTo.includes('..') || path.isAbsolute(unpackTo)) {
    throw new Error(`Unsafe unpack target: ${unpackTo}`);
  }
  return path.join(desktopDir, 'resources', unpackTo);
}

async function main() {
  await ensureArtifactDir();
  if (!existsSync(manifestPath)) {
    throw new Error(`Runtime manifest not found: ${manifestPath}. Run npm run artifacts:local or provide SENTINEL_DESKTOP_RUNTIME_MANIFEST.`);
  }

  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  if (!Array.isArray(manifest.artifacts)) {
    throw new Error(`Invalid runtime manifest: ${manifestPath}`);
  }

  for (const artifact of manifest.artifacts) {
    if (!artifact.id || !artifact.url || !artifact.sha256 || !artifact.unpackTo) {
      throw new Error(`Invalid artifact entry in ${manifestPath}`);
    }
    const cachePath = path.join(artifactDir, path.basename(new URL(artifact.url).pathname));
    const sourcePath = await download(artifact.url, cachePath);
    const actual = await sha256(sourcePath);
    if (actual !== artifact.sha256) {
      throw new Error(`Checksum mismatch for ${artifact.id}: expected ${artifact.sha256}, got ${actual}`);
    }
    const target = safeUnpackTarget(artifact.unpackTo);
    await rm(target, { recursive: true, force: true });
    await mkdir(target, { recursive: true });
    run('tar', ['-xzf', sourcePath, '-C', target, '--strip-components', '1']);
    console.log(`Prepared ${artifact.id} -> ${path.relative(repoRoot, target)}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
