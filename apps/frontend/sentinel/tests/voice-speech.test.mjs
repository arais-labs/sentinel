import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = (await readFile(new URL('../src/lib/voice-speech.ts', import.meta.url), 'utf8'))
  .replace("import { requestJson } from './api';", 'const requestJson = () => { throw new Error("No network in chunk tests"); };');
const { code } = await transformWithOxc(source, 'voice-speech.ts');
const { speechChunks, VoiceSpeechQueue } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('sentences are spoken whole; only run-on text is cut, at word boundaries, within 240 characters', () => {
  for (const text of ['Hello. Your agents are ready.', 'word '.repeat(200).trim(), 'x'.repeat(900)]) {
    const chunks = speechChunks(text);
    assert.ok(chunks.every(chunk => chunk.length > 0 && chunk.length <= 240));
    assert.equal(chunks.join('').replaceAll(' ', ''), text.replaceAll(' ', ''));
  }
  assert.deepEqual(speechChunks('   '), []);
  assert.deepEqual(speechChunks('I will check the build and report back to you as soon as the whole suite has finished running.'),
    ['I will check the build and report back to you as soon as the whole suite has finished running.']);
});

test('short fragments join the next sentence and long text splits after sentence ends', () => {
  const chunks = speechChunks('1. ' + 'Check the build and the lint results. '.repeat(10));
  assert.ok(chunks[0].startsWith('1. Check the build'));
  assert.ok(chunks.every(chunk => /[.!?]$/.test(chunk)), JSON.stringify(chunks));
});

test('progress and final text stay ordered; abort discards queued text and releases readers', async () => {
  const queue = new VoiceSpeechQueue();
  const iterator = queue[Symbol.asyncIterator]();
  const waiting = iterator.next();
  queue.push('Checking now.');
  assert.equal((await waiting).value, 'Checking now.');
  queue.push('Done.');
  queue.close();
  assert.equal((await iterator.next()).value, 'Done.');
  assert.equal((await iterator.next()).done, true);

  const cancelled = new VoiceSpeechQueue();
  const reader = cancelled[Symbol.asyncIterator]();
  const blocked = reader.next();
  cancelled.push('Do not play.');
  cancelled.close(true);
  assert.equal((await blocked).done, true);
  cancelled.push('Do not restart.');
  assert.equal((await reader.next()).done, true);
});
