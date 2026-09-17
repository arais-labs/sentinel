import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = await readFile(new URL('../src/lib/voice-captions.ts', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source, 'voice-captions.ts');
const { voiceCaptionLines } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const measure = text => Array.from(new Intl.Segmenter(undefined, {granularity:'grapheme'}).segment(text)).length * 10;

test('caption phrases fit and preserve words, long identifiers, and Unicode graphemes', () => {
  for (const text of ['One voice. All your chats.', 'word '.repeat(500), 'x'.repeat(900), '👩🏽‍💻'.repeat(40), '你好世界'.repeat(30), 'A\n multiline\t response']) {
    const lines = voiceCaptionLines(text, 120, measure);
    assert.ok(lines.every(line => line.length && measure(line) <= 120));
    assert.equal(lines.join('').replaceAll(' ', ''), text.replace(/\s+/gu, ''));
  }
  assert.deepEqual(voiceCaptionLines('   ', 120, measure), []);
  assert.deepEqual(voiceCaptionLines('One two three', 80, measure), ['One two', 'three']);
});
