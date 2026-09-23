import { EventEmitter } from 'node:events';
import { mkdir, realpath, stat, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { WorkspaceRuntime } from './workspaceRuntime.js';
import { validateDistribution, aptPackages, type WorkspaceDistribution } from './workspaceDistributions.js';
import { toolPackages, workspaceSetupSteps } from './workspaceTools.js';
import type { WorkspaceGraphics } from './workspaceGraphics.js';
import { validateDesktop, type WorkspaceDesktop } from './workspaceDesktop.js';
import { migrateDesktopSelection } from './migrations/desktopSelection.js';
import { RuntimeCompatibilityError } from './runtimeCompatibility.js';
import { validateBrowser, type WorkspaceBrowser } from './workspaceBrowsers.js';

type State = 'preparing' | 'running' | 'stopped' | 'failed' | 'stopping' | 'recovering';
export interface WorkspaceResources { cpus: number; memory_gib: number; disk_gib: number }
function stateNeedsRecovery(reply: { states?: Record<string, string>; errors?: Record<string, string> }, id: string) {
  return reply.states?.[id] === 'failed' && !!reply.errors?.[id];
}
const resources: WorkspaceResources = { cpus: 2, memory_gib: 2, disk_gib: 32 };
export interface WorkspaceSpec {
  notificationContext?: { instanceName: string; name: string };
  project: string;
  distribution?: WorkspaceDistribution;
  resources?: WorkspaceResources;
  tools: string[];
  desktop?: WorkspaceDesktop;
  browser?: WorkspaceBrowser;
  state: State;
  message?: string;
  error?: string;
  recoveryBackup?: string;
  recoveryRequired?: boolean;
  desktopVersion?: number;
  reinstall?: boolean;
  reinstallRequired?: boolean;
  reconfigure?: boolean;
  growDisk?: boolean;
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

  constructor(private readonly runtime: WorkspaceRuntime, private readonly root: string, private readonly graphics?: WorkspaceGraphics) {
    this.manifest = path.join(root, 'workspaces.json');
    runtime.events.on('progress', () => this.publishProgress());
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
      validateDistribution(entry.distribution ?? 'alpine');
      validateDesktop(entry.desktop ?? 'none');
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
      message: ['preparing', 'recovering'].includes(value.state) ? (this.runtime.isReady ? value.message : this.runtime.preparationMessage || value.message) : value.state === 'stopped' && value.reconfigure ? 'New size applies on next start.' : undefined,
      error: value.error,
    }])) };
  }

  private validate(id: string, project: string, tools: string[]) {
    if (!uuid.test(id)) throw new Error('A workspace UUID is required');
    if (typeof project !== 'string' || !path.isAbsolute(project) || project === '/' || /[\0\r\n]/.test(project)) throw new Error('Choose an absolute project folder');
    if (!Array.isArray(tools) || tools.some(tool => !Object.hasOwn(toolPackages, tool))) throw new Error('Unknown workspace tools');
  }

  async prepare(id: string, project: string, tools: string[], reinstall = false, requestedResources?: WorkspaceResources, notificationContext?: { instanceName: string; name: string }, requestedDistribution?: WorkspaceDistribution, requestedDesktop?: WorkspaceDesktop, requestedBrowser?: WorkspaceBrowser): Promise<void> {
    if (this.closing) throw new Error('Workspace services are stopping');
    this.validate(id, project, tools);
    if (this.runtime.isReady) this.runtime.assertCompatible();
    const existing = this.entries[id];
    const desktop = requestedDesktop ?? existing?.desktop ?? 'none';
    validateDesktop(desktop);
    const desktopChanged = !!existing && desktop !== existing.desktop;
    const distribution = requestedDistribution ?? existing?.distribution ?? 'alpine';
    validateDistribution(distribution);
    const browser = requestedBrowser ?? existing?.browser ?? 'chromium';
    validateBrowser(browser, distribution);
    const browserChanged = !!existing && browser !== existing.browser;
    if (existing && !reinstall && distribution !== (existing.distribution ?? 'alpine')) throw new Error('Reinstall this workspace to change its distribution');
    if (distribution !== 'alpine') aptPackages(tools, distribution);
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
      reconfigure: relocated || resized || desktopChanged || existing?.reconfigure,
      growDisk: (!!existing && allocation.disk_gib > previousResources.disk_gib) || existing?.growDisk,
      state: deferStart ? 'stopped' : 'preparing', message: reinstall ? 'Rebuilding the Linux environment…' : resized ? 'Applying workspace size…' : 'Preparing your workspace…' };
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
        if (spec.distribution && spec.distribution !== 'alpine') {
          const capabilities = await this.runtime.request('status');
          if (!capabilities.distributions?.includes(spec.distribution)) throw new Error('This Sentinel build does not support the selected distribution. Update Sentinel.');
          if (!current()) return;
        }
        if (reinstall) {
          this.entries[id].message = 'Erasing the private Linux disk…';
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
        await this.runtime.request('start', { workspace: id, project: spec.project, ...(spec.distribution && spec.distribution !== 'alpine' ? { distribution: spec.distribution } : {}), ...(spec.resources ?? resources), grow_disk: !reinstall && (spec.growDisk || false) }, 360_000);
        if (!current()) return;
        for (const step of workspaceSetupSteps(spec.tools, { distribution: spec.distribution, browser: spec.browser ?? 'chromium' })) {
          if (!current()) return;
          this.entries[id].message = step.message;
          this.publishProgress();
          const result = await this.runtime.request('exec', {
            workspace: id, arguments: step.arguments, timeout: step.timeout,
          }, (step.timeout + 30) * 1000);
          if (!current()) return;
          if (result.exitCode !== 0) throw new Error(result.stderr || result.stdout || `Workspace setup failed: ${step.message}`);
        }
        if (spec.desktop && spec.desktop !== 'none') {
          if (!this.graphics) throw new Error('Desktop graphics resources are missing');
          this.entries[id].message = 'Preparing Metal desktop graphics…';
          this.publishProgress();
          await this.graphics.install(id, spec.desktop, spec.distribution);
          if (!current()) return;
        }
        this.entries[id] = { ...spec, reinstall: undefined, reinstallRequired: undefined, reconfigure: undefined, growDisk: undefined, desktopVersion: spec.desktop !== 'none' ? 1 : undefined,
          state: 'running', message: undefined, error: undefined };
      } catch (error) {
        if (current()) this.entries[id] = error instanceof RuntimeCompatibilityError
          // Connection compatibility is reported by overview, never persisted as
          // a workspace failure that survives replacing the helper.
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
      if (this.runtime.isReady) await this.runtime.request(remove ? 'delete' : 'stop', { workspace: id });
      else if (remove) {
        // A dormant helper has no mounted disks. Never boot or download images
        // merely to remove a workspace, and never use its project path here.
        await rm(path.join(this.root, 'store/containers', id + '-resize'), { recursive: true, force: true });
        await rm(path.join(this.root, 'store/containers', id), { recursive: true, force: true });
        await rm(path.join(this.root, 'recovery', id), { recursive: true, force: true });
        await rm(path.join(this.root, 'recovery-required', id), { force: true });
      }
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
        // Native recovery controls power-off. A guest-dependent graphics stop
        // before this request would make recovery wait on the failure it fixes.
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
