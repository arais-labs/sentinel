import { spawn, type ChildProcessByStdio } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import type { Readable, Writable } from 'node:stream';
import type { LogEntry, ManagedServiceStatus, ServiceName, ServiceState } from '../shared/ipc.js';
import { hostStateRoot } from './paths.js';

export interface ManagedProcessOptions {
  name: ServiceName;
  command: string;
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  port?: number;
}

// Ties child lifecycle to ours: dies on parent-pipe EOF, propagates child
// exit code so child.on('exit') fires on crashes.
const SPAWN_WATCH_SCRIPT = `#!/bin/sh
set -u

# Dup stdin onto fd 3: async subshells in non-interactive shells get fd 0
# auto-redirected to /dev/null per POSIX, which would defeat the watcher.
exec 3<&0

"$@" &
CHILD_PID=$!

( cat <&3 >/dev/null 2>&1 || true
  kill -TERM "$CHILD_PID" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8; do
    kill -0 "$CHILD_PID" 2>/dev/null || exit 0
    sleep 1
  done
  kill -KILL "$CHILD_PID" 2>/dev/null || true
) &

# Close wrapper's copy so EOF reaches the watcher when the parent goes away.
exec 3<&-

forward_term() {
  kill -TERM "$CHILD_PID" 2>/dev/null || true
}
trap forward_term TERM INT HUP

STATUS=0
while kill -0 "$CHILD_PID" 2>/dev/null; do
  wait "$CHILD_PID"
  STATUS=$?
done

exit "$STATUS"
`;

function ensureWrapperScript(): string {
  const scriptPath = path.join(hostStateRoot(), 'bin/spawn-watch.sh');
  // Overwrite each launch so DMG upgrades pick up new script content.
  mkdirSync(path.dirname(scriptPath), { recursive: true });
  writeFileSync(scriptPath, SPAWN_WATCH_SCRIPT, { mode: 0o755 });
  return scriptPath;
}

// Spawn the wrapper *in its own process group* (detached: true) so that on
// teardown we can `kill -SIGKILL -pid` to terminate the whole group in one
// shot — including the wrapped child even if SIGKILL races past the wrapper's
// own trap handler.
function killGroup(pid: number, signal: NodeJS.Signals): boolean {
  try {
    process.kill(-pid, signal);
    return true;
  } catch {
    return false;
  }
}

export class ProcessSupervisor extends EventEmitter {
  private readonly services = new Map<ServiceName, ManagedServiceStatus>();
  private readonly processes = new Map<ServiceName, ChildProcessByStdio<Writable, Readable, Readable>>();
  private readonly logs: LogEntry[] = [];

  status(): ManagedServiceStatus[] {
    return Array.from(this.services.values());
  }

  allLogs(): LogEntry[] {
    return [...this.logs];
  }

  isRunning(name: ServiceName): boolean {
    return this.services.get(name)?.state === 'running';
  }

  pid(name: ServiceName): number | undefined {
    return this.services.get(name)?.pid;
  }

