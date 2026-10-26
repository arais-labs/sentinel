import { spawn, type ChildProcess } from 'node:child_process';
import { connect, type Socket } from 'node:net';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { requireRuntimeProtocol, RUNTIME_PROTOCOL } from './runtimeCompatibility.js';

/** What a spec may select: sentineld's `catalog` (DESIGN.md §5, §8). */
export interface RuntimeCatalog {
  distributions: Array<{ id: string; name: string; detail: string | null; features: string[]; minimum: Partial<WorkspaceResources> }>;
  desktops: Array<{ id: string; name: string; distributions: string[] }>;
  tools: Array<{ id: string; name: string; detail: string; category: string; distributions: string[]; requires?: string[] }>;
  stacks: Array<{ id: string; name: string; description: string; tools: string[] }>;
  wallpapers: Array<{ id: string; name: string }>;
}

export interface WorkspaceResources { cpus: number; memory_gib: number; disk_gib: number }

/** The spec sentineld accepts (DESIGN.md §5). */
export interface RuntimeSpec {
  name: string; project: string; distribution: string; desktop: string; tools: string[]; resources: WorkspaceResources;
}

export interface RuntimeReply extends Partial<RuntimeCatalog> {
  protocol_version?: number;
  worker_id?: string;
  workspaces?: Record<string, {
    spec: RuntimeSpec;
    revision: number; operation?: string; error?: string; recovery_backup?: string; screen?: boolean;
  }>;
  process?: string;
  data?: string;
  states?: Record<string, string>;
  screens?: string[];  // workspaces whose VM is up with a display
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
  socket?: string;
  port?: number;
  cert_sha256?: string;
  ticket?: string;
}

interface RuntimeLaunch {
  remote?: boolean;
  /** sentineld, owned by this app: --lifeline stops it (its guests shut down, then any left are powered off) when the app exits.
   * Absent for a daemon reached through a tunnel. */
  daemon?: { command: string; args: string[] };
  /** The build tree a remote Mac installs (the backend uploads it as its release). */
  release?: { sentineld: string };
  socket: string;  // sentineld.sock
  root: string;    // sentineld's state directory
  onProgress: (message: string) => void;
  onFailure: (message: string) => void;
  log: (message: string) => void;
}

interface SentineldRecord {
  spec: Record<string, unknown> & { resources?: Record<string, number>; desktop?: string };
  revision: number;
  state: 'stopped' | 'running' | 'failed';
  operation: string | null;
  error: string | null;
  /** What provisioning is doing, worded for people (sentineld DESIGN.md §4). */
  progress?: string | null;
  /** The VM is up and has a display: viewers can watch it start (sentineld DESIGN.md §5). */
  screen?: boolean;
}

type Message = Record<string, any>;

/** One NDJSON control connection to sentineld (DESIGN.md §4). */
class Connection {
  readonly events = new EventEmitter();
  private next = 1;
  private pending = new Map<number, { resolve: (reply: Message) => void; reject: (error: Error) => void }>();
  constructor(readonly socket: Socket) {
    createInterface({ input: socket }).on('line', line => {
      const message = JSON.parse(line) as Message;
      if (message.event) { this.events.emit('event', message); return; }
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message || message.error.code));
      else waiter.resolve(message);
    });
    socket.on('close', () => {
      for (const waiter of this.pending.values()) waiter.reject(new Error('Workspace runtime connection closed'));
      this.pending.clear();
      this.events.emit('closed');
    });
    socket.on('error', () => {});
  }
  static open(path: string): Promise<Connection> {
    return new Promise((resolve, reject) => {
      const socket = connect(path);
      socket.once('error', reject);
      socket.once('connect', () => {
        socket.off('error', reject);
        const connection = new Connection(socket);
        connection.call('status', { api: 1 }).then(() => resolve(connection), reject);
      });
    });
  }
  call(op: string, values: Message = {}): Promise<Message> {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.write(`${JSON.stringify({ ...values, id, op })}\n`);
    });
  }
  close(): void { this.socket.destroy(); }
}

