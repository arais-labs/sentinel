import { requestJson } from './api';

/** Formatting is never spoken, even when a model ignores the spoken-output contract. */
export function spokenText(text: string): string {
  let value = text;
  if (value.includes('</think>')) value = value.slice(value.lastIndexOf('</think>') + 8);
  else if (value.includes('<think>')) value = value.slice(0, value.indexOf('<think>'));
  value = value.replace(/```[^\n]*\n?/g, '');
  value = value.replace(/!?\[([^\]]+)\]\([^)]*\)/g, '$1');
  value = value.replace(/^\s*(?:#{1,6}\s+|>\s*|[-+•]\s+|\d+[.)]\s+)/gm, '');
  value = value.replace(/[*`]/g, '').replace(/_/g, ' ');
  return value.split(/\s+/).filter(Boolean).join(' ').trim();
}

/** Turns streamed text deltas into whole sentences; thinking tags are discarded. */
export class SpokenSentences {
  private buffer = '';
  private tags = '';
  private thinking = false;
  constructor(private emit: (sentence: string) => void) {}
  push(delta: string) {
    this.tags += delta;
    while (this.tags) {
      if (this.tags.startsWith('<think>')) { this.thinking = true; this.tags = this.tags.slice(7); }
      else if (this.tags.startsWith('</think>')) { this.thinking = false; this.tags = this.tags.slice(8); }
      else if ('<think>'.startsWith(this.tags) || '</think>'.startsWith(this.tags)) break;
      else { if (!this.thinking) this.buffer += this.tags[0]; this.tags = this.tags.slice(1); }
    }
    while (this.buffer) {
      let end = 0;
      for (const match of this.buffer.matchAll(/[.!?…](?:["'’”])?\s+/g)) {
        // A fragment such as a list marker is spoken with the sentence that follows it.
        if (this.buffer.slice(0, match.index).trim().length >= 12) { end = match.index! + match[0].length; break; }
      }
      if (end && end <= 240) { /* sentence boundary found */ }
      else if (this.buffer.length >= 240) { const space = this.buffer.lastIndexOf(' ', 240); end = space > 0 ? space : 240; }
      else break;
      this.send(this.buffer.slice(0, end));
      this.buffer = this.buffer.slice(end);
    }
  }
  flush() { this.send(this.buffer); this.buffer = this.tags = ''; this.thinking = false; }
  private send(text: string) { const spoken = spokenText(text); if (spoken) this.emit(spoken); }
}

/** Whole sentences are the speech unit: cutting inside one breaks its intonation and adds pauses. */
export function speechChunks(text: string): string[] {
  const chunks: string[] = [];
  let rest = text.trim();
  while (rest) {
    let end = rest.length;
    if (end > 240) {
      const sentence = rest.slice(0, 240).match(/^.*[.!?…](?:["'’”])?\s/s)?.[0].length;
      end = sentence || rest.lastIndexOf(' ', 240);
      if (end < 1) end = 240;
    }
    const chunk = rest.slice(0, end).trim();
    rest = rest.slice(end).trim();
    // A fragment such as a list marker is spoken with the sentence that follows it.
    if (chunks.length && chunks[chunks.length - 1].length < 12) chunks[chunks.length - 1] += ' ' + chunk;
    else chunks.push(chunk);
  }
  return chunks;
}

function playAudio(encoded: string, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
  const audio = new Audio(url);
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      audio.onended = null; audio.onerror = null;
      audio.pause(); audio.removeAttribute('src'); audio.load();
      URL.revokeObjectURL(url);
      if (error) reject(error); else resolve();
    };
    const abort = () => finish();
    const timer = setTimeout(() => finish(new Error('Speech playback timed out.')), 60_000);
    audio.onended = () => finish();
    audio.onerror = () => finish(new Error('Could not play local speech.'));
    signal.addEventListener('abort', abort, { once: true });
    void audio.play().catch(error => finish(error));
  });
}

/** A single turn's text inbox. Closing releases a pending read, including on abort. */
export class VoiceSpeechQueue implements AsyncIterable<string> {
  private values: string[] = [];
  private wake?: () => void;
  private closed = false;
  private characters = 0;
  push(text: string) {
    if (this.closed) return;
    if (this.characters + text.length > 64_000) throw new Error('Voice reply is too long to queue.');
    this.characters += text.length;
    this.values.push(text);
    this.wake?.();
  }
  close(discard = false) {
    this.closed = true;
    if (discard) { this.values = []; this.characters = 0; }
    this.wake?.();
  }
  async *[Symbol.asyncIterator]() {
    while (true) {
      const text = this.values.shift();
      if (text !== undefined) { this.characters -= text.length; yield text; }
      else if (this.closed) return;
      else await new Promise<void>(resolve => { this.wake = resolve; });
    }
  }
}

export async function speakLocal(path: string, lease: string, text: string | AsyncIterable<string>, signal: AbortSignal, speed = 1.05, parentTraceId?: string | null, onText?: (text: string) => void, onPlaying?: (playing: boolean) => void) {
  async function* chunks() {
    const phrases = typeof text === 'string' ? [text] : text;
    for await (const phrase of phrases) {
      for (const chunk of speechChunks(phrase)) yield { chunk, caption: phrase };
    }
  }
  const iterator = chunks();
  const generate = (chunk: string) => requestJson<{ audio: string }>(`${path}/speak`, {
    method: 'POST', body: { text: chunk, lease_id: lease, speed, parent_trace_id: parentTraceId }, signal, timeoutMs: 50_000,
  }).then(result => ({ result, text: chunk }), error => ({ error }));
  const next = async () => {
    const chunk = await iterator.next();
    if (chunk.done || signal.aborted) return null;
    return { ...await generate(chunk.value.chunk), caption: chunk.value.caption };
  };
  let pending = next();
  while (!signal.aborted) {
    const outcome = await pending;
    if (!outcome || signal.aborted) return;
    if ('error' in outcome) throw outcome.error;
    // Synthesize at most one chunk ahead while the current audio plays.
    pending = next();
    onText?.(outcome.caption);
    onPlaying?.(true);
    try { await playAudio(outcome.result.audio, signal); } finally { onPlaying?.(false); }
  }
}
