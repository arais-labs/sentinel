// The workspace runtime component a release carries next to the payload: sentineld (the daemon
// and, as `sentineld vm`, each VM) as a tarball (`make release` in packages/sentineld). The app
// downloads it when it changed (src/main/app/components.ts).
//
// Index shape (latest-<channel>.json):
//   components: {
//     sentineld: { file, sha256, url? },   url is filled in by publish-release.mjs
//     images:    {}   read by app builds from before sentineld installed every distribution
//   }
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

export const COMPONENT_NAMES = ['sentineld'];
const FILE = /^sentineld-[0-9a-f]{16}\.tar\.gz$/;

export async function sha256File(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

// Throws unless `components` is complete and well-formed (urls optional).
export function validateComponents(components) {
  if (!components || typeof components !== 'object') throw new Error('Release index has no components.');
  for (const name of COMPONENT_NAMES) {
    const entry = components[name];
    if (!entry || !FILE.test(entry.file ?? '') || !entry.file.startsWith(`${name}-`) || !/^[0-9a-f]{64}$/.test(entry.sha256 ?? '')
      || entry.file.slice(name.length + 1, name.length + 17) !== entry.sha256.slice(0, 16)) {
      throw new Error(`Release component ${name} is missing or malformed.`);
    }
  }
  return components;
}

// Reads the package release outputs from `dir`: <name>.json and its tarball per component. Returns
// { components, files }: every file to place beside the payload ({ from, name }), each tarball verified.
export async function readComponents(dir) {
  const components = {};
  const files = [];
  for (const name of COMPONENT_NAMES) {
    const metadata = path.join(dir, `${name}.json`);
    if (!existsSync(metadata)) {
      throw new Error(`Missing ${metadata}: run \`make release\` in packages/${name} (CI: the ${name}-release artifact).`);
    }
    const { file, sha256 } = JSON.parse(await readFile(metadata, 'utf8'));
    const tarball = path.join(dir, file ?? '');
    if (!file || !existsSync(tarball)) throw new Error(`Missing the ${name} release tarball ${tarball}.`);
    if (await sha256File(tarball) !== sha256) throw new Error(`${name} release tarball does not match its checksum.`);
    components[name] = { file, sha256 };
    files.push({ from: tarball, name: file });
  }
  // Older apps reject an index without images; they find none and ask for an update.
  components.images = {};
  return { components: validateComponents(components), files };
}
