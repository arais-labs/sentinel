import { MAX_VIDEO_PACKET, VideoMessage, VideoPacketReader, desktopCursor, videoConfiguration, videoPacket,
  type DesktopCursor, type VideoPacket } from './desktop-video-protocol';
import { DesktopAudio } from './desktop-audio';

export interface VideoSocket extends EventTarget {
  binaryType: BinaryType;
  readyState: number;
  send(bytes: Uint8Array<ArrayBuffer>): void;
  close(): void;
}
export interface DesktopVideoStats { decoded: number; presented: number; dropped: number; decodeQueue: number }

// Transport owns bytes, decoder owns compressed frames; each decoded frame is painted at once on a
// desynchronized (low-latency) canvas instead of waiting for the next animation frame, which cost up
// to a display refresh per frame. Pixels never enter React state.
export class DesktopVideo {
  private decoder?: VideoDecoder;
  private config?: VideoDecoderConfig;
  private closed = false;
  private waitingForKey = true;
  private visible = true;
  private paneVisible = true;
  private connected = false;
  private readonly audio = new DesktopAudio(message => console.warn('Desktop audio:', message));
  readonly stats: DesktopVideoStats = { decoded: 0, presented: 0, dropped: 0, decodeQueue: 0 };
  // The guest's cursor, which the viewer shows as the native pointer (undefined: none received yet).
  cursor?: DesktopCursor | null;
  onCursor?: (cursor: DesktopCursor | null) => void;
  private readonly context: CanvasRenderingContext2D;
  private readonly reader = new VideoPacketReader(packet => this.receive(packet));

  constructor(private readonly canvas: HTMLCanvasElement, private readonly socket: VideoSocket,
    private readonly onReady: () => void, private readonly onError: (message: string) => void) {
    const context = canvas.getContext('2d', { alpha: false, desynchronized: true });
    if (!context) throw new Error('Desktop canvas is unavailable');
    this.context = context;
    if (typeof VideoDecoder === 'undefined') throw new Error('Video decoding is unavailable');
    socket.binaryType = 'arraybuffer';
    socket.addEventListener('message', this.message);
    socket.addEventListener('close', this.disconnected);
    socket.addEventListener('error', this.disconnected);
    socket.addEventListener('open', this.opened);
    document.addEventListener('visibilitychange', this.visibility);
  }
  private message = (event: Event) => {
    try {
      const data = (event as MessageEvent).data;
      if (!(data instanceof ArrayBuffer) || data.byteLength > MAX_VIDEO_PACKET + 16) throw new Error('Invalid desktop stream data');
      this.reader.push(new Uint8Array(data));
    } catch (error) { this.fail(error instanceof Error ? error.message : 'Desktop video failed'); }
  };
  private disconnected = () => this.fail('Desktop connection lost');
  private opened = () => this.requestKey();
  private visibility = () => this.setVisible(this.paneVisible);
  private createDecoder() {
    if (this.decoder?.state !== 'closed') this.decoder?.close();
    this.decoder = new VideoDecoder({ output: this.present, error: error => this.fail(error.message) });
    this.decoder.configure(this.config!);
    this.waitingForKey = true;
  }
  private receive(packet: VideoPacket) {
    if (this.closed) return;
    if (packet.type === VideoMessage.audio) { this.audio.receive(packet.data); return; }
    if (packet.type === VideoMessage.cursor) { this.cursor = desktopCursor(packet.data); this.onCursor?.(this.cursor); return; }
    if (packet.type === VideoMessage.config) {
      const config = videoConfiguration(packet.data);
      const previous = this.config?.description as Uint8Array | undefined;
      const next = config.description as Uint8Array;
      const changed = !previous || config.codedWidth !== this.config?.codedWidth || config.codedHeight !== this.config?.codedHeight
        || previous.length !== next.length || previous.some((byte, index) => byte !== next[index]);
      this.config = config;
      if (changed) this.createDecoder();
      return;
    }
    if (packet.type !== VideoMessage.frame || !this.decoder || !this.config) throw new Error('Unexpected desktop video packet');
    if (!this.visible || document.hidden) return;
    const key = (packet.flags & 1) !== 0;
    // Dropping encoded deltas breaks references: reset once, then wait for an IDR.
    if (this.decoder.decodeQueueSize >= 4) {
      this.stats.dropped++;
      this.createDecoder();
      this.requestKey();
    }
    if (this.waitingForKey && !key) { this.stats.dropped++; return; }
    this.waitingForKey = false;
    this.decoder!.decode(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp: packet.timestamp, data: packet.data }));
    this.stats.decodeQueue = this.decoder!.decodeQueueSize;
  }
  private present = (frame: VideoFrame) => {
    try {
      if (this.closed || !this.visible || document.hidden) return;
      this.stats.decoded++;
      if (this.canvas.width !== frame.displayWidth || this.canvas.height !== frame.displayHeight) {
        this.canvas.width = frame.displayWidth; this.canvas.height = frame.displayHeight;
      }
      this.context.drawImage(frame, 0, 0);
      this.stats.presented++;
      if (!this.connected) { this.connected = true; this.onReady(); }
    } finally {
      frame.close();
    }
  };
  private requestKey() { if (this.socket.readyState === 1) this.socket.send(videoPacket(VideoMessage.keyframe)); }
  enableAudio() { void this.audio.enable(); }
  setVisible(visible: boolean) {
    this.paneVisible = visible;
    const showing = visible && !document.hidden;
    if (this.visible === showing) return;
    this.visible = showing;
    this.audio.setVisible(showing);
    if (showing && this.config) { this.createDecoder(); this.requestKey(); }
  }
  input(device: number, events: { type: number; code: number; value: number }[]) {
    const releasing = events.every(event => event.type === 0 || (event.type === 1 && event.value === 0));
    if (this.closed || (!this.visible && !releasing) || this.socket.readyState !== 1 || events.length > 16) return;
    const body = new Uint8Array(4 + events.length * 8), view = new DataView(body.buffer);
    view.setUint16(0, device); view.setUint16(2, events.length);
    events.forEach((event, index) => {
      const offset = 4 + index * 8;
      view.setUint16(offset, event.type); view.setUint16(offset + 2, event.code); view.setInt32(offset + 4, event.value);
    });
    this.socket.send(videoPacket(VideoMessage.input, body));
  }
  private fail(message: string) { if (!this.closed) { this.close(); this.onError(message); } }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.audio.close();
    document.removeEventListener('visibilitychange', this.visibility);
    this.socket.removeEventListener('message', this.message);
    this.socket.removeEventListener('close', this.disconnected);
    this.socket.removeEventListener('error', this.disconnected);
    this.socket.removeEventListener('open', this.opened);
    if (this.decoder?.state !== 'closed') this.decoder?.close();
    this.socket.close();
  }
}
