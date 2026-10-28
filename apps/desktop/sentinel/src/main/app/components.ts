import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream, existsSync } from 'node:fs';
import { mkdir, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { RuntimeComponentFile, RuntimeComponents } from '../../shared/ipc.js';
import { execFileText } from './shell.js';

// The workspace runtime the app downloads instead of bundling: sentineld and the workspace VM
// process as release tarballs. They ride the release index next to the payload
// (scripts/packaging/components.mjs writes the same shape):
//   components: { sentineld: {file, sha256, url?}, 'sentinel-workspace': {...} }
// Each version lives in <root>/<name>/<sha256>, immutable once complete; <root>/installed.json names
// the active ones. sentineld starts from them when the app starts it, so an update never swaps
// files under running VMs. sentineld installs each distribution itself (its share's recipes).

export const COMPONENT_NAMES = ['sentineld', 'sentinel-workspace'] as const;
export type ComponentName = typeof COMPONENT_NAMES[number];

export type ComponentFile = RuntimeComponentFile;
export type Components = RuntimeComponents;

export interface ActiveComponents {
  sentineld: string;   // directory: bin/sentineld, tools/, share/
  workspace: string;   // directory: bin/sentinel-workspace, lib/, share/firmware/
}

// What a complete component directory must hold before it may become active.
const REQUIRED: Record<ComponentName, string[]> = {
  sentineld: ['bin/sentineld', 'bin/sentineld-media', 'tools/mke2fs', 'tools/resize2fs', 'tools/wimlib-imagex', 'tools/mkntfs', 'tools/hive', 'share/sentineld-agent', 'share/catalog.json'],
  'sentinel-workspace': ['bin/sentinel-workspace', 'lib/libkrun.dylib', 'lib/libvulkan_kosmickrisp.dylib', 'share/firmware/SENTINEL_EFI.fd'],
};
const FILE = /^(sentineld|sentinel-workspace)-[0-9a-f]{16}\.tar\.gz$/;
const COMPLETE = '.complete';

export function validateComponents(value: unknown): Components {
  const components = value as Components | undefined;
  if (!components || typeof components !== 'object') throw new Error('The release lists no workspace runtime.');
  for (const name of COMPONENT_NAMES) {
    const entry = components[name];
    if (!entry || !FILE.test(entry.file ?? '') || !entry.file.startsWith(`${name}-`) || !/^[0-9a-f]{64}$/.test(entry.sha256 ?? '')
      || entry.file.slice(name.length + 1, name.length + 17) !== entry.sha256.slice(0, 16)) {
      throw new Error(`The release's ${name} component is malformed.`);
    }
    if (entry.url !== undefined && !/^https:\/\/[^\s]+$/.test(entry.url)) throw new Error(`The release's ${name} URL is not HTTPS.`);
  }
  return components;
}

export class ComponentStore {
  constructor(private readonly root: string) {}

  private dir(name: ComponentName, sha256: string): string { return path.join(this.root, name, sha256); }

  async installed(): Promise<Components | null> {
    try { return validateComponents(JSON.parse(await readFile(path.join(this.root, 'installed.json'), 'utf8'))); }
    catch { return null; }
  }

  /** The active runtime, or null when none is installed (or a directory went missing). */
  async active(): Promise<ActiveComponents | null> {
    const installed = await this.installed();
    if (!installed) return null;
    const sentineld = this.dir('sentineld', installed.sentineld.sha256);
    const workspace = this.dir('sentinel-workspace', installed['sentinel-workspace'].sha256);
    if (!existsSync(path.join(sentineld, COMPLETE)) || !existsSync(path.join(workspace, COMPLETE))) return null;
    return { sentineld, workspace };
  }

  /**
   * Installs the release's components and makes them active. Only those whose sha256 isn't
   * installed yet are fetched: `fetch(name, entry, destination)` writes the tarball (a download, or
   * a copy of a file beside a payload installed from disk). Every tarball is verified against the
   * index before it's extracted, and every directory checked before it becomes active.
   */
  async install(components: Components, fetch: (name: ComponentName, entry: ComponentFile, destination: string) => Promise<void>): Promise<boolean> {
    validateComponents(components);
    let changed = false;
    for (const name of COMPONENT_NAMES) {
      const entry = components[name];
      const target = this.dir(name, entry.sha256);
      if (existsSync(path.join(target, COMPLETE))) continue;
      changed = true;
      const scratch = `${target}.tar.gz`;
      const partial = `${target}.partial`;
      await mkdir(path.dirname(target), { recursive: true });
      await rm(partial, { recursive: true, force: true });
      try {
        await fetch(name, entry, scratch);
        const actual = await sha256File(scratch);
        if (actual !== entry.sha256) throw new Error(`The ${name} download does not match the release (sha256 ${actual}).`);
        await mkdir(partial, { recursive: true });
        await execFileText('/usr/bin/tar', ['-xzf', scratch, '-C', partial]);
        for (const file of REQUIRED[name]) {
          if (!existsSync(path.join(partial, file))) throw new Error(`The ${name} component is missing ${file}.`);
        }
        await writeFile(path.join(partial, COMPLETE), `${entry.sha256}\n`);
        await rm(target, { recursive: true, force: true });
        await rename(partial, target);
      } finally {
        await rm(scratch, { force: true });
        await rm(partial, { recursive: true, force: true });
      }
    }
    const previous = await this.installed();
    if (!changed && previous?.sentineld.sha256 === components.sentineld.sha256
      && previous?.['sentinel-workspace'].sha256 === components['sentinel-workspace'].sha256) return false;
    const manifest = path.join(this.root, 'installed.json');
    const strip = ({ file, sha256 }: ComponentFile) => ({ file, sha256 });
    await writeFile(`${manifest}.next`, `${JSON.stringify({
      sentineld: strip(components.sentineld), 'sentinel-workspace': strip(components['sentinel-workspace']),
    }, null, 2)}\n`);
    await rename(`${manifest}.next`, manifest);
    return true;
  }

  /** Removes component versions other than `keep` (the ones the running sentineld uses). */
  async prune(keep: ActiveComponents): Promise<void> {
    for (const name of COMPONENT_NAMES) {
      const base = path.join(this.root, name);
      const used = name === 'sentineld' ? keep.sentineld : keep.workspace;
      let entries: string[] = [];
      try { entries = await readdir(base); } catch { continue; }
      for (const entry of entries) {
        if (path.join(base, entry) !== used) await rm(path.join(base, entry), { recursive: true, force: true });
      }
    }
    // The GPU provider the workspace VM process replaced, and the OCI archives of local builds from
    // before sentineld installed every distribution itself.
    await rm(path.join(this.root, 'sentinel-gpu'), { recursive: true, force: true });
    await rm(path.join(this.root, 'images'), { recursive: true, force: true });
  }
}

async function sha256File(filePath: string): Promise<string> {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

/** Downloads `url` to `destination`, reporting progress as a fraction. */
export async function download(url: string, destination: string, onProgress?: (fraction: number) => void): Promise<void> {
  const response = await fetch(url, { redirect: 'follow' });
  if (!response.ok || !response.body) throw new Error(`Download failed (${response.status}) from ${url}.`);
  const total = Number(response.headers.get('content-length'));
  let received = 0;
  const progress = new Transform({
    transform(chunk, _encoding, callback) {
      received += chunk.length;
      if (total > 0) onProgress?.(Math.min(1, received / total));
      callback(null, chunk);
    },
  });
  await pipeline(Readable.fromWeb(response.body as import('node:stream/web').ReadableStream), progress, createWriteStream(destination));
}
