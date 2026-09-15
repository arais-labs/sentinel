import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildWorkspaceRuntime } from '../packaging/platforms/macos-arm64.mjs';

if (process.platform !== 'darwin' || process.arch !== 'arm64') {
  throw new Error('Workspace development requires an Apple silicon Mac with macOS 26 or newer.');
}
const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const targetDir = path.join(desktopDir, 'build/macos-arm64');
const lock = JSON.parse(await readFile(path.join(desktopDir, 'runtime.lock.json'), 'utf8'));
await buildWorkspaceRuntime({
  config: lock.platforms['macos-arm64'], configuration: 'debug',
  paths: { desktopDir, targetDir, runtimeDir: path.join(targetDir, 'runtime') },
});
