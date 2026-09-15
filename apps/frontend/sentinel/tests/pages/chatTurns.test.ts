import assert from 'node:assert/strict';
import test from 'node:test';
import { groupChatTurns } from '../../src/pages/chatTurns';
import type { ChatRow } from '../../src/pages/chatTimeline';
import type { Message } from '../../src/types/api';
const row = (key: string, role: string, metadata = {}): ChatRow => ({ key, time: 0, kind: 'message', message: { id: key, role, content: key, metadata } as Message });
test('completed tool turns collapse, active turns and plain replies do not', () => {
  const rows = [row('u','user'),row('t','tool_result'),row('a','assistant')];
  assert.equal(groupChatTurns(rows,false)[0].collapsible,true);
  assert.equal(groupChatTurns(rows,true)[0].collapsible,false);
  assert.equal(groupChatTurns([rows[0],rows[2]],false)[0].collapsible,false);
});
test('steering remains in its original turn and chronological position', () => {
  const rows = [row('u','user'),row('t','tool_result'),row('s','user',{steering:'applied'}),row('a','assistant'),row('u2','user')];
  const turns = groupChatTurns(rows,true);
  assert.equal(turns.length,2);
  assert.deepEqual(turns[0].rows.map(r=>r.key),['t','s','a']);
  assert.equal(turns[0].collapsible,true);
});
test('partial history, pending approvals and unfinished tools stay expanded', () => {
  assert.equal(groupChatTurns([row('t','tool_result'),row('a','assistant')],false)[0].collapsible,false);
  assert.equal(groupChatTurns([row('u','user'),row('t','tool_result',{pending:true}),row('a','assistant')],false)[0].collapsible,false);
  assert.equal(groupChatTurns([row('u','user'),row('t','tool_result')],false)[0].collapsible,false);
});

test('fork notice stands outside copied turns and preserves their folding', () => {
  const turns = groupChatTurns([row('u', 'user'), row('t', 'tool_result'), row('a', 'assistant'), row('fork', 'system', { source: 'session_fork', notice: { title: 'Session fork' } }), row('follow-up', 'user')], false);
  assert.equal(turns.length, 3);
  assert.equal(turns[0].collapsible, true);
  assert.equal(turns[1].collapsible, false);
  assert.deepEqual(turns[1].rows.map(r => r.key), ['fork']);
});

test('agent notices are not user prompts while human steering stays in its turn', () => {
  const turns = groupChatTurns([row('u', 'user'), row('steer', 'user', { source: 'web', steering: 'delivered' }), row('agent', 'user', { notice: { title: 'Agent message' }, steering: 'delivered' })], false);
  assert.deepEqual(turns[0].rows.map(r => r.key), ['steer']);
  assert.equal(turns[1].input, undefined);
  assert.deepEqual(turns[1].rows.map(r => r.key), ['agent']);
});
