import type { MicrophonePermission } from '../../../../desktop/sentinel/src/shared/ipc';

export class MicrophoneAccessError extends Error {
  constructor(public permission?: MicrophonePermission) {
    super('Microphone access is needed to listen.');
    this.name = 'MicrophoneAccessError';
  }
}

export class MicrophoneUnavailableError extends Error {
  constructor() {
    super('The selected microphone is not connected. Choose another one in Voice settings.');
    this.name = 'MicrophoneUnavailableError';
  }
}

export interface MicrophoneDevice { deviceId: string; label: string }

/** Input devices with labels; opens the default input briefly if labels are still hidden. */
export async function listMicrophones(): Promise<MicrophoneDevice[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const inputs = async () => (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput' && device.deviceId && device.deviceId !== 'default');
  let devices = await inputs();
  if (devices.length && devices.every(device => !device.label)) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach(track => track.stop());
    devices = await inputs();
  }
  return devices.map((device, index) => ({ deviceId: device.deviceId, label: device.label || `Microphone ${index + 1}` }));
}

export function microphoneConstraints(deviceId?: string): MediaTrackConstraints {
  return { ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
}

/** A live track that only ever carries exact zeros is a disabled device, not a quiet room. */
export class DeadInputDetector {
  private zeroSeconds = 0;
  private dead = false;
  constructor(private rate: number, private threshold = 3) {}
  push(frame: Float32Array): 'dead' | 'alive' | null {
    if (frame.some(value => value !== 0)) {
      this.zeroSeconds = 0;
      if (!this.dead) return null;
      this.dead = false;
      return 'alive';
    }
    this.zeroSeconds += frame.length / this.rate;
    if (this.dead || this.zeroSeconds < this.threshold) return null;
    this.dead = true;
    return 'dead';
  }
}

export function deadInputMessage(label: string): string {
  const device = label || 'the microphone';
  const lid = /macbook|built-in|intégré/i.test(label) ? ' If the MacBook lid is closed, its built-in microphone is off: open the lid or' : ' Check the device or';
  return `No audio is coming from ${device}.${lid} choose another microphone in Voice settings.`;
}

export async function ensureMicrophonePermission(signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw new DOMException('Disconnected', 'AbortError');
  const desktop = window.sentinelDesktop;
  // Older desktop shells and web development use the browser's native prompt.
  if (!desktop?.requestMicrophonePermission) return;
  const permission = await desktop.requestMicrophonePermission();
  if (signal.aborted) throw new DOMException('Disconnected', 'AbortError');
  if (permission.status === 'denied' || permission.status === 'restricted') throw new MicrophoneAccessError(permission);
}

/** Encode microphone PCM as the 16 kHz mono WAV accepted by local Whisper. */
export function voiceWav(chunks: Float32Array[], sampleRate: number): Blob {
  const samples = new Float32Array(chunks.reduce((n, chunk) => n + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
  const ratio = sampleRate / 16000;
  const count = Math.floor(samples.length / ratio);
  const buffer = new ArrayBuffer(44 + count * 2);
  const view = new DataView(buffer);
  const ascii = (start: number, value: string) => [...value].forEach((c, i) => view.setUint8(start + i, c.charCodeAt(0)));
  ascii(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); ascii(8, 'WAVE');
  ascii(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const start = Math.floor(i * ratio), end = Math.max(start + 1, Math.floor((i + 1) * ratio));
    let value = 0;
    for (let j = start; j < end; j++) value += samples[j] ?? 0;
    value = Math.max(-1, Math.min(1, value / (end - start)));
    view.setInt16(44 + i * 2, value * (value < 0 ? 32768 : 32767), true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

/** Segment spoken turns, retaining a little audio before speech begins. */
export class VoiceSegmenter {
  private before: Float32Array[] = [];
  private chunks: Float32Array[] = [];
  private duration = 0;
  private speech = 0;
  private silence = 0;
  constructor(private rate: number, private onUtterance: (audio: Blob) => void) {}
  get recording(): boolean { return this.chunks.length > 0; }
  reset() { this.before = []; this.chunks = []; this.duration = this.speech = this.silence = 0; }
  push(frame: Float32Array): number {
    const seconds = frame.length / this.rate;
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    const speaking = rms > .014;
    if (!this.chunks.length && !speaking) {
      this.before.push(frame);
      while (this.before.length * seconds > .25) this.before.shift();
      return rms;
    }
    if (!this.chunks.length) { this.chunks = this.before; this.before = []; }
    this.chunks.push(frame); this.duration += seconds;
    if (speaking) { this.speech += seconds; this.silence = 0; } else this.silence += seconds;
    if (this.silence >= .65 || this.duration >= 25) {
      const chunks = this.chunks, speech = this.speech;
      this.reset();
      if (speech >= .2) this.onUtterance(voiceWav(chunks, this.rate));
    }
    return rms;
  }
}

/** A stricter onset gate during replies avoids interrupting on a single click.
 * Input has already passed through the microphone's echo/noise suppression.
 * Retain the onset so waiting for confirmation never clips the first word.
 */
export class VoiceInterruptionDetector {
  private frames: Float32Array[] = [];
  private duration = 0;
  private speech = 0;
  private silence = 0;
  constructor(private rate: number) {}
  reset() { this.frames = []; this.duration = this.speech = this.silence = 0; }
  push(frame: Float32Array): Float32Array[] | null {
    if (!frame.length) return null;
    const seconds = frame.length / this.rate;
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    this.frames.push(frame);
    this.duration += seconds;
    while (this.frames.length > 1 && this.duration - this.frames[0].length / this.rate >= .35) {
      this.duration -= this.frames.shift()!.length / this.rate;
    }
    if (rms > .025) { this.speech += seconds; this.silence = 0; }
    else {
      this.silence += seconds;
      if (this.silence >= .06) this.speech = 0;
    }
    if (this.speech < .2) return null;
    const onset = this.frames;
    this.reset();
    return onset;
  }
}

export interface CaptureOptions {
  deviceId?: string;
  /** Reports a device delivering only digital silence, and its recovery. */
  onInputHealth?: (state: 'dead' | 'alive', label: string) => void;
}

export async function captureVoice(signal: AbortSignal, onFrame: (frame: Float32Array, rate: number) => void, options: CaptureOptions = {}): Promise<() => void> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access is unavailable in this window.');
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: microphoneConstraints(options.deviceId) });
  } catch (error) {
    const name = error instanceof Error ? error.name : '';
    if (options.deviceId && (name === 'OverconstrainedError' || name === 'NotFoundError' || name === 'DevicesNotFoundError')) throw new MicrophoneUnavailableError();
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
      const permission = await window.sentinelDesktop?.getMicrophonePermission?.().catch(() => undefined);
      throw new MicrophoneAccessError(permission);
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') throw new Error('No microphone was found. Connect a microphone, then try again.');
    if (name === 'NotReadableError' || name === 'TrackStartError') throw new Error('The microphone could not start. Check your sound input settings or close other apps using it, then try again.');
    throw error;
  }
  let context: AudioContext | undefined;
  let node: AudioWorkletNode | undefined;
  const close = () => {
    stream.getTracks().forEach(track => track.stop());
    node?.disconnect();
    if (context && context.state !== 'closed') void context.close();
    signal.removeEventListener('abort', close);
  };
  signal.addEventListener('abort', close, { once: true });
  try {
    if (signal.aborted) throw new DOMException('Disconnected', 'AbortError');
    context = new AudioContext();
    await context.audioWorklet.addModule(new URL('./voice-capture-worklet.js', import.meta.url).href);
    if (signal.aborted) throw new DOMException('Disconnected', 'AbortError');
    node = new AudioWorkletNode(context, 'sentinel-voice-capture');
    const rate = context.sampleRate;
    const label = stream.getAudioTracks()[0]?.label ?? '';
    const health = new DeadInputDetector(rate);
    node.port.onmessage = event => {
      if (signal.aborted) return;
      const state = health.push(event.data);
      if (state) options.onInputHealth?.(state, label);
      onFrame(event.data, rate);
    };
    context.createMediaStreamSource(stream).connect(node);
    // The processor emits silence; connect it to keep the audio graph scheduled.
    node.connect(context.destination);
    await context.resume();
    if (context.state !== 'running') throw new Error('Click Connect to enable microphone audio.');
    return close;
  } catch (error) { close(); throw error; }
}
