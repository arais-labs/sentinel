import { voiceWav } from './voice-audio';

/** Pauses delimit dictation; volume never decides whether Whisper receives it. */
export class DictationBuffer {
  private chunks: Float32Array[] = [];
  private samples = 0;
  private quiet = 0;
  private peak = 0;
  constructor(private rate: number, private emit: (audio: Blob) => void) {}

  push(frame: Float32Array): number {
    if (!frame.length) return 0;
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    this.chunks.push(frame.slice());
    this.samples += frame.length;
    this.peak = Math.max(this.peak, rms);
    this.quiet = rms < Math.max(.0005, this.peak * .2) ? this.quiet + frame.length / this.rate : 0;
    // Keep continuous speech bounded, and let Whisper's VAD decide what is speech.
    // Even quiet input gets submitted at the 8-second limit or when Stop is pressed.
    if (this.samples / this.rate >= 8 || (this.samples / this.rate >= 1 && this.peak > .003 && this.quiet >= .7)) this.flush();
    return rms;
  }

  flush() {
    if (!this.samples) return;
    const audio = voiceWav(this.chunks, this.rate);
    this.chunks = []; this.samples = 0; this.quiet = 0; this.peak = 0;
    this.emit(audio);
  }
}
