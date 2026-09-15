import { API_BASE_URL } from '../../lib/env';

export type ReviewEvent = { event: string; data?: unknown; label?: string; step?: number; total?: number; message?: string; account_id?: string };
export async function streamReview(instance: string, action: 'load' | 'submit' | 'checks', body: unknown, onEvent: (event: ReviewEvent) => void, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE_URL}/instances/${encodeURIComponent(instance)}/git/reviews/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === 'string' ? error.detail : `Review request failed (${response.status})`);
  }
  if (!response.body) throw new Error('No review stream received');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', complete = false;
  const consume = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as ReviewEvent;
    if (event.event === 'error') throw new Error(event.message ?? 'Review request failed');
    if (event.event === 'complete' || event.event === 'submitted') complete = true;
    onEvent(event);
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n'); buffer = lines.pop() ?? '';
      lines.forEach(consume);
      if (done) break;
    }
    consume(buffer);
    if (!complete) throw new Error(action === 'submit' ? 'Submission status unknown. Check GitHub before retrying.' : 'The connection ended before loading completed. Refresh to retry.');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
