import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = await readFile(new URL('../src/lib/voice-audio.ts', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source, 'voice-audio.ts');
const { DeadInputDetector, deadInputMessage, microphoneConstraints } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const frame = (amplitude = 0) => new Float32Array(320).fill(amplitude);

test('a device delivering only digital zeros is reported dead once, then recovers once', () => {
  const detector = new DeadInputDetector(16000);
  const events = [];
  for (let index = 0; index < 200; index++) { const state = detector.push(frame()); if (state) events.push(state); }
  assert.deepEqual(events, ['dead']);
  for (let index = 0; index < 5; index++) { const state = detector.push(frame(.0004)); if (state) events.push(state); }
  assert.deepEqual(events, ['dead', 'alive']);
});

test('a quiet room is never mistaken for a dead device', () => {
  const detector = new DeadInputDetector(16000);
  for (let index = 0; index < 400; index++) assert.equal(detector.push(frame(index % 7 ? .0002 : 0)), null);
});

test('short silences before speech do not trip the detector', () => {
  const detector = new DeadInputDetector(16000);
  for (let index = 0; index < 100; index++) assert.equal(detector.push(frame()), null);
  assert.equal(detector.push(frame(.02)), null);
});

test('the message names the device and mentions the lid only for built-in microphones', () => {
  assert.match(deadInputMessage('Default - Micro MacBook Pro (Built-in)'), /lid is closed/);
  assert.match(deadInputMessage('USB Audio Device'), /Check the device/);
  assert.match(deadInputMessage(''), /the microphone/);
});

test('a chosen device is requested exactly; the default keeps processing constraints', () => {
  assert.deepEqual(microphoneConstraints('abc').deviceId, { exact: 'abc' });
  assert.equal(microphoneConstraints().deviceId, undefined);
  assert.equal(microphoneConstraints('abc').echoCancellation, true);
});
