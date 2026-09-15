import type { Message } from '../types/api';

export type MessageNotice = { title: string; body?: string };

/** Presentation is explicitly assigned by message producers, never inferred from text or role. */
export function messageNotice(message: Message): MessageNotice | null {
  const notice = message.metadata?.notice;
  if (!notice || typeof notice !== 'object') return null;
  const value = notice as Record<string, unknown>;
  if (typeof value.title !== 'string' || !value.title.trim()) return null;
  return { title: value.title, ...(typeof value.body === 'string' ? { body: value.body } : {}) };
}
