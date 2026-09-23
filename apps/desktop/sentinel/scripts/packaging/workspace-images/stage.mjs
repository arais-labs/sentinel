import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

export function workspaceImageSource(desktopDir, environment = process.env) {
  const metadata = path.join(desktopDir, 'build/workspace-images/current.json');
  let directory = environment.SENTINEL_WORKSPACE_IMAGES_DIR;
  if (!directory && existsSync(metadata)) {
    const record = JSON.parse(readFileSync(metadata, 'utf8'));
    if (record.schema !== 1 || typeof record.directory !== 'string' || !record.directory.trim()) {
      throw new Error(`Invalid native workspace image cache metadata: ${metadata}`);
    }
    directory = path.resolve(path.dirname(metadata), record.directory);
  }
  if (!directory?.trim()) {
    throw new Error('Native workspace images are missing. Build all three native-init images and set SENTINEL_WORKSPACE_IMAGES_DIR to their artifact directory, or publish build/workspace-images/current.json.');
  }
  directory = path.resolve(directory);
  const manifests = existsSync(path.join(directory, 'manifest.json'))
    ? [path.join(directory, 'manifest.json')]
    : ['alpine', 'ubuntu', 'debian'].map(name => path.join(directory, name, 'manifest.json'));
  for (const manifest of manifests) {
    if (!existsSync(manifest)) throw new Error(`Missing native workspace image artifact: ${manifest}. Rebuild all three native-init images.`);
  }
  return { directory, manifests };
}

function imageTool(desktopDir, arguments_) {
  const result = spawnSync('python3', [
    path.join(desktopDir, 'scripts/packaging/workspace-images/stage.py'),
    ...arguments_,
  ], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) {
    throw new Error(`Native workspace image verification/staging failed: ${result.error?.message ?? result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

export function stageWorkspaceImages(desktopDir, runtimeDirectory, source = workspaceImageSource(desktopDir)) {
  return imageTool(desktopDir, [source.directory, path.join(runtimeDirectory, 'workspace-images')]);
}

export function verifyWorkspaceImages(desktopDir, runtimeDirectory) {
  return imageTool(desktopDir, [path.join(runtimeDirectory, 'workspace-images'), '--verify-only']);
}
