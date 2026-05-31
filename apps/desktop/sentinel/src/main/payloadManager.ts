import { createHash } from 'node:crypto';
import { createReadStream, existsSync } from 'node:fs';
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';
import type { PayloadInfo, PayloadUpdate, ReleaseChannel } from '../shared/ipc.js';
import { execFileText } from './shell.js';
import {
  hostStateRoot,
  payloadManifestPath,
  payloadRoot,
  payloadStagingRoot,
} from './paths.js';

// Mirrors the manifest written by scripts/payload-build.mjs.
interface PayloadManifest {
  schema: number;
  version: string;
  channel: ReleaseChannel;
  commit: string;
  python: string;
  builtAt: string;
  alembicHeads: { manager: string[]; instance: string[] };
}

// Mirrors latest-<channel>.json emitted by the payload build and published to
// the release server alongside the tarball. `file` is the tarball's basename
// (used by the flat local layout); `url` is an absolute download URL filled in
// by the CI publish step so the pointer index can live in a different release
// than the tarball it points at.
interface ReleaseIndex {
  schema: number;
  channel: ReleaseChannel;
  version: string;
  commit: string;
  file: string;
  url?: string;
  sha256: string;
  alembicHeads: { manager: string[]; instance: string[] };
}

// Public repo, so release assets download without auth. The pointer release for
// a channel is tagged `latest-<channel>` and holds only latest-<channel>.json,
// which in turn carries the absolute URL of the newest payload tarball.
const GITHUB_REPO = 'arais-labs/sentinel';
const GITHUB_DOWNLOAD_BASE = `https://github.com/${GITHUB_REPO}/releases/download`;

function payloadOldRoot(): string {
  return path.join(hostStateRoot(), 'payload.old');
}

async function readJson<T>(filePath: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(filePath, 'utf8')) as T;
  } catch {
    return null;
  }
}

export async function readManifest(): Promise<PayloadManifest | null> {
  return readJson<PayloadManifest>(payloadManifestPath());
}

export function isInstalled(): boolean {
  return existsSync(payloadManifestPath());
}

export async function readPayloadInfo(): Promise<PayloadInfo> {
  const manifest = await readManifest();
  if (!manifest) {
    return { installed: false, version: null, channel: null, commit: null, builtAt: null };
  }
  return {
    installed: true,
    version: manifest.version ?? null,
    channel: manifest.channel ?? null,
    commit: manifest.commit ?? null,
    builtAt: manifest.builtAt ?? null,
  };
}

async function sha256File(filePath: string): Promise<string> {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

export async function verifySha256(filePath: string, expected: string): Promise<void> {
  const actual = await sha256File(filePath);
  if (actual !== expected) {
    throw new Error(`Payload checksum mismatch. Expected ${expected}, got ${actual}.`);
  }
}

// Extracts a payload tarball into staging, validates it, then atomically swaps
// it over the live payload. The previous payload is kept until the swap
// succeeds so a failed extract never leaves a half-written app.
export async function installFromTarball(tarPath: string): Promise<void> {
  if (!existsSync(tarPath)) {
    throw new Error(`Payload archive not found at ${tarPath}.`);
  }
  const staging = payloadStagingRoot();
  await mkdir(hostStateRoot(), { recursive: true });
  await rm(staging, { recursive: true, force: true });
  await rm(payloadOldRoot(), { recursive: true, force: true });
  await mkdir(staging, { recursive: true });

  // The build packs with `tar -C <staging> .`, so contents sit at the archive
  // root (manifest.json, site-packages/, backend/, frontend/dist/).
  await execFileText('/usr/bin/tar', ['-xzf', tarPath, '-C', staging]);

  const manifest = await readJson<PayloadManifest>(path.join(staging, 'manifest.json'));
  if (!manifest || !manifest.version) {
    await rm(staging, { recursive: true, force: true });
    throw new Error('Payload archive is missing a valid manifest.json.');
  }
  for (const required of ['site-packages', 'backend', 'frontend/dist']) {
    if (!existsSync(path.join(staging, required))) {
      await rm(staging, { recursive: true, force: true });
      throw new Error(`Payload archive is missing ${required}/.`);
    }
  }

  // Atomic-ish swap: move the live payload aside, promote staging, then drop
  // the old copy. rename() is atomic within the same filesystem (userData).
  const live = payloadRoot();
  if (existsSync(live)) {
    await rename(live, payloadOldRoot());
  }
  try {
    await rename(staging, live);
  } catch (error) {
    // Restore the previous payload if promotion failed.
    if (existsSync(payloadOldRoot()) && !existsSync(live)) {
      await rename(payloadOldRoot(), live);
    }
    throw error;
  }
  await rm(payloadOldRoot(), { recursive: true, force: true });
}

// When set, points the updater at a flat directory (e.g. the Tart build VM's
// http.server) serving latest-<channel>.json + the tarball side by side. When
// unset, the updater defaults to GitHub releases.
function localBaseUrl(): string | null {
  const base = process.env.SENTINEL_UPDATE_BASE_URL;
  return base ? base.replace(/\/+$/, '') : null;
}

async function fetchReleaseIndex(channel: ReleaseChannel): Promise<ReleaseIndex | null> {
  const local = localBaseUrl();
  const url = local
    ? `${local}/latest-${channel}.json`
    : `${GITHUB_DOWNLOAD_BASE}/latest-${channel}/latest-${channel}.json`;
  const response = await fetch(url, { redirect: 'follow' });
  // No pointer release published yet for this channel: nothing to offer.
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Release index fetch failed (${response.status}) for channel ${channel}.`);
  }
  return (await response.json()) as ReleaseIndex;
}

// Resolves the absolute tarball URL for an index. GitHub indexes carry an
// absolute `url`; the flat local layout serves the tarball next to the index.
function resolveTarballUrl(index: ReleaseIndex): string {
  if (index.url) return index.url;
  const local = localBaseUrl();
  if (local) return `${local}/${index.file}`;
  throw new Error('Release index has no download URL.');
}

// Compares the installed payload against the channel's release index. Returns
// null when no release is published or the channel is already current.
export async function checkForUpdate(channel: ReleaseChannel): Promise<PayloadUpdate | null> {
  const index = await fetchReleaseIndex(channel);
  if (!index) return null;
  const installed = await readManifest();
  if (installed && installed.commit === index.commit) return null;

  const installedHeads = installed
    ? JSON.stringify(installed.alembicHeads)
    : null;
  const targetHeads = JSON.stringify(index.alembicHeads);
  return {
    channel,
    version: index.version,
    commit: index.commit,
    url: resolveTarballUrl(index),
    sha256: index.sha256,
    hasNewMigrations: installedHeads !== targetHeads,
  };
}

export async function downloadTarball(url: string, destPath: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok || !response.body) {
    throw new Error(`Payload download failed (${response.status}) from ${url}.`);
  }
  await mkdir(path.dirname(destPath), { recursive: true });
  await writeFile(destPath, Buffer.from(await response.arrayBuffer()));
}

export function downloadScratchPath(): string {
  return path.join(hostStateRoot(), 'payload.download.tar.gz');
}
