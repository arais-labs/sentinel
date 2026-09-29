// Fixed 80 ms ceiling, 20 ms starting cushion. Late network bursts discard old
// sound instead of building an ever-growing delay behind the visible desktop.
class DesktopAudioOutput extends AudioWorkletProcessor {
  channels = [new Float32Array(3840), new Float32Array(3840)];
  read = 0;
  length = 0;
  primed = false;
  closed = false;
  constructor() {
    super();
    this.port.onmessage = ({ data }) => {
      if (data === 'reset' || data === 'close') {
        this.read = this.length = 0;
        this.primed = false;
        this.closed = data === 'close';
        return;
      }
      if (this.closed || !(data instanceof Float32Array) || data.length !== 960) return;
      const capacity = this.channels[0].length;
      if (this.length + 480 > capacity) {
        // Retain only the newest 10 ms, then append the arriving frame.
        this.read = (this.read + this.length - 480) % capacity;
        this.length = 480;
      }
      for (let frame = 0; frame < 480; frame++) {
        const offset = (this.read + this.length + frame) % capacity;
        this.channels[0][offset] = data[frame];
        this.channels[1][offset] = data[480 + frame];
      }
      this.length += 480;
    };
  }
  process(_inputs, outputs) {
    if (this.closed) return false;
    const output = outputs[0];
    if (!output?.[0]) return true;
    for (const channel of output) channel.fill(0);
    if (!this.primed) {
      if (this.length < 960) return true;
      this.primed = true;
    }
    const count = Math.min(this.length, output[0].length);
    for (let frame = 0; frame < count; frame++) {
      const offset = (this.read + frame) % this.channels[0].length;
      for (let channel = 0; channel < output.length; channel++) output[channel][frame] = this.channels[channel % 2][offset];
    }
    this.read = (this.read + count) % this.channels[0].length;
    this.length -= count;
    if (count < output[0].length) this.primed = false;
    return true;
  }
}
registerProcessor('sentinel-desktop-audio', DesktopAudioOutput);
