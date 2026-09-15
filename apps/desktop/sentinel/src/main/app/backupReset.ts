import { createHash } from 'node:crypto';
import { constants, createReadStream } from 'node:fs';
import { chmod, cp, lstat, mkdtemp, readdir, readlink, realpath, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

export interface BackupRoot { name: string; path: string }

function contains(parent: string, child: string): boolean {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export async function validateBackupFolder(folder: string, roots: BackupRoot[]): Promise<string> {
  const destination = await realpath(folder);
  for (const root of roots) {
    const source = await realpath(root.path).catch((error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return path.resolve(root.path);
      throw error;
    });
    if (contains(source, destination)) throw new Error('Choose a backup folder outside Sentinel’s data and logs folders.');
  }
  return destination;
}

async function inventory(root: string, progress: (message: string) => void): Promise<Record<string, string>> {
  const entries: Record<string, string> = Object.create(null);
  let checked = 0;
  async function visit(relative: string): Promise<void> {
    const file = path.join(root, relative);
    const stat = await lstat(file);
    if (stat.isSymbolicLink()) entries[relative] = `link:${await readlink(file)}`;
    else if (stat.isDirectory()) {
      entries[relative] = `directory:${stat.mode & 0o777}`;
      for (const name of (await readdir(file)).sort()) await visit(path.join(relative, name));
    } else if (stat.isFile()) {
      progress(`Verifying files (${checked++} checked)…`);
      const hash = createHash('sha256');
      for await (const chunk of createReadStream(file)) hash.update(chunk);
      entries[relative] = `file:${stat.mode & 0o777}:${stat.size}:${hash.digest('hex')}`;
    } else throw new Error(`Cannot safely back up a special filesystem entry: ${file}`);
  }
  await visit('');
  return entries;
}

// Call only after all processes using these roots have exited. Never follow
// symlinks into external project folders, and verify every root before deletion.
export async function backupAndReset(folder: string, roots: BackupRoot[], progress: (message: string) => void = () => {}): Promise<string> {
  const destination = await validateBackupFolder(folder, roots);
  const sources: BackupRoot[] = [];
  for (const root of roots) {
    if (!/^[a-z-]+$/.test(root.name)) throw new Error('Invalid backup root name.');
    const stat = await lstat(root.path).catch((error: NodeJS.ErrnoException) => {
      if (error.code === 'ENOENT') return undefined;
      throw error;
    });
    if (!stat) continue;
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Expected a real data directory: ${root.path}`);
    const resolved = await realpath(root.path);
    if (sources.some(source => contains(source.path, resolved))) continue;
    if (sources.some(source => contains(resolved, source.path))) throw new Error('Overlapping backup roots.');
    sources.push({ ...root, path: resolved });
  }
  if (!sources.length) throw new Error('No Sentinel data was found to back up.');
  const backup = await mkdtemp(path.join(destination, `Sentinel-backup-${new Date().toISOString().replace(/[:.]/g, '-')}-`));
  await chmod(backup, 0o700);
  const manifest: Record<string, unknown> = {};
  for (const source of sources) {
    progress(`Backing up ${source.name}…`);
    const target = path.join(backup, source.name);
    await cp(source.path, target, { recursive: true, dereference: false, verbatimSymlinks: true, preserveTimestamps: true, errorOnExist: true, force: false, mode: constants.COPYFILE_FICLONE });
    const original = await inventory(source.path, progress);
    const copied = await inventory(target, progress);
    if (JSON.stringify(original) !== JSON.stringify(copied)) throw new Error(`Backup verification failed for ${source.name}. Original data was not deleted. Backup: ${backup}`);
    manifest[source.name] = { source: source.path, entries: original };
  }
  await writeFile(path.join(backup, 'manifest.json'), JSON.stringify({ verifiedAt: new Date().toISOString(), roots: manifest }, null, 2), { mode: 0o600 });
  progress('Backup verified. Clearing Sentinel data…');
  try {
    for (const source of sources) await rm(source.path, { recursive: true });
  } catch (error) {
    throw new Error(`The backup is verified at ${backup}, but clearing data failed: ${String(error)}`);
  }
  await writeFile(path.join(backup, 'reset-complete.txt'), 'Sentinel data was backed up, verified, and removed.\n', { mode: 0o600 });
  return backup;
}
