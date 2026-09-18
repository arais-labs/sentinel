import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { workspaceSetupSteps } from '../workspaceTools.js';
import type { WorkspaceSpec } from '../workspaceLifecycle.js';

type Reference = { id: string; name: string; project: string; distribution: string; tools: string[] };

/** Only the updater calls this. Legacy registry interpretation stays here. */
export async function runtimeMigrationInputs(root: string, machine: string, references: Reference[]) {
  if (!/^[0-9a-f-]{36}$/i.test(machine)) throw new Error('Invalid machine identity');
  let entries: Record<string, WorkspaceSpec>;
  try { entries = JSON.parse(await readFile(path.join(root, 'remotes', machine, 'workspaces.json'), 'utf8')); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    if (!references.length) return { '001_worker_ownership': { workspaces: {} } };
    throw new Error('Workspace registrations are missing on this desktop. Update from the desktop that created these workspaces.');
  }
  const rows = new Map<string, Reference>();
  for (const row of references) {
    const existing = rows.get(row.id);
    if (existing && JSON.stringify(existing) !== JSON.stringify(row)) throw new Error(`Instance registrations disagree for ${row.name}`);
    rows.set(row.id, row);
    if (!entries[row.id]) throw new Error(`Lifecycle registration is missing for ${row.name}`);
  }
  const workspaces = Object.fromEntries(Object.entries(entries).map(([id, entry]) => {
    if (!/^[0-9a-f-]{36}$/i.test(id) || !entry.resources) throw new Error(`Workspace ${id} has incomplete registration data`);
    if (['preparing', 'stopping', 'recovering'].includes(entry.state)) throw new Error('Finish pending workspace operations before updating');
    const row = rows.get(id);
    const distribution = entry.distribution ?? 'alpine';
    const name = row?.name ?? entry.notificationContext?.name;
    if (!name) throw new Error(`Workspace ${id} has no recorded name`);
    if (row && (row.project !== entry.project || row.distribution !== distribution || [...row.tools].sort().join() !== [...entry.tools].sort().join())) {
      throw new Error(`Database and lifecycle registrations disagree for ${name}`);
    }
    return [id, { spec: { name, project: entry.project, distribution, tools: entry.tools, resources: entry.resources,
      steps: workspaceSetupSteps(entry.tools, { distribution }) }, revision: 1,
      reconfigure: Boolean(entry.reconfigure), grow_disk: Boolean(entry.growDisk),
      ...(entry.recoveryBackup ? { recovery_backup: entry.recoveryBackup } : {}),
    }];
  }));
  return { '001_worker_ownership': { workspaces } };
}
