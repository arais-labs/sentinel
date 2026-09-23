import { api } from './api';
import type { attachDesktopInput } from './desktop-video-input';

/** Explicit user clipboard gestures only: no polling or hidden-pane sharing. */
export function shareDesktopClipboard(host: HTMLElement, input: ReturnType<typeof attachDesktopInput>,
  url: string, onError: (message: string) => void) {
  const abort = new AbortController();
  let enabled = false, disposed = false, revision = 0;
  const swallowed = new Set<string>();
  const copying = new Set<string>();
  const active = () => enabled && !disposed && document.visibilityState === 'visible' && document.hasFocus()
    && host.contains(document.activeElement);
  const valid = (token: number) => active() && token === revision;
  const fail = () => onError('Clipboard access failed. Check clipboard permissions and try again.');
  const receive = async () => {
    if (!active()) return;
    const token = ++revision;
    try {
      const { text } = await api.post<{ text: string }>(url, { action: 'read' }, { signal: abort.signal });
      if (!valid(token)) return;
      await navigator.clipboard.writeText(text);
      if (valid(token)) onError('');
    } catch { if (valid(token)) fail(); }
  };
  const paste = async (shift: boolean, supplied?: string) => {
    if (!active()) return;
    const token = ++revision;
    input.release();
    try {
      const text = supplied ?? await navigator.clipboard.readText();
      if (!valid(token)) return;
      if (new TextEncoder().encode(text).length > 262144) throw new Error('Clipboard too large');
      await api.post(url, { action: 'write', text }, { signal: abort.signal });
      if (!valid(token)) return;
      input.shortcut('v', shift);
      onError('');
    } catch { if (valid(token)) fail(); }
  };
  const options = { capture: true, signal: abort.signal };
  host.addEventListener('keydown', event => {
    const key = event as KeyboardEvent;
    swallowed.delete(key.code);
    if (!active() || key.altKey) return;
    const letter = key.key.toLowerCase();
    const isPaste = letter === 'v' && (key.ctrlKey || key.metaKey) || key.key === 'Insert' && key.shiftKey;
    const isCopy = (letter === 'c' || letter === 'x') && (key.ctrlKey || key.metaKey);
    if (!isPaste && !isCopy) return;
    key.preventDefault(); key.stopImmediatePropagation();
    swallowed.add(key.code);
    if (isPaste) void paste(key.key !== 'Insert' && key.shiftKey);
    else { input.shortcut(letter as 'c' | 'x', key.shiftKey); copying.add(key.code); }
  }, options);
  host.addEventListener('keyup', event => {
    const key = event as KeyboardEvent;
    // macOS may omit the letter's keyup while Command remains held.
    if (key.key === 'Meta' && copying.size) {
      for (const code of copying) swallowed.delete(code);
      copying.clear();
      void receive();
    }
    if (!swallowed.delete(key.code)) return;
    key.preventDefault(); key.stopImmediatePropagation();
    // Read on completion of the copy gesture, not on every animation frame.
    if (copying.delete(key.code)) void receive();
  }, options);
  host.addEventListener('paste', event => {
    const clip = event as ClipboardEvent;
    if (!active() || !clip.clipboardData?.types.includes('text/plain')) return;
    clip.preventDefault(); clip.stopImmediatePropagation();
    void paste(false, clip.clipboardData.getData('text/plain'));
  }, options);
  for (const kind of ['copy', 'cut']) host.addEventListener(kind, event => {
    if (!active()) return;
    event.preventDefault(); event.stopImmediatePropagation();
    input.shortcut(kind === 'copy' ? 'c' : 'x');
    void receive();
  }, options);
  const invalidate = () => { revision++; swallowed.clear(); copying.clear(); };
  host.addEventListener('focusout', invalidate, { signal: abort.signal });
  window.addEventListener('blur', invalidate, { signal: abort.signal });
  return {
    setEnabled(value: boolean) { enabled = value; invalidate(); },
    dispose() { disposed = true; invalidate(); abort.abort(); },
  };
}
