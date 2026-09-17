// Audio remains in memory and leaves this processor only while Voice is connected.
class VoiceCapture extends AudioWorkletProcessor {
  buffer = new Float32Array(2048);
  offset = 0;
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const sample of input) {
      this.buffer[this.offset++] = sample;
      if (this.offset === this.buffer.length) {
        this.port.postMessage(this.buffer, [this.buffer.buffer]);
        this.buffer = new Float32Array(2048);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('sentinel-voice-capture', VoiceCapture);
