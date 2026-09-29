// Optional playback alongside the desktop. Failure is isolated from video/input.
// Wire format is always Opus, 48 kHz stereo, exactly 10 ms per packet.
export class DesktopAudio {
  private context?: AudioContext;
  private node?: AudioWorkletNode;
  private decoder?: AudioDecoder;
  private starting?: Promise<void>;
  private visible = true;
  private closed = false;
  private failed = false;
  private timestamp = 0;
  constructor(private readonly onError: (message: string) => void = () => {}) {}

  // Call directly from a trusted pointer/key gesture: never bypass autoplay.
  enable(): Promise<void> {
    if (this.closed || this.failed) return Promise.resolve();
    if (this.starting) return this.starting;
    this.starting = this.start().catch(error => this.fail(error));
    return this.starting;
  }
  private async start() {
    if (typeof AudioDecoder === 'undefined') throw new Error('Audio decoding is unavailable');
    const context = this.context = new AudioContext({ sampleRate: 48000, latencyHint: 'interactive' });
    // Resume must happen before awaiting module loading, while activation is live.
    await Promise.all([context.resume(),
      context.audioWorklet.addModule(new URL('./desktop-audio-worklet.js', import.meta.url).href)]);
    if (this.closed || this.failed) return;
    this.node = new AudioWorkletNode(context, 'sentinel-desktop-audio', {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [2],
    });
    this.node.connect(context.destination);
    this.decoder = new AudioDecoder({ output: data => {
      try {
        if (this.closed || !this.visible || this.context?.state !== 'running') return;
        if (data.numberOfFrames !== 480 || data.numberOfChannels !== 2 || data.sampleRate !== 48000) {
          throw new Error('Unsupported desktop audio format');
        }
        const samples = new Float32Array(960);
        data.copyTo(samples.subarray(0, 480), { planeIndex: 0, format: 'f32-planar' });
        data.copyTo(samples.subarray(480), { planeIndex: 1, format: 'f32-planar' });
        this.node?.port.postMessage(samples, [samples.buffer]);
      } catch (error) { this.fail(error); }
      finally { data.close(); }
    }, error: error => this.fail(error) });
    this.decoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 2 });
  }
  receive(bytes: Uint8Array): void {
    if (this.closed || this.failed || !this.visible || this.context?.state !== 'running' || !this.decoder) return;
    try {
      if (bytes.length < 1 || bytes.length > 1275) throw new Error('Invalid desktop audio packet');
      if (this.decoder.decodeQueueSize >= 8) {
        this.decoder.reset();
        this.decoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 2 });
        this.node?.port.postMessage('reset');
      }
      this.decoder.decode(new EncodedAudioChunk({ type: 'key', timestamp: this.timestamp, data: bytes }));
      this.timestamp += 10000;
    } catch (error) { this.fail(error); }
  }
  setVisible(visible: boolean): void {
    this.visible = visible;
    if (!visible) this.node?.port.postMessage('reset');
  }
  private fail(error: unknown) {
    if (this.failed || this.closed) return;
    this.failed = true;
    this.dispose();
    this.onError(error instanceof Error ? error.message : 'Desktop audio unavailable');
  }
  private dispose() {
    if (this.decoder?.state !== 'closed') this.decoder?.close();
    this.node?.port.postMessage('close');
    this.node?.disconnect();
    this.node?.port.close();
    if (this.context && this.context.state !== 'closed') void this.context.close().catch(() => {});
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.dispose();
  }
}
