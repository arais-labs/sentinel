import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const audio = await transformWithOxc(await readFile(new URL('../src/lib/voice-audio.ts', import.meta.url), 'utf8'), 'voice-audio.ts');
const source = (await readFile(new URL('../src/lib/dictation-audio.ts', import.meta.url), 'utf8')).replace("'./voice-audio'", JSON.stringify(url(audio.code)));
const { code } = await transformWithOxc(source, 'dictation-audio.ts');
const { DictationBuffer } = await import(url(code));
const frame = amplitude => new Float32Array(320).fill(amplitude);

test('quiet dictation is submitted on Stop instead of rejected by a volume gate', async () => {
  const chunks = [];
  const buffer = new DictationBuffer(16000, audio => chunks.push(audio));
  for (let index = 0; index < 50; index++) buffer.push(frame(.001));
  buffer.flush();
  assert.equal(chunks.length, 1);
  const wav = new DataView(await chunks[0].arrayBuffer());
  assert.equal(wav.getUint32(40, true), 16000 * 2);
  assert.ok(wav.getInt16(44, true) > 0);
});

test('speech followed by a pause is transcribed before Stop', () => {
  const chunks = [];
  const buffer = new DictationBuffer(16000, audio => chunks.push(audio));
  for (let index = 0; index < 50; index++) buffer.push(frame(.03));
  assert.equal(chunks.length, 0);
  for (let index = 0; index < 36; index++) buffer.push(frame(0));
  assert.equal(chunks.length, 1);
});

test('continuous speech emits bounded chunks without duplicate final flush', () => {
  const chunks = [];
  const buffer = new DictationBuffer(16000, audio => chunks.push(audio));
  for (let index = 0; index < 800; index++) buffer.push(frame(.03));
  assert.equal(chunks.length, 2);
  assert.ok(chunks.every(chunk => chunk.size === 44 + 8 * 16000 * 2));
  buffer.flush(); buffer.flush();
  assert.equal(chunks.length, 2);
});

test('an empty recording does not enqueue a transcription', () => {
  const chunks = [];
  const buffer = new DictationBuffer(16000, audio => chunks.push(audio));
  buffer.flush();
  assert.equal(chunks.length, 0);
});
