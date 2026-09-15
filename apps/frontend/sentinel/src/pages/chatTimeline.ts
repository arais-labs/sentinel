import { messageNotice } from '../lib/message-notice';
import type { Message } from '../types/api';
import { streamingCallKey, type Presentation, type StreamingState, type StreamingToolCall } from './sessionStreaming';

export function presentationOf(value: unknown): Presentation | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const item = value as Partial<Presentation>;
  return typeof item.id === 'string' && typeof item.created_at === 'string'
    ? item as Presentation : undefined;
}

// SQLite timestamps without an offset are UTC, not the browser's local timezone.
export function messageTime(value: string): number {
  const utc = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return Date.parse(utc) || 0;
}

export type ChatRow = { key: string; time: number } & (
  | { kind: 'message'; message: Message }
  | { kind: 'text'; text: string; streaming: boolean }
  | { kind: 'tool'; call: StreamingToolCall; active: boolean }
);

/** One display timeline. Persisted identities replace their live counterparts. */
export function buildChatTimeline(messages: Message[], state: StreamingState): ChatRow[] {
  const rows = new Map<string, ChatRow>();
  const calls = new Map([...state.completedToolCalls, ...state.activeToolCalls].map(call => [streamingCallKey(call), call]));
  const active = new Set(state.activeToolCalls.map(streamingCallKey));
  const fallbackTime = Math.max(0, ...messages.map(m => messageTime(m.created_at))) + 1;
  const representedCalls = new Set<string>();
  for (const [index, item] of state.timeline.entries()) {
    const key = item.presentation?.id ?? item.key;
    const time = item.presentation ? messageTime(item.presentation.created_at) : fallbackTime + index;
    if (item.kind === 'text') {
      rows.set(key, { kind: 'text', key, time, text: item.text, streaming: false });
    } else {
      const call = calls.get(item.callKey);
      representedCalls.add(item.callKey);
      if (call) rows.set(key, { kind: 'tool', key, time, call, active: active.has(item.callKey) });
    }
  }
  for (const [callKey, call] of calls) {
    if (representedCalls.has(callKey)) continue;
    const key = `tool-${callKey}`;
    rows.set(key, { kind: 'tool', key, time: fallbackTime, call, active: active.has(callKey) });
  }
  if (state.text.trim()) {
    const key = state.textPresentation?.id ?? 'streaming-text';
    rows.set(key, { kind: 'text', key, time: state.textPresentation ? messageTime(state.textPresentation.created_at) : fallbackTime + state.timeline.length, text: state.text, streaming: true });
  }
  for (const message of messages) {
    if ((message.role === 'system' && !messageNotice(message)) || (message.role === 'assistant' && !message.content?.trim() && !message.tool_name)) continue;
    const presentation = presentationOf(message.metadata?.presentation);
    const key = presentation?.id ?? message.id;
    const live = rows.get(key);
    const approval = message.metadata?.approval as { pending?: boolean; status?: string } | undefined;
    const savedPending = message.metadata?.pending === true || approval?.pending === true || approval?.status === 'pending';
    // History can still contain a pending approval after its live result arrived.
    if (live?.kind === 'tool' && savedPending) continue;
    rows.set(key, { kind: 'message', key, time: messageTime(presentation?.created_at ?? message.created_at), message });
  }
  return [...rows.values()].sort((a, b) => a.time - b.time);
}
