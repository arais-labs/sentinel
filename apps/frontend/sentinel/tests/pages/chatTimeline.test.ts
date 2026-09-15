import assert from 'node:assert/strict';
import test from 'node:test';
import { buildChatTimeline, messageTime } from '../../src/pages/chatTimeline';
import { defaultStreamingState, type StreamingState } from '../../src/pages/sessionStreaming';
import type { Message } from '../../src/types/api';

const stamp = (id: string, second: number) => ({ id, created_at: `2026-09-10T06:14:${second}.000Z` });
const message = (id: string, second: number, metadata = {}) => ({ id, role: 'user', content: id, created_at: stamp(id, second).created_at, metadata } as Message);
const call = { id: 'call', name: 'runtime', argumentsJson: '{}', outputJson: '', isError: false, metadata: {}, complete: false, contentIndex: null };
const state: StreamingState = {
  ...defaultStreamingState,
  timeline: [
    { kind: 'tool', key: 'tool', callKey: 'call::na', presentation: stamp('tool', 15) },
    { kind: 'text', key: 'text', text: 'Existing reply', presentation: stamp('text', 14) },
  ], activeToolCalls: [call],
};

test('steering follows already visible text and tools; subsequent output follows steering', () => {
  const live = { ...state, text: 'New reply', textPresentation: stamp('next', 18) };
  assert.deepEqual(buildChatTimeline([message('initial', 10), message('steer', 17)], live).map(r => r.key), ['initial', 'text', 'tool', 'steer', 'next']);
});

test('history replaces live identities without duplicates or movement', () => {
  const savedText = { ...message('saved-text', 20, { presentation: stamp('text', 14) }), role: 'assistant' } as Message;
  const savedTool = { ...message('saved-tool', 23, { presentation: stamp('tool', 15) }), role: 'tool_result' } as Message;
  const history = [message('initial', 10), message('steer', 17), savedText, savedTool];
  const during = buildChatTimeline(history, state);
  const refreshed = buildChatTimeline(history, defaultStreamingState);
  assert.deepEqual(during.map(r => r.key), ['initial', 'text', 'tool', 'steer']);
  assert.deepEqual(refreshed.map(r => r.key), during.map(r => r.key));
  assert.ok(during.every(r => r.kind === 'message'));
});

test('tool completion updates its original row and session reset drops live items', () => {
  const completed = { ...state, activeToolCalls: [], completedToolCalls: [{ ...call, complete: true, outputJson: 'ok' }] };
  const row = buildChatTimeline([message('steer', 17)], completed).find(r => r.kind === 'tool');
  assert.ok(row?.kind === 'tool' && !row.active && row.call.outputJson === 'ok');
  assert.deepEqual(buildChatTimeline([message('other-session', 10)], defaultStreamingState).map(r => r.key), ['other-session']);
});

test('legacy UTC timestamps and pagination remain chronological', () => {
  assert.equal(messageTime('2026-09-10T06:14:17'), messageTime('2026-09-10T06:14:17Z'));
  assert.deepEqual(buildChatTimeline([message('new', 17), message('old', 10)], defaultStreamingState).map(r => r.key), ['old', 'new']);
});


test('stale pending history cannot overwrite a live tool completion', () => {
  const pending = { ...message('saved-tool', 20, { presentation: stamp('tool', 15), pending: true }), role: 'tool_result' } as Message;
  const completed = { ...state, activeToolCalls: [], completedToolCalls: [{ ...call, complete: true, outputJson: 'done' }] };
  const row = buildChatTimeline([pending], completed).find(r => r.key === 'tool');
  assert.ok(row?.kind === 'tool' && row.call.outputJson === 'done');
});

test('fork notice is visible while internal system context stays hidden', () => {
  const notice = { ...message('fork', 20, { source: 'session_fork', notice: { title: 'Session fork' } }), role: 'system' } as Message;
  const internal = { ...message('context', 19, { source: 'runtime_context' }), role: 'system' } as Message;
  assert.deepEqual(buildChatTimeline([internal, notice], defaultStreamingState).map(r => r.key), ['fork']);
});

test('only explicit notice metadata exposes system messages or changes user presentation', () => {
  const legacy = { ...message('legacy', 10, { source: 'runtime_job_completion' }), role: 'system' } as Message;
  const report = { ...message('report', 11, { notice: { title: 'Background job report' } }), role: 'system' } as Message;
  assert.deepEqual(buildChatTimeline([legacy, report], defaultStreamingState).map(r => r.key), ['report']);
});

test('live notice is replaced by saved history using its presentation identity', () => {
  const metadata = { notice: { title: 'Background job report' }, presentation: stamp('job-notice', 12) };
  const live = { ...message('runtime-job', 12, metadata), role: 'system' } as Message;
  const saved = { ...message('database-id', 15, metadata), role: 'system' } as Message;
  const rows = buildChatTimeline([live, saved], defaultStreamingState);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].key, 'job-notice');
  assert.equal(rows[0].time, messageTime(metadata.presentation.created_at));
});
