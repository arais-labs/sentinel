import RFB from '@novnc/novnc';

// Existing local-only pacing policy. Remote streams retain native Fence flow
// control and ContinuousUpdates; suppressing them stalls window updates over
// SSH. These noVNC 1.7 internal hooks are covered by the real-client browser test.
type Socket = {
  rQlen(): number;
  sQpush8(value: number): void;
  sQpush16(value: number): void;
  sQpush32(value: number): void;
  flush(): void;
};
type Client = RFB & {
  _sock: Socket;
  _fbWidth: number;
  _fbHeight: number;
  _flushing: boolean;
  _FBU: { rects: number };
  _rfbConnectionState: string;
  _display: { pending(): boolean };
  _sendEncodings(): void;
  _framebufferUpdate(): boolean;
};
const messages = (RFB as typeof RFB & { messages: {
  clientEncodings(socket: Socket, encodings: number[]): void;
  fbUpdateRequest(socket: Socket, incremental: boolean, x: number, y: number, width: number, height: number): void;
} }).messages;

export function paceVncUpdates(rfb: RFB, visible: () => boolean = () => true): () => void {
  const client = rfb as Client;
  const sendEncodings = client._sendEncodings;
  const framebufferUpdate = client._framebufferUpdate;
  let requestedAhead = false;
  let timer: ReturnType<typeof setInterval> | undefined;

  client._sendEncodings = function () {
    const encodings: number[] = [];
    // Capture upstream's advertised codecs without changing shared RFB methods
    // or replacing its codec list with a stale copy.
    const capture = Object.create(this) as Client;
    capture._sock = {
      rQlen() { return 0; }, sQpush8() {}, sQpush16() {}, flush() {},
      sQpush32(value) { encodings.push(value); },
    };
    sendEncodings.call(capture);
    messages.clientEncodings(this._sock, encodings.filter(value => value !== -312 && value !== -313));
  };
  client._framebufferUpdate = function () {
    const complete = framebufferUpdate.call(this);
    if (complete) requestedAhead = false;
    return complete;
  };
  const stop = () => { clearInterval(timer); timer = undefined; };
  const start = () => {
    stop();
    timer = setInterval(() => {
      // noVNC still requests the next update on completion. At most one extra
      // request gets ahead of that response; a stalled connection cannot grow
      // an endless timer-driven request queue. Fall back to ordinary pull when
      // decoding or received data is backed up.
      if (!visible() || requestedAhead || document.hidden || client._rfbConnectionState !== 'connected'
        || client._flushing || client._FBU.rects > 0 || client._display.pending()
        || client._sock.rQlen() > 512 * 1024) return;
      messages.fbUpdateRequest(client._sock, true, 0, 0, client._fbWidth, client._fbHeight);
      requestedAhead = true;
    }, 1000 / 60);
  };
  rfb.addEventListener('connect', start);
  rfb.addEventListener('disconnect', stop);
  if (client._rfbConnectionState === 'connected') start();
  return () => {
    stop();
    rfb.removeEventListener('connect', start);
    rfb.removeEventListener('disconnect', stop);
    client._sendEncodings = sendEncodings;
    client._framebufferUpdate = framebufferUpdate;
  };
}
