import assert from 'node:assert/strict';
import test from 'node:test';
import { messageNotice } from '../../src/lib/message-notice';
import type { Message } from '../../src/types/api';

const message = (metadata: Record<string, unknown>, content = '[Agent message from someone] hello') => ({ role: 'user', metadata, content } as Message);

test('requires explicit metadata, never source labels or bracketed content', () => {
  for (const metadata of [{}, { source: 'agent_message' }, { source: 'sub_agent' }, { source: 'web', steering: 'delivered' }, { notice: 'wrong' }, { notice: { title: '' } }]) {
    assert.equal(messageNotice(message(metadata)), null);
  }
});
test('notice uses its producer title and optional display body', () => {
  assert.deepEqual(messageNotice(message({ notice: { title: 'Agent message', body: 'Hello' } })), { title: 'Agent message', body: 'Hello' });
  assert.deepEqual(messageNotice(message({ notice: { title: 'Delegated task' } })), { title: 'Delegated task' });
});
