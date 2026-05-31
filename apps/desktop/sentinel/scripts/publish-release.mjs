#!/usr/bin/env node
// Publishes a built desktop release to GitHub Releases under the "keep every
// build" model:
//   1. A versioned release tagged `<channel>-<version>-<shortsha>` holds the
//      DMG shell + the payload tarball + the release index. Every push creates
//      a new one, so nothing is ever clobbered or lost.
//   2. A fixed pointer release tagged `latest-<channel>` holds ONLY
//      `latest-<channel>.json`, clobbered on each push. The app polls this one
//      stable URL to discover the newest payload; the index inside it carries
//      the absolute URL of the tarball in the versioned release.
//
// Runs in CI after `desktop:build` + `payload:build`. Requires the `gh` CLI and
// a GH_TOKEN with `contents: write`. Invoke as:
//   node scripts/publish-release.mjs <channel>
import { existsSync } from 'node:fs';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const desktopDir = path.resolve(path.dirname(__filename), '..');
const distRoot = path.join(desktopDir, 'dist');
const repoSlug = process.env.GITHUB_REPOSITORY || 'arais-labs/sentinel';

// Anything matching these is forbidden inside a payload that's about to go
// public. payload-build already excludes them; this is the last-line guard so a
// build-script regression can never leak secrets/PII to a public release.
const FORBIDDEN_PAYLOAD_ENTRIES = [
  /(^|\/)\.env(\.|$)/,
  /(^|\/)\.git(\/|$)/,
  /(^|\/)\.npmrc$/,
  /(^|\/)uv\.lock$/,
  /(^|\/)id_(rsa|ed25519)(\.|$)/,
  /(^|\/)\.ssh(\/|$)/,
];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: 'inherit', ...options, env: { ...process.env, ...(options.env ?? {}) } });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit ${result.status}`);
  }
}

function output(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, ...options, env: { ...process.env, ...(options.env ?? {}) } });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr || result.error?.message || `exit ${result.status}`}`);
  }
  return result.stdout.trim();
}

function resolveChannel() {
  const channel = process.argv[2];
  if (channel !== 'stable' && channel !== 'beta') {
    throw new Error(`Usage: publish-release.mjs <stable|beta> (got "${channel ?? ''}")`);
  }
  return channel;
}

async function findDist(predicate, label) {
  const entries = existsSync(distRoot) ? await readdir(distRoot) : [];
  const match = entries.find(predicate);
  if (!match) {
    throw new Error(`Could not find ${label} in ${distRoot}. Did desktop:build / payload:build run?`);
  }
  return path.join(distRoot, match);
}

function assertPayloadClean(tarPath) {
  const listing = output('tar', ['-tzf', tarPath]).split('\n').filter(Boolean);
  const offenders = listing.filter((entry) => FORBIDDEN_PAYLOAD_ENTRIES.some((re) => re.test(entry)));
  if (offenders.length) {
    throw new Error(`Refusing to publish: payload tarball contains forbidden entries:\n${offenders.join('\n')}`);
  }
  console.log(`✓ payload PII/secret guard passed (${listing.length} entries scanned).`);
}

// gh exits non-zero when a release doesn't exist; treat that as "not found"
// rather than a hard failure.
function releaseExists(tag) {
  const result = spawnSync('gh', ['release', 'view', tag, '--repo', repoSlug], { encoding: 'utf8' });
  return result.status === 0;
}

async function main() {
  const channel = resolveChannel();
  const indexPath = path.join(distRoot, `latest-${channel}.json`);
  if (!existsSync(indexPath)) {
    throw new Error(`Missing ${indexPath}. Run \`SENTINEL_BUILD_CHANNEL=${channel} npm run payload:build\` first.`);
  }
  const index = JSON.parse(await readFile(indexPath, 'utf8'));
  const { version, commit } = index;
  if (!version || !commit) {
    throw new Error(`Release index ${indexPath} is missing version/commit.`);
  }
  const shortSha = commit.slice(0, 7);
  const versionedTag = `${channel}-${version}-${shortSha}`;
  const pointerTag = `latest-${channel}`;

  const dmgPath = await findDist((name) => name.endsWith('.dmg'), 'DMG');
  const tarPath = await findDist(
    (name) => name === `sentinel-payload-${channel}-${version}.tar.gz`,
    `payload tarball for ${channel} ${version}`,
  );

  assertPayloadClean(tarPath);

  // Absolute URL the asset will have once attached to the versioned release.
  const tarUrl = `https://github.com/${repoSlug}/releases/download/${versionedTag}/${path.basename(tarPath)}`;
  const enrichedIndex = { ...index, url: tarUrl };

  // Write the enriched index to a temp file so the pointer release carries the
  // absolute tarball URL (the build only knows the relative filename).
  const pointerIndexPath = path.join(os.tmpdir(), `latest-${channel}.json`);
  await writeFile(pointerIndexPath, `${JSON.stringify(enrichedIndex, null, 2)}\n`);

  console.log(`▶ Publishing ${channel} release ${versionedTag} (commit ${shortSha})`);

  // 1. Versioned release: immutable record of this exact build.
  if (releaseExists(versionedTag)) {
    console.log(`Release ${versionedTag} already exists; re-uploading assets with --clobber.`);
    run('gh', ['release', 'upload', versionedTag, dmgPath, tarPath, pointerIndexPath, '--clobber', '--repo', repoSlug]);
  } else {
    run('gh', [
      'release', 'create', versionedTag,
      dmgPath, tarPath, pointerIndexPath,
      '--repo', repoSlug,
      '--target', commit,
      '--title', `Sentinel ${version} (${channel}) ${shortSha}`,
      '--notes', `Automated ${channel} build from ${commit}.`,
      '--prerelease',
    ]);
  }

  // 2. Pointer release: one stable URL per channel. The app polls the index
  // here; humans land here for "the latest" and grab the DMG installer. Both
  // are clobbered each push so the pointer always reflects the newest build.
  // The heavy payload tarball stays off the pointer — the app fetches it via
  // the index's absolute `url`, so re-uploading it here would only add churn.
  if (!releaseExists(pointerTag)) {
    run('gh', [
      'release', 'create', pointerTag,
      dmgPath, pointerIndexPath,
      '--repo', repoSlug,
      '--target', commit,
      '--title', `Latest ${channel} build`,
      '--notes', `Latest ${channel} installer (DMG) + payload pointer. Updated automatically.`,
      '--prerelease',
    ]);
  } else {
    run('gh', ['release', 'upload', pointerTag, dmgPath, pointerIndexPath, '--clobber', '--repo', repoSlug]);
  }

  console.log(`\n✓ Published ${versionedTag}`);
  console.log(`✓ Pointer ${pointerTag} → DMG + ${tarUrl}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
