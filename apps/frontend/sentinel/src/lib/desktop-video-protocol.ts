// Same framed byte stream over a local socket or authenticated remote tunnel.
// Header: type:u8, flags:u8, reserved:u16, length:u32be, timestamp:u64be (us).
export const VIDEO_HEADER_SIZE = 16;
export const MAX_VIDEO_PACKET = 4 * 1024 * 1024;
export const VideoMessage = { config: 1, frame: 2, input: 3, keyframe: 4, audio: 5 } as const;
export interface VideoPacket { type: number; flags: number; timestamp: number; data: Uint8Array }

export function videoPacket(type: number, data = new Uint8Array(), flags = 0, timestamp = 0): Uint8Array<ArrayBuffer> {
  if (data.length > MAX_VIDEO_PACKET || !Number.isSafeInteger(timestamp) || timestamp < 0) throw new Error('Invalid desktop packet');
  const bytes = new Uint8Array(VIDEO_HEADER_SIZE + data.length);
  const view = new DataView(bytes.buffer);
  view.setUint8(0, type); view.setUint8(1, flags);
  view.setUint32(4, data.length); view.setBigUint64(8, BigInt(timestamp));
  bytes.set(data, VIDEO_HEADER_SIZE);
  return bytes;
}

export class VideoPacketReader {
  private header = new Uint8Array(VIDEO_HEADER_SIZE);
  private headerUsed = 0;
  private body: Uint8Array | null = null;
  private bodyUsed = 0;
  constructor(private readonly receive: (packet: VideoPacket) => void) {}
  push(bytes: Uint8Array): void {
    let offset = 0;
    while (offset < bytes.length) {
      if (this.headerUsed < VIDEO_HEADER_SIZE) {
        const count = Math.min(VIDEO_HEADER_SIZE - this.headerUsed, bytes.length - offset);
        this.header.set(bytes.subarray(offset, offset + count), this.headerUsed);
        this.headerUsed += count; offset += count;
        if (this.headerUsed !== VIDEO_HEADER_SIZE) continue;
        const header = new DataView(this.header.buffer);
        const size = header.getUint32(4);
        if (size > MAX_VIDEO_PACKET || header.getUint16(2) !== 0 || this.header[0] < 1 || this.header[0] > 5
          || (this.header[0] === VideoMessage.audio && (size < 1 || size > 1275 || this.header[1] !== 0))) {
          throw new Error('Invalid desktop video header');
        }
        this.body = new Uint8Array(size);
      }
      const count = Math.min(this.body!.length - this.bodyUsed, bytes.length - offset);
      this.body!.set(bytes.subarray(offset, offset + count), this.bodyUsed);
      this.bodyUsed += count; offset += count;
      if (this.bodyUsed !== this.body!.length) continue;
      const header = new DataView(this.header.buffer);
      const timestamp = Number(header.getBigUint64(8));
      if (!Number.isSafeInteger(timestamp)) throw new Error('Invalid desktop timestamp');
      const packet = { type: this.header[0], flags: this.header[1], timestamp, data: this.body! };
      this.headerUsed = 0; this.bodyUsed = 0; this.body = null;
      this.receive(packet);
    }
  }
}

export function videoConfiguration(data: Uint8Array): VideoDecoderConfig {
  if (data.length < 15) throw new Error('Invalid desktop codec configuration');
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const codedWidth = view.getUint32(0), codedHeight = view.getUint32(4);
  if (!codedWidth || !codedHeight || codedWidth > 8192 || codedHeight > 8192 || data[8] !== 1) {
    throw new Error('Unsupported desktop video dimensions or codec');
  }
  const codec = 'avc1.' + [...data.subarray(9, 12)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  return { codec, codedWidth, codedHeight, description: data.slice(8), optimizeForLatency: true, hardwareAcceleration: 'prefer-hardware' };
}
