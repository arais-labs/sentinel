import { EventEmitter } from 'node:events';
import { createConnection, type Socket } from 'node:net';

// A byte stream with the same events used by the renderer's existing transport.
// Workspace selection and remote SSH setup stay in the authenticated backend.
export class DesktopStream extends EventEmitter {
  readyState = 0;
  private socket?: Socket;
  private readonly abort = new AbortController();
  private code = 1000;
  private reason = '';

  constructor(resolve: (signal: AbortSignal) => Promise<string>) {
    super();
    const timeout = setTimeout(() => this.fail('Desktop connection timed out'), 35000);
    timeout.unref();
    void resolve(this.abort.signal).then(path => {
      if (this.readyState !== 0) return;
      if (!path.startsWith('/') || path.includes('\0') || Buffer.byteLength(path) >= 104) {
        throw new Error('Invalid desktop socket');
      }
      const socket = this.socket = createConnection(path);
      socket.once('connect', () => {
        clearTimeout(timeout);
        if (this.readyState !== 0) return;
        this.readyState = 1;
        this.emit('open');
      });
      socket.on('data', data => this.emit('message', data, true));
      socket.on('error', () => this.fail('Desktop connection lost'));
      socket.once('close', () => this.finish());
    }).catch(() => {
      if (this.readyState === 0) this.fail('Desktop unavailable');
    }).finally(() => { if (this.readyState !== 0) clearTimeout(timeout); });
    this.once('close', () => clearTimeout(timeout));
  }

  get bufferedAmount(): number { return this.socket?.writableLength ?? 0; }

  send(data: string | Uint8Array): void {
    if (this.readyState !== 1) return;
    if (this.bufferedAmount + Buffer.byteLength(data) > 8 * 1024 * 1024) {
      this.fail('Desktop send buffer exceeded');
      return;
    }
    this.socket!.write(data);
  }

  close(code = 1000, reason = ''): void {
    if (this.readyState >= 2) return;
    const connected = this.readyState === 1;
    this.readyState = 2;
    this.code = code;
    this.reason = reason;
    this.abort.abort();
    if (connected) {
      this.socket!.end();
      this.socket!.setTimeout(1000, () => this.socket!.destroy());
    }
    else { this.socket?.destroy(); this.finish(); }
  }

  terminate(): void {
    if (this.readyState === 3) return;
    this.readyState = 2;
    this.abort.abort();
    this.socket?.destroy();
    this.finish();
  }

  private fail(reason: string): void {
    if (this.readyState >= 2) return;
    this.code = 1006;
    this.reason = reason;
    this.emit('error', new Error(reason));
    this.terminate();
  }

  private finish(): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit('close', this.code, Buffer.from(this.reason));
  }
}