/** A `process` stream: binary frames [u8 type][u32 BE len][bytes] after `started` (DESIGN.md §4). */
class ProcessStream {
  private buffer = Buffer.alloc(0);
  constructor(private readonly socket: Socket, private readonly emit: (message: Message) => void) {
    socket.on('data', (chunk: Buffer) => {
      this.buffer = Buffer.concat([this.buffer, chunk]);
      while (this.buffer.length >= 5) {
        const length = this.buffer.readUInt32BE(1);
        if (this.buffer.length < 5 + length) break;
        const type = this.buffer[0], payload = this.buffer.subarray(5, 5 + length);
        this.buffer = this.buffer.subarray(5 + length);
        if (type === 1) this.emit({ event: 'output', data: payload.toString('base64') });
        if (type === 3) {
          const exit = JSON.parse(payload.toString()) as { code?: number; error?: string };
          this.emit({ event: 'exit', exitCode: exit.code ?? -1, ...(exit.error ? { error: exit.error } : {}) });
        }
      }
    });
    socket.on('close', () => this.emit({ event: 'closed' }));
  }
  private frame(type: number, payload: Buffer) {
    const header = Buffer.alloc(5);
    header[0] = type;
    header.writeUInt32BE(payload.length, 1);
    this.socket.write(Buffer.concat([header, payload]));
  }
  input(data?: string) { this.frame(0, data ? Buffer.from(data, 'base64') : Buffer.alloc(0)); }
  resize(cols: number, rows: number) {
    const size = Buffer.alloc(4);
    size.writeUInt16BE(cols, 0);
    size.writeUInt16BE(rows, 2);
    this.frame(2, size);
  }
  stop() { this.socket.destroy(); }
}

/** The app's workspace runtime: sentineld, spoken through the actions the lifecycle, the backend
 * bridge and the worker client already use. */
export class WorkspaceRuntime {
  readonly events = new EventEmitter();
  private daemon?: ChildProcess;
  private control?: Connection;
  private starting?: Promise<void>;
  private stopping = false;
  private processes = new Map<string, ProcessStream>();
  private settled = new Map<string, Array<(record: SentineldRecord) => void>>();
  private catalogReply?: Promise<RuntimeCatalog>;
  preparationMessage?: string;

  constructor(private readonly launch: RuntimeLaunch) {}

  get isReady(): boolean { return !!this.control; }

  assertCompatible(): void { requireRuntimeProtocol(RUNTIME_PROTOCOL, this.launch.remote); }

  async deployment(_prepare = true) {
    return { root: this.launch.root, executable: this.launch.daemon?.command, ...this.launch.release };
  }

  start(): Promise<void> {
    if (this.control) return Promise.resolve();
    this.stopping = false;
    this.starting ??= this.connect().finally(() => { this.starting = undefined; });
    return this.starting;
  }

