import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const scriptDir = path.dirname(fileURLToPath(import.meta.url));
export const desktopDir = path.resolve(scriptDir, '..');
export const repoRoot = path.resolve(desktopDir, '../../..');
export const artifactDir = path.join(repoRoot, '.desktop-artifacts');

export async function ensureArtifactDir() {
  await mkdir(artifactDir, { recursive: true });
}

export function artifactPath(name) {
  return path.join(artifactDir, name);
}

export async function sha256(filePath) {
  const hash = createHash('sha256');
  await new Promise((resolve, reject) => {
    const stream = createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', resolve);
  });
  return hash.digest('hex');
}

export function fileUrl(filePath) {
  return new URL(`file://${path.resolve(filePath)}`).href;
}
