import assert from 'node:assert/strict';
import test from 'node:test';
import { transformWithOxc } from 'vite';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/pages/sessionStreaming.ts', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source, 'sessionStreaming.ts');
const { applyStreamReset, applyToolcallDiscard, defaultStreamingState, streamingCallKey } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
);

const call = (id, contentIndex) => ({
  id, name: 'workspaces', argumentsJson: '{"files":', outputJson: '', isError: false, metadata: {}, complete: false, contentIndex,
});

test('a discarded streamed call leaves the active calls and the timeline; others stay', () => {
  const kept = call('call_running', 0);
  const cut = call('toolu_cut', 1);
  const state = {
    ...defaultStreamingState,
    activeToolCalls: [kept, cut],
    timeline: [
      { kind: 'text', key: 'interim-1', text: 'Writing files' },
      { kind: 'tool', key: 'tool-a', callKey: streamingCallKey(kept) },
      { kind: 'tool', key: 'tool-b', callKey: streamingCallKey(cut) },
    ],
  };

  const next = applyToolcallDiscard(state, 'toolu_cut');

  assert.deepEqual(next.activeToolCalls.map(item => item.id), ['call_running']);
  assert.deepEqual(next.timeline.map(item => item.key), ['interim-1', 'tool-a']);
  assert.equal(applyToolcallDiscard(state, 'unknown'), state);
});

test('a stream reset voids the failed attempt\'s text and waits for the retry', () => {
  const state = { ...defaultStreamingState, text: 'Partial words', isStreaming: true, textPresentation: { id: 'p', created_at: 'now' } };

  const next = applyStreamReset(state);

  assert.equal(next.text, '');
  assert.equal(next.textPresentation, undefined);
  assert.equal(next.isStreaming, false);
  assert.equal(next.isThinking, true);
});
