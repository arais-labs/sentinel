import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { buildWorkspaceRuntime } from '../packaging/platforms/macos-arm64.mjs';
import { workspaceImageSource } from '../packaging/workspace-images/stage.mjs';

export function prepareWorkspaceImages(desktopDir, runtimeDirectory, initImage, environment = process.env, run = spawnSync) {
  if (!environment.SENTINEL_WORKSPACE_IMAGES_DIR) {
    const result = run('python3', [
      path.join(desktopDir, 'scripts/packaging/workspace-images/build-vm.py'),
      path.join(desktopDir, 'build/workspace-images'), '--cache',
      '--runtime', runtimeDirectory, '--init-image', initImage,
    ], { stdio: 'inherit' });
    if (result.error || result.status !== 0) {
      throw new Error(`Preparing development workspace images failed: ${result.error?.message ?? `exit ${result.status}`}`);
    }
  }
  return workspaceImageSource(desktopDir, environment);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.platform !== 'darwin' || process.arch !== 'arm64') {
    throw new Error('Workspace development requires an Apple silicon Mac with macOS 26 or newer.');
  }
  const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const targetDir = path.join(desktopDir, 'build/macos-arm64');
  const lock = JSON.parse(await readFile(path.join(desktopDir, 'runtime.lock.json'), 'utf8'));
  await buildWorkspaceRuntime({
    config: lock.platforms['macos-arm64'], configuration: 'debug',
    prepareWorkspaceImages: runtimeDirectory => prepareWorkspaceImages(
      desktopDir, runtimeDirectory, lock.platforms['macos-arm64'].workspaceRuntime.initImage,
    ),
    paths: { desktopDir, targetDir, runtimeDir: path.join(targetDir, 'runtime') },
  });
}
