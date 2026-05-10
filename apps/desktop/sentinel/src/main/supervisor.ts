import { spawn, type ChildProcessByStdio } from 'node:child_process';
import { EventEmitter } from 'node:events';
import readline from 'node:readline';
import type { Readable } from 'node:stream';
import type { LogEntry, ManagedServiceStatus, ServiceName, ServiceState } from '../shared/ipc.js';

export interface ManagedProcessOptions {
  name: ServiceName;
  command: string;
  args?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  port?: number;
}

export class ProcessSupervisor extends EventEmitter {
  private readonly services = new Map<ServiceName, ManagedServiceStatus>();
  private readonly processes = new Map<ServiceName, ChildProcessByStdio<null, Readable, Readable>>();
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

  start(options: ManagedProcessOptions): void {
    this.stop(options.name);
    this.setStatus({
      name: options.name,
      state: 'starting',
      port: options.port,
      message: `${options.command} ${(options.args || []).join(' ')}`.trim(),
    });

    const child = spawn(options.command, options.args || [], {
      cwd: options.cwd,
      env: options.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

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
    child.kill('SIGTERM');
    setTimeout(() => {
      if (this.processes.get(name) === child) {
        child.kill('SIGKILL');
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
        if (this.processes.get(name) === child) {
          child.kill('SIGKILL');
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
