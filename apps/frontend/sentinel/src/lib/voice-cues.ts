/** Short synthesized cues so the user knows Voice heard them and when it starts working. */

export type VoiceCue = 'heard' | 'thinking' | 'ongoing';

let context: AudioContext | undefined;

// [frequency Hz, offset s, duration s]; quieter and shorter than completion sounds.
const cues: Record<VoiceCue, [number, number, number][]> = {
  heard: [[880, 0, 0.09], [1174.66, 0.07, 0.14]],
  thinking: [[523.25, 0, 0.22], [659.25, 0.12, 0.3]],
  ongoing: [[659.25, 0, 0.16]],
};

export function playVoiceCue(cue: VoiceCue): void {
  try {
    context ??= new AudioContext();
    if (context.state !== 'running') void context.resume();
    const mix = context.createGain();
    mix.gain.value = cue === 'heard' ? 0.07 : cue === 'ongoing' ? 0.035 : 0.06;
    mix.connect(context.destination);
    const start = context.currentTime;
    for (const [frequency, offset, duration] of cues[cue]) {
      const oscillator = context.createOscillator();
      const envelope = context.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      const at = start + offset;
      envelope.gain.setValueAtTime(0, at);
      envelope.gain.linearRampToValueAtTime(0.9, at + 0.015);
      envelope.gain.exponentialRampToValueAtTime(0.001, at + duration);
      oscillator.connect(envelope);
      envelope.connect(mix);
      oscillator.start(at);
      oscillator.stop(at + duration + 0.02);
      oscillator.onended = () => { oscillator.disconnect(); envelope.disconnect(); };
    }
    window.setTimeout(() => mix.disconnect(), 800);
  } catch {
    // Cues are a convenience; audio hardware or autoplay limits must never break Voice.
  }
}

/** A faint, slowly pulsing hum for the first seconds of thinking, then a soft reminder blip
 * every few seconds while the wait goes on. Returns a stop function that fades everything out. */
const THINKING_HUM_SECONDS = 5;
const THINKING_REMINDER_SECONDS = 5;
export function startThinkingSound(): () => void {
  try {
    context ??= new AudioContext();
    if (context.state !== 'running') void context.resume();
    const ctx = context;
    const mix = ctx.createGain();
    mix.gain.setValueAtTime(0, ctx.currentTime);
    mix.gain.linearRampToValueAtTime(0.028, ctx.currentTime + 1.2);
    mix.connect(ctx.destination);
    // Two soft partials, amplitude-modulated by a slow LFO so it breathes rather than drones.
    const tremolo = ctx.createGain();
    tremolo.gain.value = 0.55;
    const lfo = ctx.createOscillator();
    lfo.type = 'sine'; lfo.frequency.value = 0.35;
    const depth = ctx.createGain();
    depth.gain.value = 0.45;
    lfo.connect(depth); depth.connect(tremolo.gain);
    tremolo.connect(mix);
    const voices = [220, 330.3].map(frequency => {
      const oscillator = ctx.createOscillator();
      oscillator.type = 'sine'; oscillator.frequency.value = frequency;
      oscillator.connect(tremolo);
      oscillator.start();
      return oscillator;
    });
    lfo.start();
    let stopped = false;
    let reminders: number | undefined;
    const release = () => {
      const at = ctx.currentTime;
      mix.gain.cancelScheduledValues(at);
      mix.gain.setValueAtTime(mix.gain.value, at);
      mix.gain.linearRampToValueAtTime(0, at + 0.6);
      window.setTimeout(() => { for (const voice of voices) { voice.stop(); voice.disconnect(); } lfo.stop(); lfo.disconnect(); depth.disconnect(); tremolo.disconnect(); mix.disconnect(); }, 700);
    };
    // The hum is bounded; afterwards a quiet blip says the wait is still going.
    const humTimer = window.setTimeout(() => {
      if (stopped) return;
      release();
      reminders = window.setInterval(() => { if (!stopped) playVoiceCue('ongoing'); }, THINKING_REMINDER_SECONDS * 1000);
    }, THINKING_HUM_SECONDS * 1000);
    return () => {
      if (stopped) return;
      stopped = true;
      window.clearTimeout(humTimer);
      if (reminders !== undefined) window.clearInterval(reminders); else release();
    };
  } catch {
    return () => {};
  }
}
