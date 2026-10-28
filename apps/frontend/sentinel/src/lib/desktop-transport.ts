import type { VideoSocket } from './desktop-video';
import { VideoMessage, VideoPacketReader, videoPacket } from './desktop-video-protocol';

export interface DesktopViewer { port: number; cert_sha256: string; ticket: string }

const HELLO = 16;

// The desktop's WebTransport session with sentineld-media, shaped like the socket DesktopVideo reads.
// Each video frame arrives on its own unidirectional stream (a key frame's stream starts with the
// decoder config and guest cursor); input and key frame requests go out on one bidirectional control
// stream, and guest cursor changes come back on it.
export class DesktopTransport extends EventTarget implements VideoSocket {
  binaryType: BinaryType = 'arraybuffer';
  readyState = 0;
  private session?: WebTransport;
  private control?: WritableStreamDefaultWriter<Uint8Array>;
  private writes = Promise.resolve();
  private dropUntilKey = false;

  constructor(viewer: () => Promise<DesktopViewer>) {
    super();
    void this.open(viewer).catch(() => this.finish());
  }

  private async open(viewer: () => Promise<DesktopViewer>) {
    const { port, cert_sha256, ticket } = await viewer();
    const hash = new Uint8Array(cert_sha256.match(/../g)!.map(byte => parseInt(byte, 16)));
    const session = new WebTransport(`https://127.0.0.1:${port}/`, {
      serverCertificateHashes: [{ algorithm: 'sha-256', value: hash }],
    });
    this.session = session;
    void session.closed.finally(() => this.finish());
    await session.ready;
    if (this.readyState > 1) { session.close(); return; }
    const stream = await session.createBidirectionalStream();
    this.control = stream.writable.getWriter();
    await this.control.write(videoPacket(HELLO, new TextEncoder().encode(ticket)));
    this.readyState = 1;
    this.dispatchEvent(new Event('open'));
    void this.readControl(stream.readable);
    // Frames are decoded in stream order; a stream reset by the server (a late frame) breaks the
    // reference chain, so later deltas are dropped until a key frame.
    let order = Promise.resolve();
    const streams = session.incomingUnidirectionalStreams.getReader();
    for (;;) {
      const { value, done } = await streams.read();
      if (done) break;
      const bytes = readAll(value);
      order = order.then(async () => {
        const data = await bytes;
        if (!data) { this.dropUntilKey = true; this.send(videoPacket(VideoMessage.keyframe)); return; }
        const key = data[0] === VideoMessage.config || (data[0] === VideoMessage.frame && (data[1] & 1) !== 0);
        if (this.dropUntilKey && !key) return;
        this.dropUntilKey = false;
        if (this.readyState === 1) this.dispatchEvent(new MessageEvent('message', { data: data.buffer }));
      });
    }
  }

  // Packets from the relay on the control stream (guest cursor changes), whole packets in order.
  private async readControl(readable: ReadableStream<Uint8Array>) {
    const reader = new VideoPacketReader(packet => {
      if (this.readyState !== 1) return;
      const data = videoPacket(packet.type, packet.data, packet.flags, packet.timestamp);
      this.dispatchEvent(new MessageEvent('message', { data: data.buffer }));
    });
    try {
      for await (const chunk of readable as unknown as AsyncIterable<Uint8Array>) reader.push(chunk);
    } catch { /* the session ended */ }
  }

  send(bytes: Uint8Array<ArrayBuffer>): void {
    if (this.readyState !== 1 || !this.control) return;
    const copy = bytes.slice();
    this.writes = this.writes.then(() => this.control!.write(copy)).catch(() => this.finish());
  }

  close(): void { this.finish(); }

  private finish() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    try { this.session?.close(); } catch { /* already closed */ }
    this.dispatchEvent(new CloseEvent('close'));
  }
}

async function readAll(stream: ReadableStream<Uint8Array>): Promise<Uint8Array<ArrayBuffer> | null> {
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    for await (const chunk of stream as unknown as AsyncIterable<Uint8Array>) { chunks.push(chunk); length += chunk.length; }
  } catch { return null; }
  const out = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.length; }
  return out;
}
