import assert from 'node:assert/strict';
import test from 'node:test';
import { summarizeToolField, summarizeToolResult } from '../../src/lib/toolResultSummary';

test('summarizes arbitrary collections including empty results', () => {
  assert.equal(summarizeToolResult('{"tasks":[]}'), 'tasks: 0');
  assert.equal(summarizeToolResult('{"widgets":[{},{}]}'), 'widgets: 2');
  assert.equal(summarizeToolResult('[1,2]'), '2 items — 1; 2');
});
test('summarizes status fields without tool-specific mappings', () => {
  assert.equal(summarizeToolResult('{"success":true,"running":false,"connected_chats":0}'), 'running: no · connected chats: 0');
});
test('unwraps envelopes and prioritizes errors', () => {
  assert.equal(summarizeToolResult(JSON.stringify({content:[{type:'text',text:'{"data":{"accounts":[]}}'}]})), 'accounts: 0');
  assert.equal(summarizeToolResult('{"error":{"message":"Connection refused"},"items":[]}'), 'Connection refused');
  assert.equal(summarizeToolResult('{"stderr":"Permission denied","returncode":1}', true), 'Permission denied');
});
test('keeps summaries bounded and excludes sensitive fields', () => {
  assert.equal(summarizeToolResult('{"api_key":"hidden","connected":true}'), 'connected: yes');
  assert.equal(summarizeToolResult('{"message":"Bearer abcdefghijklmnop"}'), '[redacted]');
  assert.ok(summarizeToolResult('x'.repeat(1000)).length <= 181);
  assert.equal(summarizeToolResult('{}'), '');
});

test('previews collection items using identity fields and limits the number shown', () => {
  assert.equal(summarizeToolResult(JSON.stringify({tasks: [
    {id: 'internal', title: 'Fix login'}, {name: 'Review'}, {label: 'Deploy'}, {title: 'Later'},
  ]})), 'tasks: 4 — Fix login; Review; Deploy; +1 more');
  assert.equal(summarizeToolResult('{"items":[{"port":8080,"enabled":true}]}'), 'items: 1 — port: 8080, enabled: true');
  assert.equal(summarizeToolResult('{"items":[{"password":"hidden","name":"Public"}]}'), 'items: 1 — Public');
});
test('summarizes JSON strings inside collections instead of printing their syntax', () => {
  assert.equal(summarizeToolResult(JSON.stringify([{name: '{"title":"Nested"}'}])), '1 item — Nested');
  assert.ok(summarizeToolResult(JSON.stringify([{name: 'x'.repeat(500)}])).length < 100);
});

test('handles JSON in stdout, fenced text, and double encoded results', () => {
  const payload = JSON.stringify({items: [{title: 'Agent Identity'}]});
  for (const raw of [payload, JSON.stringify(payload), JSON.stringify(JSON.stringify(payload)), '```json\n' + payload + '\n```', JSON.stringify({stdout: payload})]) {
    assert.equal(summarizeToolResult(raw), 'items: 1 — Agent Identity');
  }
});
test('does not dump incomplete structured payloads into a summary', () => {
  assert.equal(summarizeToolResult('{"items": [{"id": "abc", "content": "cut off…'), 'Structured result · Inspect for details');
});

test('summarizes full input fields before preview truncation', () => {
  assert.equal(summarizeToolField(JSON.stringify({items: [{content: 'x'.repeat(500), title: 'Agent Identity'}]})), 'items: 1 — Agent Identity');
  assert.equal(summarizeToolField('ls -la'), 'ls -la');
});