  async start(options: ManagedProcessOptions): Promise<void> {
    // Wait for any prior process under this name to fully exit before spawning
    // the replacement; otherwise the old SIGTERM'd process can still hold the
    // listening port and cause EADDRINUSE on the new one.
    await this.stopAndWait(options.name);
    this.setStatus({
      name: options.name,
      state: 'starting',
      port: options.port,
      message: `${options.command} ${(options.args || []).join(' ')}`.trim(),
    });

    // Every spawn goes through a tiny shell wrapper that ties the child's
    // lifecycle to ours via stdin-EOF. detached:true puts the wrapper (and
    // its descendants) into a new process group so we can group-kill on
    // teardown — guaranteeing no orphans even if the wrapper itself is
    // SIGKILL'd.
    const wrapperPath = ensureWrapperScript();
    const child = spawn('/bin/sh', [wrapperPath, options.command, ...(options.args || [])], {
      cwd: options.cwd,
      env: options.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: true,
    }) as ChildProcessByStdio<Writable, Readable, Readable>;
    // Don't write to or close the wrapper's stdin; we *want* it to stay open
    // until our process dies, so the kernel-closed pipe is what signals EOF.

    this.processes.set(options.name, child);
    this.setStatus({
      name: options.name,
      state: 'running',
      pid: child.pid,
      port: options.port,
      startedAt: new Date().toISOString(),
      message: `${options.command} ${(options.args || []).join(' ')}`.trim(),
    });

    this.attachLogs(options.name, child.stdout);
    this.attachLogs(options.name, child.stderr);

    child.once('error', (error) => {
      this.appendLog(options.name, `process error: ${error.message}`);
      this.setStatus({
        name: options.name,
        state: 'failed',
        port: options.port,
        message: error.message,
        exitedAt: new Date().toISOString(),
      });
    });

    child.once('exit', (code) => {
      this.processes.delete(options.name);
      const current = this.services.get(options.name);
      const state: ServiceState = current?.state === 'stopping' || code === 0 ? 'stopped' : 'failed';
      this.setStatus({
        name: options.name,
        state,
        port: options.port,
        message: `exited with code ${code ?? 'signal'}`,
        exitCode: code,
        exitedAt: new Date().toISOString(),
      });
    });
  }

  stop(name: ServiceName): void {
    const child = this.processes.get(name);
    if (!child) {
      const existing = this.services.get(name);
      if (existing) {
        this.setStatus({ ...existing, state: 'stopped', pid: undefined });
      }
      return;
    }
    this.setStatus({ ...this.services.get(name)!, state: 'stopping' });
    // Group-kill: wrapper + wrapped child are in the same process group
    // thanks to `detached: true`. Falls back to direct PID signal if the
    // group send fails (e.g. wrapper already gone but pid still tracked).
    if (child.pid !== undefined && !killGroup(child.pid, 'SIGTERM')) {
      child.kill('SIGTERM');
    }
    setTimeout(() => {
      if (this.processes.get(name) === child && child.pid !== undefined) {
        if (!killGroup(child.pid, 'SIGKILL')) {
          child.kill('SIGKILL');
        }
      }
    }, 5000).unref();
  }

  async stopAndWait(name: ServiceName, timeoutMs = 8000): Promise<void> {
    const child = this.processes.get(name);
    if (!child) {
      const existing = this.services.get(name);
      if (existing) {
        this.setStatus({ ...existing, state: 'stopped', pid: undefined });
      }
      return;
    }

    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      child.once('exit', finish);
      child.once('error', finish);
      this.stop(name);
      setTimeout(() => {
        if (this.processes.get(name) === child && child.pid !== undefined) {
          if (!killGroup(child.pid, 'SIGKILL')) {
            child.kill('SIGKILL');
          }
        }
      }, Math.max(1000, timeoutMs - 1000)).unref();
      setTimeout(finish, timeoutMs).unref();
    });
  }

  async stopAll(): Promise<void> {
    for (const name of Array.from(this.processes.keys()).reverse()) {
      await this.stopAndWait(name);
    }
  }

  appendManagerLog(line: string): void {
    this.appendLog('manager', line);
  }

  setVirtualStatus(status: ManagedServiceStatus): void {
    this.setStatus(status);
  }

  private attachLogs(name: ServiceName, stream: NodeJS.ReadableStream): void {
    const rl = readline.createInterface({ input: stream });
    rl.on('line', (line) => this.appendLog(name, line));
  }

  private appendLog(service: LogEntry['service'], line: string): void {
    const entry: LogEntry = { service, line, at: new Date().toISOString() };
    this.logs.push(entry);
    if (this.logs.length > 2000) {
      this.logs.splice(0, this.logs.length - 2000);
    }
    this.emit('log', entry);
  }

  private setStatus(status: ManagedServiceStatus): void {
    this.services.set(status.name, status);
    this.emit('status', this.status());
  }
}
