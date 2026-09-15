import type RFB from '@novnc/novnc';

// noVNC keeps text for deferred ExtendedClipboard requests. Clear that cache
// when sharing stops, and release its tracked modifiers before a paste whose
// clipboard read may finish after the user has released the physical keys.
type ClipboardClient = RFB & {
  _clipboardText: string | null;
  _keyboard: { _allKeysUp(): void };
};

export function shareVncClipboard(
  rfb: RFB,
  host: HTMLElement,
  onError: (message: string) => void,
) {
  const client = rfb as ClipboardClient;
  let enabled = false;
  let disposed = false;
  let revision = 0;
  let lastText: string | undefined;
  const swallowed = new Set<string>();
  const active = () => enabled && !disposed && !rfb.viewOnly &&
    document.visibilityState === 'visible' && document.hasFocus() &&
    host.contains(document.activeElement);
  const fail = () => onError('Clipboard access failed. Check clipboard permissions and try again.');
  const sendText = (text: string) => {
    if (text !== lastText) {
      rfb.clipboardPasteFrom(text);
      lastText = text;
    }
    onError('');
  };
  const shortcut = (key: string, shift: boolean) => {
    client._keyboard._allKeysUp();
    rfb.sendKey(0xffe3, 'ControlLeft', true);
    if (shift) rfb.sendKey(0xffe1, 'ShiftLeft', true);
    rfb.sendKey((shift ? key.toUpperCase() : key).charCodeAt(0), `Key${key.toUpperCase()}`);
    if (shift) rfb.sendKey(0xffe1, 'ShiftLeft', false);
    rfb.sendKey(0xffe3, 'ControlLeft', false);
  };
  const read = async (paste = false, shift = false) => {
    if (!active()) return;
    const token = ++revision;
    try {
      const text = await navigator.clipboard.readText();
      if (!active() || token !== revision) return;
      sendText(text);
      if (paste) shortcut('v', shift);
    } catch {
      if (active() && token === revision) fail();
    }
  };
  const sync = () => { void read(); };
  const invalidate = () => { revision++; };
  const incoming = async (event: Event) => {
    if (!active()) return;
    const text = (event as CustomEvent<{ text: string }>).detail.text;
    revision++;
    lastText = text;
    const token = revision;
    try {
      await navigator.clipboard.writeText(text);
      if (active() && token === revision) onError('');
    } catch {
      if (active() && token === revision) fail();
    }
  };
  const keydown = (event: KeyboardEvent) => {
    // macOS can omit keyup while Command is held. A later ordinary press of
    // that key must still get its matching release through noVNC.
    swallowed.delete(event.code);
    if (!active() || event.altKey) return;
    const key = event.key.toLowerCase();
    const paste = key === 'v' && (event.ctrlKey || event.metaKey) ||
      event.key === 'Insert' && event.shiftKey && !event.ctrlKey && !event.metaKey;
    const macCopy = event.metaKey && (key === 'c' || key === 'x');
    if (!paste && !macCopy) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    swallowed.add(event.code);
    client._keyboard._allKeysUp();
    if (paste) void read(true, event.key !== 'Insert' && event.shiftKey);
    else shortcut(key, event.shiftKey);
  };
  const keyup = (event: KeyboardEvent) => {
    if (!swallowed.delete(event.code)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  const paste = (event: ClipboardEvent) => {
    if (!active() || !event.clipboardData?.types.includes('text/plain')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    revision++;
    sendText(event.clipboardData.getData('text/plain'));
    shortcut('v', false);
  };
  // Electron's native Edit menu may dispatch clipboard events directly,
  // without a keydown reaching the canvas.
  const copy = (event: ClipboardEvent) => {
    if (!active()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    shortcut(event.type === 'cut' ? 'x' : 'c', false);
  };
  host.addEventListener('focusin', sync);
  host.addEventListener('focusout', invalidate);
  host.addEventListener('pointerdown', sync);
  host.addEventListener('keydown', keydown, true);
  host.addEventListener('keyup', keyup, true);
  host.addEventListener('paste', paste, true);
  host.addEventListener('copy', copy, true);
  host.addEventListener('cut', copy, true);
  window.addEventListener('focus', sync);
  window.addEventListener('blur', invalidate);
  rfb.addEventListener('clipboard', incoming);
  return {
    setEnabled(value: boolean) {
      if (enabled === value) return;
      enabled = value;
      revision++;
      lastText = undefined;
      client._clipboardText = null;
      if (enabled) sync();
    },
    dispose() {
      disposed = true;
      revision++;
      client._clipboardText = null;
      host.removeEventListener('focusin', sync);
      host.removeEventListener('focusout', invalidate);
      host.removeEventListener('pointerdown', sync);
      host.removeEventListener('keydown', keydown, true);
      host.removeEventListener('keyup', keyup, true);
      host.removeEventListener('paste', paste, true);
      host.removeEventListener('copy', copy, true);
      host.removeEventListener('cut', copy, true);
      window.removeEventListener('focus', sync);
      window.removeEventListener('blur', invalidate);
      rfb.removeEventListener('clipboard', incoming);
    },
  };
}
