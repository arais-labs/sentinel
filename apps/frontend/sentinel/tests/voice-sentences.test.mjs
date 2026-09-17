import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const api = await transformWithOxc('export const requestJson = async () => { throw new Error("no network in tests"); };', 'api.ts');
const source = (await readFile(new URL('../src/lib/voice-speech.ts', import.meta.url), 'utf8')).replace("'./api'", JSON.stringify(url(api.code)));
const { code } = await transformWithOxc(source, 'voice-speech.ts');
const { SpokenSentences, spokenText } = await import(url(code));

const collect = deltas => {
  const out = [];
  const stream = new SpokenSentences(sentence => out.push(sentence));
  for (const delta of deltas) stream.push(delta);
  stream.flush();
  return out;
};

test('streamed deltas become whole sentences and the remainder is flushed', () => {
  assert.deepEqual(collect(['I will check', ' the build. Then I ', 'report back. Almost done']),
    ['I will check the build.', 'Then I report back.', 'Almost done']);
});

test('list markers are never spoken alone and are not read aloud', () => {
  assert.deepEqual(collect(['1. Check the build. ', '2. Then report back. ', 'Done.']),
    ['Check the build.', 'Then report back.', 'Done.']);
});

test('thinking tags are never spoken, even when split across deltas', () => {
  assert.deepEqual(collect(['<thi', 'nk>private plan</th', 'ink>No active chats found.']), ['No active chats found.']);
  assert.equal(spokenText('Private reasoning.\n</think>\nNo active chats found.'), 'No active chats found.');
  assert.equal(spokenText('<think>Unfinished reasoning'), '');
});

test('markdown is stripped before speech and run-on text is cut at a word boundary', () => {
  assert.equal(spokenText('**Bold** and `code` with [a link](http://x) and\n- a bullet'), 'Bold and code with a link and a bullet');
  const chunks = collect(['word '.repeat(120)]);
  assert.ok(chunks.length > 1 && chunks.every(chunk => chunk.length <= 240 && !chunk.startsWith(' ')));
  assert.equal(chunks.join(' ').split(' ').length, 120);
});
