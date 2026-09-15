import type { SocketEvent } from '../../../../desktop/sentinel/src/shared/ipc';

// WebSocket-shaped local channel for the session stream, xterm, and noVNC.
export class DesktopSocket extends EventTarget implements WebSocket {
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  readonly extensions = '';
  readonly protocol = '';
  readonly bufferedAmount = 0;
  binaryType: BinaryType = 'blob';
  readyState: 0 | 1 | 2 | 3 = 0;
  onopen: WebSocket['onopen'] = null;
  onmessage: WebSocket['onmessage'] = null;
  onerror: WebSocket['onerror'] = null;
  onclose: WebSocket['onclose'] = null;
  private readonly id = crypto.randomUUID();
  private readonly api = window.sentinelDesktop!;
  private unsubscribe: () => void;
  private writes = Promise.resolve();

  constructor(readonly url: string) {
    super();
    this.unsubscribe = this.api.onSocketEvent(event => this.receive(event));
    void this.api.socketOpen(this.id, url).catch(() => {
      this.emit(new Event('error'));
      this.finish(1006, 'Connection failed');
    });
  }
  private emit(event: Event) {
    this.dispatchEvent(event);
    if (event.type === 'open') this.onopen?.call(this, event);
    if (event.type === 'error') this.onerror?.call(this, event);
    if (event.type === 'message') this.onmessage?.call(this, event as MessageEvent);
    if (event.type === 'close') this.onclose?.call(this, event as CloseEvent);
  }
  private finish(code: number, reason: string) {
    if (this.readyState === this.CLOSED) return;
    this.readyState = this.CLOSED;
    this.unsubscribe();
    this.emit(new CloseEvent('close', { code, reason, wasClean: code === 1000 }));
  }
  private receive(event: SocketEvent) {
    if (event.id !== this.id || this.readyState === this.CLOSED) return;
    if (event.type === 'open') {
      if (this.readyState === this.CLOSING) return;
      this.readyState = this.OPEN;
      this.emit(new Event('open'));
    } else if (event.type === 'message') {
      const data = typeof event.data === 'string' ? event.data : this.binaryType === 'arraybuffer'
        ? new Uint8Array(event.data).buffer : new Blob([new Uint8Array(event.data)]);
      this.emit(new MessageEvent('message', { data }));
    } else if (event.type === 'error') this.emit(new Event('error'));
    else this.finish(event.code, event.reason);
  }
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    if (this.readyState === this.CONNECTING) throw new DOMException('Socket is connecting', 'InvalidStateError');
    if (this.readyState !== this.OPEN) return;
    // WebSocket.send captures bytes immediately. noVNC reuses its send buffer
    // after each call, so retaining a view until this queue runs corrupts frames.
    const snapshot = typeof data === 'string' || data instanceof Blob ? data
      : ArrayBuffer.isView(data)
        ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength).slice()
        : new Uint8Array(data).slice();
    this.writes = this.writes.then(async () => {
      const payload = snapshot instanceof Blob
        ? new Uint8Array(await snapshot.arrayBuffer()) : snapshot;
      if (this.readyState === this.OPEN || this.readyState === this.CLOSING) this.api.socketSend(this.id, payload);
    });
  }
  close(code = 1000, reason = ''): void {
    if (this.readyState >= this.CLOSING) return;
    this.readyState = this.CLOSING;
    void this.writes.finally(() => this.api.socketClose(this.id, code, reason));
  }
}
