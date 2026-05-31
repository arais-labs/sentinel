export interface StreamingToolCall {
  id: string;
  name: string;
  argumentsJson: string;
  outputJson: string;
  isError: boolean;
  metadata: Record<string, unknown>;
  complete: boolean;
  contentIndex: number | null;
}

export interface StreamTimelineToolItem {
  kind: 'tool';
  key: string;
  callKey: string;
}

export interface StreamTimelineTextItem {
  kind: 'text';
  key: string;
  text: string;
}

export type StreamTimelineItem = StreamTimelineToolItem | StreamTimelineTextItem;

export interface StreamingState {
  connection: string;
  isThinking: boolean;
  isStreaming: boolean;
  isCompactingContext: boolean;
  text: string;
  timeline: StreamTimelineItem[];
  interimTextSeq: number;
  activeToolCalls: StreamingToolCall[];
  completedToolCalls: StreamingToolCall[];
  agentIteration: number;
  agentMaxIterations: number;
}

export const defaultStreamingState: StreamingState = {
  connection: 'disconnected',
  isThinking: false,
  isStreaming: false,
  isCompactingContext: false,
  text: '',
  timeline: [],
  interimTextSeq: 0,
  activeToolCalls: [],
  completedToolCalls: [],
  agentIteration: 0,
  agentMaxIterations: 0,
};

export function streamingCallKeyFromParts(id: string, contentIndex: number | null): string {
  return `${id}::${contentIndex ?? 'na'}`;
}

export function streamingCallKey(call: StreamingToolCall): string {
  return streamingCallKeyFromParts(call.id, call.contentIndex);
}

export function hasVisibleStreamingText(text: string): boolean {
  return text.trim().length > 0;
}

export function shouldShowThinkingIndicator(
  state: StreamingState,
  options: {
    streamBusy: boolean;
    hasPendingApproval: boolean;
  },
): boolean {
  return (
    !state.isCompactingContext &&
    !options.hasPendingApproval &&
    state.activeToolCalls.length === 0 &&
    !state.isStreaming &&
    !hasVisibleStreamingText(state.text) &&
    (state.isThinking || options.streamBusy)
  );
}

export function applyToolcallEnd(
  current: StreamingState,
  eventCallId: string,
  eventContentIndex: number | null,
): StreamingState {
  if (!current.activeToolCalls.length) return current;
  const nextActive = [...current.activeToolCalls];
  let targetIndex = -1;
  if (eventCallId) {
    targetIndex = nextActive.findIndex((item) => item.id === eventCallId);
  }
  if (targetIndex < 0 && eventContentIndex !== null) {
    targetIndex = nextActive.findIndex((item) => item.contentIndex === eventContentIndex);
  }
  if (targetIndex < 0) targetIndex = nextActive.length - 1;
  nextActive[targetIndex] = {
    ...nextActive[targetIndex],
    complete: true,
  };
  return {
    ...current,
    isThinking: false,
    activeToolCalls: nextActive,
  };
}

interface ToolResultUpdate {
  callId: string;
  toolName: string;
  fallbackArguments: string;
  outputJson: string;
  isError: boolean;
  metadata: Record<string, unknown>;
  keepsWaitingState: boolean;
}

export function applyToolResult(
  current: StreamingState,
  update: ToolResultUpdate,
): StreamingState {
  const hydrate = (call: StreamingToolCall): StreamingToolCall => ({
    ...call,
    argumentsJson: call.argumentsJson.trim().length > 0 ? call.argumentsJson : update.fallbackArguments,
    outputJson: update.outputJson,
    isError: update.isError,
    metadata: update.metadata,
    complete: true,
  });

  const activeIndex = update.callId
    ? current.activeToolCalls.findIndex((call) => call.id === update.callId)
    : -1;
  if (activeIndex >= 0) {
    const activeCall = hydrate(current.activeToolCalls[activeIndex]);
    const nextActive = [...current.activeToolCalls];
    nextActive.splice(activeIndex, 1);
    const nextCompleted = [...current.completedToolCalls];
    const completedIndex = nextCompleted.findIndex(
      (call) => call.id === activeCall.id && call.contentIndex === activeCall.contentIndex,
    );
    if (completedIndex >= 0) {
      nextCompleted[completedIndex] = activeCall;
    } else {
      nextCompleted.push(activeCall);
    }
    return {
      ...current,
      isThinking: update.keepsWaitingState ? current.isThinking : true,
      isStreaming: false,
      activeToolCalls: nextActive,
      completedToolCalls: nextCompleted,
    };
  }

  const completedIndex = update.callId
    ? current.completedToolCalls.findIndex((call) => call.id === update.callId)
    : -1;
  if (completedIndex >= 0) {
    const nextCompleted = [...current.completedToolCalls];
    nextCompleted[completedIndex] = hydrate(nextCompleted[completedIndex]);
    return {
      ...current,
      isThinking: update.keepsWaitingState ? current.isThinking : true,
      isStreaming: false,
      completedToolCalls: nextCompleted,
    };
  }

  const syntheticCall: StreamingToolCall = {
    id: update.callId || `tool-result-${Date.now()}`,
    name: update.toolName,
    argumentsJson: update.fallbackArguments,
    outputJson: update.outputJson,
    isError: update.isError,
    metadata: update.metadata,
    complete: true,
    contentIndex: null,
  };
  const syntheticKey = streamingCallKey(syntheticCall);
  const hasTimelineItem = current.timeline.some(
    (item) => item.kind === 'tool' && item.callKey === syntheticKey,
  );
  return {
    ...current,
    isThinking: update.keepsWaitingState ? current.isThinking : true,
    isStreaming: false,
    timeline: hasTimelineItem
      ? current.timeline
      : [...current.timeline, { kind: 'tool', key: `tool-${syntheticKey}`, callKey: syntheticKey }],
    completedToolCalls: [...current.completedToolCalls, syntheticCall],
  };
}
