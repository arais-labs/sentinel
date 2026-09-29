import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import { requireRuntimeProtocol } from './runtimeCompatibility.js';
import type { WorkspaceDesktop } from './workspaceDesktop.js';

export interface RuntimeReply {
  protocol_version?: number;
  worker_id?: string;
  workspaces?: Record<string, {
    spec: { name: string; project: string; distribution: string; desktop: WorkspaceDesktop; browser?: 'chromium' | 'firefox' | 'chrome'; tools: string[]; resources: { cpus: number; memory_gib: number; disk_gib: number } };
    revision: number; operation?: string; error?: string; recovery_backup?: string;
  }>;
  process?: string;
  data?: string;
  states?: Record<string, string>;
  distributions?: string[];
  capabilities?: string[];
  errors?: Record<string, string>;
  backup?: string;
  id?: string;
  event?: string;
  error?: string;
  message?: string;
  stdout?: string;
  stderr?: string;
  exitCode?: number;
  truncated?: boolean;
}

interface RuntimeLaunch {
  remote?: boolean;
  prepare?: () => Promise<void>;
  cancelPreparation?: () => Promise<void>;
  command: string;
  args: string[];
  onProgress: (message: string) => void;
  onFailure: (message: string) => void;
  log: (message: string) => void;
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
}

/** Owns one private helper; never discovers, upgrades or stops a host engine. */
export class WorkspaceRuntime {
  readonly events = new EventEmitter();
  private child?: ChildProcessWithoutNullStreams;
  private starting?: Promise<void>;
  private ready = false;
  private protocolVersion = 1;
  private stopping = false;
  preparationMessage?: string;
  private pending = new Map<string, {
    resolve: (reply: RuntimeReply) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>();

  constructor(private readonly launch: RuntimeLaunch) {}

  get isReady(): boolean { return this.ready; }

  assertCompatible(): void {
    requireRuntimeProtocol(this.protocolVersion, this.launch.remote);
  }

  async deployment(prepare = true) {
    if (prepare) await this.launch.prepare?.();
    return { root: this.launch.args[0], executable: this.launch.command, kernel: this.launch.args[1], initImage: this.launch.args[2] };
  }

  start(): Promise<void> {
    if (this.ready) return Promise.resolve();
    if (this.starting) return this.starting;
    this.stopping = false;
    const task = (async () => {
      if (this.launch.prepare) {
        this.preparationMessage = 'Preparing workspace kernel…';
        this.events.emit('progress');
        await this.launch.prepare();
      }
      if (this.stopping) throw new Error('Workspace services are stopping');
      await this.launchHelper();
    })();
    this.starting = task;
    void task.finally(() => { if (this.starting === task) this.starting = undefined; }).catch(() => {});
    return task;
  }

  private launchHelper(): Promise<void> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.launch.command, this.launch.args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        // The helper does not inherit registry settings, proxy credentials, or
        // the user's shell configuration. Paths and images are explicit args.
        env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin', LANG: 'en_US.UTF-8' },
      });
      this.child = child;
      let started = false;
      const timer = setTimeout(() => {
        reject(new Error('Workspace setup timed out. Retry setup to resume.'));
        child.kill('SIGKILL');
      }, this.launch.startupTimeoutMs ?? 30 * 60_000);
      const fail = (error: Error) => { clearTimeout(timer); reject(error); };
      child.once('error', fail);
      child.stdin.on('error', error => this.launch.log(`Workspace runtime input: ${error.message}`));
      child.stderr.on('data', chunk => this.launch.log(String(chunk).trim()));
      const lines = createInterface({ input: child.stdout });
      lines.on('line', line => {
        let reply: RuntimeReply;
        try { reply = JSON.parse(line) as RuntimeReply; }
        catch { this.launch.log(`Workspace runtime: ${line.slice(0, 2000)}`); return; }
        if (reply.event === 'preparing') {
          this.preparationMessage = reply.message || 'Preparing workspace images…';
          this.launch.onProgress(this.preparationMessage);
          this.events.emit('progress');
        } else if (reply.event === 'ready' && !reply.id) {
          clearTimeout(timer);
          started = true;
          this.protocolVersion = reply.protocol_version ?? 1;
          this.ready = true;
          this.preparationMessage = undefined;
          this.launch.onProgress('Workspace runtime ready');
          resolve();
        } else if (reply.event === 'fatal') {
          fail(new Error(reply.error || 'Workspace runtime failed'));
        }
        if (reply.process && !reply.id) this.events.emit(reply.process, reply);
        if (reply.id) {
          const pending = this.pending.get(reply.id);
          if (pending) {
            clearTimeout(pending.timer);
            this.pending.delete(reply.id);
            if (reply.error) pending.reject(new Error(reply.error));
            else pending.resolve(reply);
          }
        }
        if (reply.event === 'shutdown_error') this.launch.log(reply.error || 'Workspace shutdown failed');
      });
      let finished = false;
      const finish = (code: number | null, signal: NodeJS.Signals | null) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        lines.close();
        // Descendants can inherit the helper's pipes. Its exit, rather than
        // EOF from every descendant, ends ownership of this connection.
        child.stdin.destroy();
        child.stdout.destroy();
        child.stderr.destroy();
        if (this.child === child) { this.child = undefined; this.ready = false; }
        const error = new Error(`Workspace runtime exited (${signal || code})`);
        for (const pending of this.pending.values()) {
          clearTimeout(pending.timer);
          pending.reject(error);
        }
        this.pending.clear();
        this.events.emit("closed", error);
        if (!started) reject(error);
        if (started && !this.stopping) {
          this.launch.onFailure(error.message);
          // Recovery is requested by workspace Retry/Start; never replay commands.
        }
      };
      child.once('exit', finish);
      // Spawn failures emit close without exit.
      child.once('close', finish);
    });
  }

  async request(action: string, values: Record<string, unknown> = {}, timeoutMs = 10 * 60_000): Promise<RuntimeReply> {
    if (!this.ready || this.stopping) throw new Error('Workspace runtime is not ready');
    // Status remains readable for diagnostics/upgrades. Never send an operation
    // using a contract the worker cannot interpret, including catalog reads.
    if (action !== 'status') this.assertCompatible();
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Workspace ${action} timed out; the command was not retried.`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.child!.stdin.write(`${JSON.stringify({ ...values, id, action })}\n`, error => {
        if (!error) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      });
    });
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.preparationMessage = undefined;
    await this.launch.cancelPreparation?.();
    const child = this.child;
    if (!child) { await this.starting?.catch(() => {}); return; }
    await new Promise<void>(resolve => {
      const done = () => {
        clearTimeout(terminate);
        clearTimeout(force);
        child.removeListener('exit', done);
        child.removeListener('close', done);
        resolve();
      };
      const terminate = setTimeout(() => child.kill('SIGTERM'), this.launch.shutdownTimeoutMs ?? 15_000);
      const force = setTimeout(() => child.kill('SIGKILL'), (this.launch.shutdownTimeoutMs ?? 15_000) + 5000);
      child.once('exit', done);
      child.once('close', done);
      child.stdin.end();
    });
  }
}