  private async connect(): Promise<void> {
    if (this.launch.daemon && !this.daemon) {
      const { command, args } = this.launch.daemon;
      const child = spawn(command, args, { stdio: ['pipe', 'ignore', 'pipe'], env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin', HOME: process.env.HOME, LANG: 'en_US.UTF-8' } });
      this.daemon = child;
      child.stderr!.on('data', chunk => this.launch.log(String(chunk).trim()));
      child.once('exit', (code, signal) => {
        if (this.daemon === child) this.daemon = undefined;
        this.control?.close();
        if (!this.stopping) this.launch.onFailure(`Workspace runtime exited (${signal || code})`);
      });
    }
    // sentineld listens once its state is loaded; retry briefly while it starts.
    for (let attempt = 0; ; attempt++) {
      try { this.control = await Connection.open(this.launch.socket); break; }
      catch (error) {
        if (attempt === 100 || (this.launch.daemon && !this.daemon)) throw new Error(`Workspace runtime did not start: ${String(error)}`);
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    const control = this.control;
    control.events.on('event', (message: Message) => {
      // A deleted workspace's event carries no record: whoever waited on it is answered with that.
      const record = (message.record ?? { state: 'failed', error: 'Workspace was deleted' }) as SentineldRecord;
      if (record.operation) {
        if (record.progress) this.events.emit('workspace-progress', message.id, record.progress);
        return;
      }
      const waiters = this.settled.get(message.id);
      this.settled.delete(message.id);
      for (const waiter of waiters ?? []) waiter(record);
    });
    control.events.once('closed', () => {
      if (this.control === control) this.control = undefined;
      this.catalogReply = undefined;
      for (const stream of this.processes.values()) stream.stop();
      this.events.emit('closed', new Error('Workspace runtime connection closed'));
    });
    await control.call('list', { watch: true });
    this.launch.onProgress('Workspace runtime ready');
  }

  private async call(op: string, values: Message = {}): Promise<Message> {
    if (!this.control || this.stopping) throw new Error('Workspace runtime is not ready');
    return this.control.call(op, values);
  }

  /** An op's reply without its request id. */
  private async reply(op: string, values: Message): Promise<RuntimeReply> {
    const { id: _id, ...reply } = await this.call(op, values);
    return reply as RuntimeReply;
  }

  /** sentineld's catalog, read once per connection: it changes with sentineld or a distribution op. */
  catalog(): Promise<RuntimeCatalog> {
    if (!this.catalogReply) {
      const reply = this.call('catalog').then(({ id: _id, ...catalog }) => catalog as RuntimeCatalog);
      this.catalogReply = reply;
      reply.catch(() => { if (this.catalogReply === reply) this.catalogReply = undefined; });
    }
    return this.catalogReply;
  }

  /** The spec for these settings. The user's browser is a catalog tool added to the tools when the
   * distribution offers it; the default browser (chromium) is sentineld's own hidden tool. */
  async spec(values: { name: string; project: string; distribution: string; desktop?: string; tools?: string[]; browser?: string; resources: WorkspaceResources }): Promise<RuntimeSpec> {
    const catalog = await this.catalog();
    const distribution = catalog.distributions.find(entry => entry.id === values.distribution);
    if (!distribution) throw new Error('This Sentinel build does not support the selected distribution. Update Sentinel.');
    // docker-builder, from before sentineld's catalog, is now part of docker.
    const tools = [...new Set((values.tools ?? []).map(tool => tool === 'docker-builder' ? 'docker' : tool))];
    const browser = values.browser && values.browser !== 'chromium'
      && catalog.tools.find(tool => tool.id === values.browser && tool.distributions.includes(distribution.id));
    if (browser && !tools.includes(browser.id)) tools.push(browser.id);
    return { name: values.name, project: values.project, distribution: distribution.id, desktop: values.desktop ?? 'none',
      tools, resources: values.resources };
  }

  private async records(): Promise<Record<string, SentineldRecord>> {
    return (await this.call('list')).workspaces as Record<string, SentineldRecord>;
  }

  /** Resolves when the workspace's current operation ends. */
  private settle(workspace: string): Promise<SentineldRecord> {
    return new Promise(resolve => {
      const waiters = this.settled.get(workspace) ?? [];
      waiters.push(resolve);
      this.settled.set(workspace, waiters);
    });
  }

  async request(action: string, values: Record<string, any> = {}, _timeoutMs = 0): Promise<RuntimeReply> {
    const workspace = values.workspace as string;
    switch (action) {
      case 'status': {
        const records = await this.records();
        const states: Record<string, string> = {}, errors: Record<string, string> = {}, screens: string[] = [];
        for (const [id, record] of Object.entries(records)) {
          states[id] = record.operation === 'preparing' ? 'preparing' : record.state;
          if (record.error) errors[id] = record.error;
          if (record.screen) screens.push(id);
        }
        return { protocol_version: RUNTIME_PROTOCOL, states, errors, screens, capabilities: [] };
      }
      case 'catalog': return this.catalog();
      // sentineld's guide, sources, consoles and distributions, for the agent's workspaces tool.
      case 'source': {
        // Downloading needs url alone; browsing a downloaded archive adds what to list or read.
        const fields = ['match', 'paths', 'offset', 'length', 'limit'].filter(key => values[key] !== undefined);
        return this.reply('source', { url: values.url, ...Object.fromEntries(fields.map(key => [key, values[key]])) });
      }
      // The machine's disk use by workspace and by shared item, in bytes, for the Workspaces screen.
      case 'usage': return this.reply('usage', {});
      case 'log': return this.reply('log', values.lines === undefined ? { workspace } : { workspace, lines: values.lines });
      case 'help': return this.reply('help', values.topic === undefined ? {} : { topic: values.topic });
      case 'distribution_read': return this.reply('distribution_read', values.path === undefined
        ? { distribution: values.distribution } : { distribution: values.distribution, path: values.path });
      case 'distribution': case 'distribution_delete':
        try {
          return await this.reply(action, action === 'distribution'
            ? { distribution: values.distribution, files: values.files } : { distribution: values.distribution });
        } finally { this.catalogReply = undefined; }
      case 'start': return this.startWorkspace(values);
      case 'stop': await this.call('stop', { workspace }); return {};
      case 'delete': {
        if ((await this.records())[workspace]) await this.call('delete', { workspace });
        return {};
      }
      case 'exec': {
        const reply = await this.call('exec', { workspace, argv: values.arguments, timeout: values.timeout ?? 300 });
        return { exitCode: reply.timed_out ? 124 : reply.code ?? -1, stdout: reply.stdout, stderr: reply.stderr, truncated: reply.truncated };
      }
      case 'port_forward': return { socket: (await this.call('forward', { workspace, port: values.port })).socket };
      case 'display': {
        const reply = await this.call('display', { workspace });
        return { port: reply.port, cert_sha256: reply.cert_sha256, ticket: reply.ticket };
      }
      case 'keyboard': {
        const { layout, variant, model, options } = values;
        return this.call('keyboard', { workspace, layout, variant, model, options });
      }
      case 'clipboard': {
        // "direction": a runtime request's own "action" is the operation's name.
        const { direction, text } = values;
        return this.call('clipboard', { workspace, action: direction, ...(direction === 'write' ? { text } : {}) });
      }
      case 'wallpaper': return this.call('wallpaper', { workspace, design: values.design });
      case 'computer': return this.call('computer', { workspace, actions: values.actions, viewport: values.viewport });
      case 'computer_cancel': return this.call('computer_cancel', { workspace });
      case 'process_start': return this.startProcess(values);
      case 'process_input': this.processes.get(values.process)?.input(values.data); return {};
      case 'process_resize': this.processes.get(values.process)?.resize(values.cols, values.rows); return {};
      case 'process_stop': this.processes.get(values.process)?.stop(); this.processes.delete(values.process); return {};
      default: throw new Error(`This workspace runtime does not support ${action}`);
    }
  }

  // configure (create, or apply a changed spec while stopped), then start and wait for the boot.
  private async startWorkspace(values: Record<string, any>): Promise<RuntimeReply> {
    const workspace = values.workspace as string;
    const spec = await this.spec({ name: workspace, project: values.project, distribution: values.distribution, desktop: values.desktop,
      tools: values.tools, browser: values.browser, resources: { cpus: values.cpus, memory_gib: values.memory_gib, disk_gib: values.disk_gib } });
    let record = (await this.records())[workspace];
    if (record?.state === 'running' && !record.operation) return {};
    const sorted = (tools: unknown) => JSON.stringify([...(Array.isArray(tools) ? tools : [])].sort());
    const changed = !record || record.spec.project !== spec.project || (record.spec.desktop ?? 'none') !== spec.desktop
      || record.spec.distribution !== spec.distribution || sorted(record.spec.tools) !== sorted(spec.tools)
      || JSON.stringify(record.spec.resources) !== JSON.stringify(spec.resources);
    if (changed) record = (await this.call('configure', { workspace, revision: record?.revision ?? 0, spec })).record as SentineldRecord;
    const settled = this.settle(workspace);
    await this.call('start', { workspace, revision: record.revision });
    const result = await settled;
    if (result.state !== 'running') throw new Error(result.error || 'Workspace failed to start');
    return {};
  }

  // Its own connection: `status` and `process` pipelined, two reply lines, then binary frames,
  // which may arrive in the same chunk as the second line.
  private startProcess(values: Record<string, any>): Promise<RuntimeReply> {
    const process = values.process as string;
    return new Promise((resolve, reject) => {
      const socket = connect(this.launch.socket);
      let buffer = Buffer.alloc(0), lines = 0;
      const fail = (error: Error) => { socket.destroy(); reject(error); };
      socket.once('error', fail);
      socket.once('connect', () => socket.write(`${JSON.stringify({ id: 1, op: 'status', api: 1 })}\n${JSON.stringify({
        id: 2, op: 'process', workspace: values.workspace, argv: values.arguments, tty: !!values.terminal,
        ...(values.terminal ? { cols: values.cols ?? 80, rows: values.rows ?? 24 } : {}) })}\n`));
      const onData = (chunk: Buffer) => {
        buffer = Buffer.concat([buffer, chunk]);
        for (let newline; lines < 2 && (newline = buffer.indexOf(10)) >= 0;) {
          const reply = JSON.parse(buffer.subarray(0, newline).toString()) as Message;
          buffer = buffer.subarray(newline + 1);
          lines++;
          if (reply.error) return fail(new Error(reply.error.message || reply.error.code));
        }
        if (lines < 2) return;
        socket.off('data', onData);
        socket.off('error', fail);
        socket.on('error', () => {});
        const stream = new ProcessStream(socket, message => {
          if (message.event === 'closed') { this.processes.delete(process); return; }
          this.events.emit(process, { process, ...message });
        });
        this.processes.set(process, stream);
        if (buffer.length) socket.emit('data', buffer);
        resolve({});
      };
      socket.on('data', onData);
    });
  }

  async stop(): Promise<void> {
    this.stopping = true;
    for (const stream of this.processes.values()) stream.stop();
    this.processes.clear();
    this.control?.close();
    this.control = undefined;
    const daemon = this.daemon;
    if (!daemon) return;
    // Lifeline: stdin EOF shuts the guests down (up to 15 s, then powers off the rest), then sentineld exits.
    await new Promise<void>(resolve => {
      const force = setTimeout(() => daemon.kill('SIGKILL'), 30_000);
      daemon.once('exit', () => { clearTimeout(force); resolve(); });
      daemon.stdin!.end();
    });
  }
}
