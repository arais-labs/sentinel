import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyToolcallEnd,
  applyToolResult,
  defaultStreamingState,
  hasVisibleStreamingText,
  shouldShowThinkingIndicator,
  type StreamingState,
  type StreamingToolCall,
} from '../../src/pages/sessionStreaming';

function withActiveCall(state: StreamingState, call: StreamingToolCall): StreamingState {
  return {
    ...state,
    isThinking: false,
    isStreaming: false,
    activeToolCalls: [call],
    completedToolCalls: [],
    agentIteration: 1,
    text: '',
  };
}

test('toolcall_end keeps the tool active until tool_result arrives', () => {
  const state = withActiveCall(defaultStreamingState, {
    id: 'tool-1',
    name: 'runtime',
    argumentsJson: '{"shell_command":"echo 0"}',
    outputJson: '',
    isError: false,
    metadata: {},
    complete: false,
    contentIndex: 0,
  });

  const afterEnd = applyToolcallEnd(state, 'tool-1', 0);
  assert.equal(afterEnd.activeToolCalls.length, 1);
  assert.equal(afterEnd.completedToolCalls.length, 0);
  assert.equal(afterEnd.activeToolCalls[0]?.complete, true);

  const afterResult = applyToolResult(afterEnd, {
    callId: 'tool-1',
    toolName: 'runtime',
    fallbackArguments: '{"shell_command":"echo 0"}',
    outputJson: '{"stdout":"0"}',
    isError: false,
    metadata: {},
    keepsWaitingState: false,
  });
  assert.equal(afterResult.activeToolCalls.length, 0);
  assert.equal(afterResult.completedToolCalls.length, 1);
  assert.equal(afterResult.completedToolCalls[0]?.outputJson, '{"stdout":"0"}');
  assert.equal(afterResult.isThinking, true);
});

test('tool_result can still synthesize a completed tool when no active call exists', () => {
  const afterResult = applyToolResult(defaultStreamingState, {
    callId: 'tool-2',
    toolName: 'runtime',
    fallbackArguments: '{"shell_command":"echo 2"}',
    outputJson: '{"stdout":"2"}',
    isError: false,
    metadata: {},
    keepsWaitingState: false,
  });

  assert.equal(afterResult.activeToolCalls.length, 0);
  assert.equal(afterResult.completedToolCalls.length, 1);
  assert.equal(afterResult.completedToolCalls[0]?.id, 'tool-2');
  assert.equal(afterResult.timeline.length, 1);
});

test('whitespace-only streaming text does not suppress thinking indicator', () => {
  const state: StreamingState = {
    ...defaultStreamingState,
    isThinking: true,
    text: '   \n',
    agentIteration: 2,
  };

  assert.equal(hasVisibleStreamingText(state.text), false);
  assert.equal(
    shouldShowThinkingIndicator(state, {
      streamBusy: true,
      hasPendingApproval: false,
    }),
    true,
  );
});
