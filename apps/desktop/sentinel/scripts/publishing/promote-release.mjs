#!/usr/bin/env node
// Prepare release assets from an already-tested beta candidate. Beta reuses
// the successful PR artifact; stable reuses the immutable beta release. The
// DMG remains byte-for-byte identical. Only payload channel/commit metadata is
// rewritten before the archive is repacked and hashed for its target index.
import { createHash } from 'node:crypto';
import { cp, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(scriptDir, '../..');
const repoRoot = path.resolve(desktopDir, '../../..');
const distRoot = path.join(desktopDir, 'release');
const repoSlug = process.env.GITHUB_REPOSITORY || 'arais-labs/sentinel';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: 'inherit',
    ...options,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed with exit ${result.status}`);
}

function output(command, args) {
  const result = spawnSync(command, args, { cwd: repoRoot, encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr || result.error?.message || result.status}`);
  }
  return result.stdout.trim();
}

async function sha256(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

export function promoteManifest(source, targetChannel, targetCommit, builtAt) {
  return { ...source, channel: targetChannel, commit: targetCommit, builtAt };
}

export function promoteIndex(source, targetChannel, targetCommit, targetFile, targetSha256) {
  const promoted = {
    ...source,
    channel: targetChannel,
    commit: targetCommit,
    file: targetFile,
    sha256: targetSha256,
  };
  delete promoted.url;
  return promoted;
}

async function main() {
  const targetChannel = process.argv[2];
  if (targetChannel !== 'beta' && targetChannel !== 'stable') {
    throw new Error('Usage: promote-release.mjs <beta|stable>');
  }
  const version = (await readFile(path.join(repoRoot, 'VERSION'), 'utf8')).trim();
  const targetCommit = output('git', ['rev-parse', 'HEAD']);
  const sourceCommit = output('git', ['rev-parse', 'HEAD^2']);
  const targetTree = output('git', ['rev-parse', 'HEAD^{tree}']);
  const sourceTree = output('git', ['rev-parse', 'HEAD^2^{tree}']);
  if (targetTree !== sourceTree) {
    throw new Error(`Refusing ${targetChannel} promotion: target is not tree-identical to its candidate parent.`);
  }

  const dmgName = `Sentinel-${version}-arm64.dmg`;
  const sourceTarName = `sentinel-payload-beta-${version}.tar.gz`;
  const targetTarName = `sentinel-payload-${targetChannel}-${version}.tar.gz`;
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'sentinel-promote-'));
  const downloaded = path.join(temporary, 'downloaded');
  const staging = path.join(temporary, 'staging');

  try {
    await mkdir(downloaded, { recursive: true });
    await mkdir(staging, { recursive: true });
    let sourceLabel;
    if (targetChannel === 'beta') {
      const runId = process.env.PROMOTION_RUN_ID;
      if (!runId) throw new Error('PROMOTION_RUN_ID is required for beta promotion.');
      for (const artifact of ['desktop-installer', 'desktop-payload']) {
        run('gh', [
          'run', 'download', runId,
          '--name', artifact,
          '--dir', downloaded,
          '--repo', repoSlug,
        ]);
      }
      sourceLabel = `PR workflow run ${runId}`;
    } else {
      const betaTag = `beta-${version}-${sourceCommit.slice(0, 7)}`;
      run('gh', [
        'release', 'download', betaTag,
        '--pattern', dmgName,
        '--pattern', sourceTarName,
        '--pattern', 'latest-beta.json',
        '--dir', downloaded,
        '--repo', repoSlug,
      ]);
      sourceLabel = betaTag;
    }

    const sourceIndex = JSON.parse(await readFile(path.join(downloaded, 'latest-beta.json'), 'utf8'));
    if (sourceIndex.channel !== 'beta' || sourceIndex.version !== version || sourceIndex.commit !== sourceCommit) {
      throw new Error(`${sourceLabel} metadata does not match the candidate being promoted.`);
    }
    const sourceTar = path.join(downloaded, sourceTarName);
    if (await sha256(sourceTar) !== sourceIndex.sha256) {
      throw new Error(`${sourceLabel} payload checksum does not match its index.`);
    }

    run('tar', ['-xzf', sourceTar, '-C', staging], { env: { COPYFILE_DISABLE: '1' } });
    const manifestPath = path.join(staging, 'manifest.json');
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
    if (manifest.channel !== 'beta' || manifest.version !== version || manifest.commit !== sourceCommit) {
      throw new Error(`${sourceLabel} payload manifest does not match its release index.`);
    }
    const promotedManifest = promoteManifest(manifest, targetChannel, targetCommit, new Date().toISOString());
    await writeFile(manifestPath, `${JSON.stringify(promotedManifest, null, 2)}\n`);

    await rm(distRoot, { recursive: true, force: true });
    await mkdir(distRoot, { recursive: true });
    const targetTar = path.join(distRoot, targetTarName);
    run('tar', ['-czf', targetTar, '--exclude=._*', '--exclude=.DS_Store', '-C', staging, '.'], {
      env: { COPYFILE_DISABLE: '1' },
    });
    await cp(path.join(downloaded, dmgName), path.join(distRoot, dmgName));

    const targetIndex = promoteIndex(
      sourceIndex, targetChannel, targetCommit, targetTarName, await sha256(targetTar),
    );
    await writeFile(
      path.join(distRoot, `latest-${targetChannel}.json`),
      `${JSON.stringify(targetIndex, null, 2)}\n`,
    );
    await writeFile(path.join(distRoot, 'TESTING.txt'),
      `Promoted from ${sourceLabel}.\nDMG reused byte-for-byte; payload code reused with ${targetChannel} metadata.\n`);
    console.log(`Promoted ${sourceLabel} assets to ${targetChannel} commit ${targetCommit.slice(0, 7)}.`);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
}
