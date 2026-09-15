import type { CompletionSound } from '../../../../desktop/sentinel/src/shared/ipc';

let context: AudioContext | undefined;
let active: GainNode | undefined;

const notes: Record<CompletionSound, [number, number, number][]> = {
  soft: [[523.25, 0, 0.55], [659.25, 0.14, 0.65]],
  chime: [[659.25, 0, 0.65], [783.99, 0.16, 0.65], [1046.5, 0.32, 0.8]],
  glass: [[880, 0, 0.9], [1320, 0.03, 0.65]],
};

export async function playCompletionSound(sound: CompletionSound): Promise<void> {
  context ??= new AudioContext();
  await context.resume();
  active?.disconnect();
  const mix = context.createGain();
  mix.gain.value = 0.12;
  mix.connect(context.destination);
  active = mix;
  const start = context.currentTime;
  for (const [frequency, offset, duration] of notes[sound]) {
    const oscillator = context.createOscillator();
    const envelope = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = frequency;
    const at = start + offset;
    envelope.gain.setValueAtTime(0, at);
    envelope.gain.linearRampToValueAtTime(0.8, at + 0.025);
    envelope.gain.exponentialRampToValueAtTime(0.001, at + duration);
    oscillator.connect(envelope);
    envelope.connect(mix);
    oscillator.start(at);
    oscillator.stop(at + duration + 0.02);
    oscillator.onended = () => { oscillator.disconnect(); envelope.disconnect(); };
  }
  window.setTimeout(() => { mix.disconnect(); if (active === mix) active = undefined; }, 1500);
}
