import { EventEmitter } from 'node:events';
import { mkdir, realpath, stat, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { WorkspaceRuntime, type WorkspaceResources } from './workspaceRuntime.js';
import { migrateDesktopSelection } from './migrations/desktopSelection.js';
import { RuntimeCompatibilityError } from './runtimeCompatibility.js';

type State = 'preparing' | 'running' | 'stopped' | 'failed' | 'stopping' | 'recovering';
function stateNeedsRecovery(reply: { states?: Record<string, string>; errors?: Record<string, string> }, id: string) {
  return reply.states?.[id] === 'failed' && !!reply.errors?.[id];
}
const resources: WorkspaceResources = { cpus: 2, memory_gib: 2, disk_gib: 32 };
export interface WorkspaceSpec {
  notificationContext?: { instanceName: string; name: string };
  project: string;
  distribution?: string;
  resources?: WorkspaceResources;
  tools: string[];
  desktop?: string;
  browser?: string;
  state: State;
  message?: string;
  error?: string;
  recoveryBackup?: string;
  recoveryRequired?: boolean;
  screen?: boolean;  // its VM is up with a display: viewers can watch it start
  desktopVersion?: number;
  reinstall?: boolean;
  reinstallRequired?: boolean;
  reconfigure?: boolean;
  growDisk?: boolean;
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// Catalog ids: sentineld checks them against its catalog.
const catalogId = (value: unknown) => typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(value);

/** Durable workspace setup; backend restarts don't own or cancel these jobs. */
export class WorkspaceLifecycle {
  readonly events = new EventEmitter();
  private entries: Record<string, WorkspaceSpec> = {};
  private jobs = new Map<string, Promise<void>>();
  private pendingJobs = new Set<Promise<void>>();
  private versions = new Map<string, number>();
  private writes: Promise<void> = Promise.resolve();
  private closing = false;
  private readonly manifest: string;

  constructor(private readonly runtime: WorkspaceRuntime, private readonly root: string) {
    this.manifest = path.join(root, 'workspaces.json');
    runtime.events.on('progress', () => this.publishProgress());
    // Provisioning steps (the desktop's first install, …) as sentineld reports them.
    runtime.events.on('workspace-progress', (id: string, message: string) => {
      const entry = this.entries[id];
      if (!entry || entry.state !== 'preparing' || entry.message === message) return;
      entry.message = message;
      this.publishProgress();
    });
    runtime.events.on('closed', () => {
      if (this.closing) return;
      for (const [id, entry] of Object.entries(this.entries)) {
        if (entry.state === 'running') {
          this.entries[id] = { ...entry, state: 'failed', error: 'The workspace runtime stopped. Start the workspace again; files and installed tools are preserved.' };
        }
      }
      void this.save().catch(() => {});
    });
  }

  async load(): Promise<void> {
    await mkdir(this.root, { recursive: true, mode: 0o700 });
    try { this.entries = JSON.parse(await readFile(this.manifest, 'utf8')); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    const resume: string[] = [];
    for (const [id, entry] of Object.entries(this.entries)) {
      migrateDesktopSelection(entry);
      this.validate(id, entry.project, entry.tools);
      if (entry.reinstall) {
        entry.state = 'failed'; entry.reinstall = undefined;
        entry.reinstallRequired = true;
        entry.error = 'Workspace reinstall was interrupted. Check the workspace before starting or confirming another reinstall.';
      }
      else if (entry.state === 'preparing') resume.push(id);
      else if (entry.state === 'recovering') { entry.state = 'failed'; entry.recoveryRequired = true; entry.error = 'Recovery was interrupted. Choose Recover to check the workspace before starting it.'; }
      else if (entry.state === 'running' || entry.state === 'stopping') entry.state = 'stopped';
    }
    await this.save();
    // Only unfinished setup resumes. Shell commands and previously open terminals
    // are never replayed, and this work never gates backend startup.
    for (const id of resume) this.launch(id);
  }

  async overview() {
    if (this.runtime.isReady) {
      this.runtime.assertCompatible();
      const reply = await this.runtime.request('status', {}, 5000);
      for (const [id, entry] of Object.entries(this.entries)) entry.screen = Boolean(reply.screens?.includes(id));
      for (const [id, state] of Object.entries(reply.states ?? {})) {
        const entry = this.entries[id];
        if (!entry || ['preparing', 'stopping'].includes(entry.state) || (entry.state === 'recovering' && this.jobs.has(id))) continue;
        if (state === 'recovering') { entry.state = 'recovering'; entry.recoveryRequired = true; entry.error = undefined; entry.message = 'Recovery is in progress on this machine…'; }
        else if (state === 'failed') { entry.recoveryRequired = stateNeedsRecovery(reply, id); entry.state = 'failed'; entry.error = reply.errors?.[id] || 'Workspace is unresponsive. Choose Recover.'; }
        else if ((entry.recoveryRequired || entry.state === 'recovering') && (state === 'stopped' || state === 'running')) { entry.state = state; entry.recoveryRequired = false; entry.error = undefined; entry.message = undefined; }
        else if (entry.state === 'running' && state === 'stopped') entry.state = 'stopped';
      }
    }
    return this.status();
  }

  status() {
    return { resources, states: Object.fromEntries(Object.entries(this.entries).map(([id, value]) => [id, {
      state: value.state,
      resources: value.resources ?? resources,
      recovery_backup: value.recoveryBackup,
      recovery_available: value.state === 'failed' && value.recoveryRequired === true,
      screen: value.screen === true,
      message: ['preparing', 'recovering'].includes(value.state) ? (this.runtime.isReady ? value.message : this.runtime.preparationMessage || value.message) : value.state === 'stopped' && value.reconfigure ? 'New size applies on next start.' : undefined,
      error: value.error,
    }])) };
  }

  private validate(id: string, project: string, tools: string[]) {
    if (!uuid.test(id)) throw new Error('A workspace UUID is required');
    if (typeof project !== 'string' || !path.isAbsolute(project) || project === '/' || /[\0\r\n]/.test(project)) throw new Error('Choose an absolute project folder');
    if (!Array.isArray(tools) || !tools.every(catalogId)) throw new Error('Unknown workspace tools');
  }

  async prepare(id: string, project: string, tools: string[], reinstall = false, requestedResources?: WorkspaceResources, notificationContext?: { instanceName: string; name: string }, requestedDistribution?: string, requestedDesktop?: string, requestedBrowser?: string): Promise<void> {
    if (this.closing) throw new Error('Workspace services are stopping');
    this.validate(id, project, tools);
    if (this.runtime.isReady) this.runtime.assertCompatible();
    const existing = this.entries[id];
    const desktop = requestedDesktop ?? existing?.desktop ?? 'none';
    if (desktop !== 'none' && !catalogId(desktop)) throw new Error('Unknown workspace desktop');
    const desktopChanged = !!existing && desktop !== existing.desktop;
    const distribution = requestedDistribution ?? existing?.distribution;
    if (!catalogId(distribution)) throw new Error('Choose a workspace distribution');
    const browser = requestedBrowser ?? existing?.browser ?? 'chromium';
    if (!catalogId(browser)) throw new Error('Unknown workspace browser');
    const browserChanged = !!existing && browser !== existing.browser;
    if (existing && !reinstall && distribution !== existing.distribution) throw new Error('Reinstall this workspace to change its distribution');
    const allocation = requestedResources ?? existing?.resources ?? resources;
    for (const [key, min, max] of [['cpus', 1, 32], ['memory_gib', 1, 64], ['disk_gib', 8, 1024]] as const) {
      if (!Number.isInteger(allocation[key]) || allocation[key] < min || allocation[key] > max) throw new Error(`Invalid workspace ${key}: choose ${min}–${max}`);
    }
    const previousResources = existing?.resources ?? resources;
    const resized = !!existing && Object.keys(resources).some(key => allocation[key as keyof WorkspaceResources] !== previousResources[key as keyof WorkspaceResources]);
    if (existing && !reinstall && allocation.disk_gib < previousResources.disk_gib) throw new Error('Workspace disks can only be increased');
    const relocated = !!existing && existing.project !== project;
    if (relocated) {
      const resolved = await realpath(project);
      const storage = await realpath(this.root);
      if (resolved === '/' || resolved === storage || resolved.startsWith(storage + '/') || storage.startsWith(resolved + '/')) throw new Error('Project folder must not contain private runtime storage');
      if (!(await stat(resolved)).isDirectory()) throw new Error('Choose an existing project folder');
    }
    if (!reinstall && existing?.tools.some(tool => !tools.includes(tool))) throw new Error('Installed workspace tools are kept; choose additional tools');
    const changed = existing && [...existing.tools].sort().join() !== [...tools].sort().join();
    if (existing?.recoveryRequired && !reinstall) throw new Error('Recover this workspace, or choose Reinstall to erase and rebuild Linux');
    if (existing?.reinstallRequired && !reinstall) throw new Error('The previous reinstall did not finish. Confirm Reinstall before starting this workspace.');
    if (existing?.state === 'recovering') throw new Error('Wait for workspace recovery to finish');
    if (existing?.state === 'stopping') throw new Error('Wait for the workspace to stop');
    if (this.jobs.has(id)) {
      if (reinstall) throw new Error('Wait for workspace setup to finish before reinstalling Linux');
      if (changed || resized || relocated || desktopChanged || browserChanged) throw new Error('Wait for workspace setup to finish before changing settings');
      return;
    }
    if (!reinstall && !resized && !relocated && !desktopChanged && !browserChanged && existing?.state === 'running' && !changed && (desktop === 'none' || existing.desktopVersion === 1)) return;
    const deferStart = (!!requestedResources || relocated) && existing?.state === 'stopped' && !changed && !desktopChanged && !browserChanged && !reinstall;
    const entry: WorkspaceSpec = { project, tools, distribution, desktop, browser, recoveryRequired: existing?.recoveryRequired, recoveryBackup: existing?.recoveryBackup, notificationContext: notificationContext ?? existing?.notificationContext, resources: { ...allocation }, reinstall,
      // sentineld provisions a changed spec when the workspace starts.
      reconfigure: relocated || resized || desktopChanged || browserChanged || changed || existing?.reconfigure,
      growDisk: (!!existing && allocation.disk_gib > previousResources.disk_gib) || existing?.growDisk,
      state: deferStart ? 'stopped' : 'preparing', message: reinstall ? 'Rebuilding the workspace…' : resized ? 'Applying workspace size…' : 'Preparing your workspace…' };
    this.entries[id] = entry;
    // Persist before acknowledging creation so an app crash can resume setup.
    try { await this.save(); }
    catch (error) {
      if (this.entries[id] === entry) {
        if (existing) this.entries[id] = existing;
        else delete this.entries[id];
      }
      throw error;
    }
    if (!deferStart && !this.closing && this.entries[id] === entry) this.launch(id, reinstall);
  }

  private launch(id: string, reinstall = false): void {
    if (this.jobs.has(id)) return;
    const version = (this.versions.get(id) || 0) + 1;
    this.versions.set(id, version);
    const current = () => !this.closing && this.versions.get(id) === version;
    const job = (async () => {
      try {
        await this.runtime.start();
        if (!current()) return;
        this.runtime.assertCompatible();
        const spec = this.entries[id];
        if (reinstall) {
          this.entries[id].message = 'Erasing the private workspace disk…';
          this.publishProgress();
          await this.runtime.request('delete', { workspace: id });
          if (!current()) return;
          spec.recoveryRequired = false;
          spec.recoveryBackup = undefined;
        } else if (spec.reconfigure) {
          this.entries[id].message = 'Restarting with your new settings…';
          this.publishProgress();
          await this.runtime.request('stop', { workspace: id });
          if (!current()) return;
        }
        this.entries[id] = { ...spec, message: spec.growDisk ? 'Expanding workspace disk…' : 'Starting your workspace…', error: undefined };
        this.publishProgress();
        // sentineld provisions the tools and the desktop (and its GPU) as part of the boot.
        await this.runtime.request('start', { workspace: id, project: spec.project, distribution: spec.distribution, desktop: spec.desktop ?? 'none',
          tools: spec.tools, browser: spec.browser ?? 'chromium', ...(spec.resources ?? resources) }, 60 * 60_000);  // a first desktop install takes minutes on a slow connection
        if (!current()) return;
        this.entries[id] = { ...spec, reinstall: undefined, reinstallRequired: undefined, reconfigure: undefined, growDisk: undefined, desktopVersion: spec.desktop !== 'none' ? 1 : undefined,
          state: 'running', message: undefined, error: undefined };
      } catch (error) {
        if (current()) this.entries[id] = error instanceof RuntimeCompatibilityError
          // Connection compatibility is reported by overview, never persisted as
          // a workspace failure that survives updating sentineld.
          ? { ...this.entries[id], state: 'stopped', message: undefined, error: undefined }
          : { ...this.entries[id], state: 'failed', reinstallRequired: reinstall || this.entries[id].reinstallRequired, message: undefined, error: error instanceof Error ? error.message : String(error) };
      } finally {
        if (current() && this.entries[id]) this.entries[id].reinstall = undefined;
        if (this.versions.get(id) === version) this.jobs.delete(id);
        await this.save();
      }
    })();
    this.jobs.set(id, job);
    this.pendingJobs.add(job);
    void job.finally(() => this.pendingJobs.delete(job)).catch(() => {});
  }

  async stop(id: string, remove = false): Promise<void> {
    if (this.closing) throw new Error('Workspace services are stopping');
    if (!uuid.test(id)) throw new Error('A workspace UUID is required');
    if (this.runtime.isReady) this.runtime.assertCompatible();
    if (this.entries[id]?.state === 'recovering') throw new Error('Wait for workspace recovery to finish');
    if (this.entries[id]?.state === 'stopping') throw new Error('Workspace is already stopping');
    this.versions.set(id, (this.versions.get(id) || 0) + 1);
    this.jobs.delete(id);
    if (this.entries[id]) this.entries[id] = { ...this.entries[id], state: 'stopping', reinstall: undefined, error: undefined, message: undefined };
    await this.save();
    try {
      if (remove) {
        // sentineld owns the disks: starting it boots no VM and downloads nothing, and its
        // delete never touches the project folder.
        await this.runtime.start();
        this.runtime.assertCompatible();
        await this.runtime.request('delete', { workspace: id });
      } else if (this.runtime.isReady) await this.runtime.request('stop', { workspace: id });
      if (remove) { delete this.entries[id]; this.events.emit('removed', id); }
      else if (this.entries[id]) this.entries[id].state = 'stopped';
    } catch (error) {
      if (this.entries[id]) this.entries[id] = { ...this.entries[id], state: 'failed', error: String(error) };
      throw error;
    } finally { await this.save(); }
  }

  async recover(id: string): Promise<void> {
    const spec = this.entries[id];
    if (this.closing) throw new Error('Workspace services are stopping');
    if (!spec) throw new Error('Workspace is not registered');
    if (!spec.recoveryRequired) throw new Error('This workspace has not been identified as needing recovery');
    if (spec.state === 'recovering') throw new Error('Recovery is already in progress');
    await this.runtime.start();
    const capabilities = await this.runtime.request('status', {}, 5000);
    this.runtime.assertCompatible();
    if (!capabilities.capabilities?.includes('workspace-recovery-v1')) throw new Error('Update Sentinel to enable workspace recovery.');
    const version = (this.versions.get(id) || 0) + 1;
    this.versions.set(id, version);
    this.jobs.delete(id);
    this.entries[id] = { ...spec, state: 'recovering', error: undefined, message: 'Stopping the VM, backing up its disk, and checking the filesystem…' };
    await this.save();
    const job = (async () => {
      try {
        // sentineld's recovery controls power-off. Stopping through the guest first
        // would make recovery wait on the failure it fixes.
        const result = await this.runtime.request('recover', { workspace: id }, 10 * 60_000);
        if (this.versions.get(id) !== version) return;
        this.entries[id] = { ...spec, state: 'stopped', recoveryRequired: false, recoveryBackup: result.backup, error: undefined, message: undefined };
        await this.save();
        this.jobs.delete(id);
        await this.prepare(id, spec.project, spec.tools);
      } catch (error) {
        if (this.versions.get(id) === version) {
          this.entries[id] = { ...this.entries[id], state: 'failed', recoveryRequired: true, error: error instanceof Error ? error.message : String(error), message: undefined };
          await this.save();
        }
      }
    })();
    this.jobs.set(id, job);
    this.pendingJobs.add(job);
    void job.finally(() => { this.pendingJobs.delete(job); if (this.jobs.get(id) === job) this.jobs.delete(id); });
  }

  async close(): Promise<void> {
    this.closing = true;
    await this.runtime.stop();
    await Promise.allSettled(this.pendingJobs);
    await this.writes;
  }

  private publishProgress(): void {
    for (const [id, entry] of Object.entries(this.entries)) {
      this.events.emit('changed', id, { ...entry, ...this.status().states[id] });
    }
  }

  private save(): Promise<void> {
    const snapshot = JSON.stringify(this.entries);
    const task = this.writes.catch(() => {}).then(async () => {
      await writeFile(this.manifest + '.next', snapshot, { mode: 0o600 });
      await rename(this.manifest + '.next', this.manifest);
      this.publishProgress();
    });
    this.writes = task;
    return task;
  }
}
