import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = await readFile(new URL('../src/lib/voice-audio.ts', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source, 'voice-audio.ts');
const { VoiceInterruptionDetector, VoiceSegmenter } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const frame = (amplitude = 0) => new Float32Array(320).fill(amplitude);

test('quiet audio and isolated clicks do not interrupt replies', () => {
  const detector = new VoiceInterruptionDetector(16000);
  for (let index = 0; index < 100; index++) assert.equal(detector.push(frame(.012)), null);
  for (let click = 0; click < 10; click++) {
    assert.equal(detector.push(frame(.1)), null);
    for (let index = 0; index < 5; index++) assert.equal(detector.push(frame()), null);
  }
});

test('sustained speech interrupts and preserves the onset for transcription', async () => {
  const detector = new VoiceInterruptionDetector(16000);
  let utterance;
  const segmenter = new VoiceSegmenter(16000, audio => { utterance = audio; });
  for (let index = 0; index < 20; index++) detector.push(frame());
  const spoken = [];
  let onset;
  for (let index = 0; index < 15 && !onset; index++) {
    spoken.push(frame(.08));
    onset = detector.push(spoken.at(-1));
  }
  assert.ok(onset);
  assert.ok(onset.includes(spoken[0]), 'The first speech frame must survive confirmation');
  assert.ok(onset.length * .02 <= .37, 'Pre-roll must stay bounded');
  for (const buffered of onset) segmenter.push(buffered);
  for (let index = 0; index < 35; index++) segmenter.push(frame());
  assert.ok(utterance, 'The interrupting phrase becomes a normal utterance');
  const samples = new Int16Array(await utterance.arrayBuffer(), 44);
  assert.ok(samples.filter(sample => sample !== 0).length >= spoken.length * 320);
});

test('reset drops a partial interruption before the next reply or after mute', () => {
  const detector = new VoiceInterruptionDetector(16000);
  for (let index = 0; index < 8; index++) assert.equal(detector.push(frame(.1)), null);
  detector.reset();
  for (let index = 0; index < 8; index++) assert.equal(detector.push(frame(.1)), null);
});
