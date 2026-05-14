import { memo, useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';

import { wsSessionsBaseUrl } from '../../lib/env';

interface TerminalPreviewProps {
  sessionId: string;
  terminalId: string;
}

// Higher-contrast ANSI palette than xterm's defaults so `ls --color=auto`,
// grep highlights, git diff output etc. render with real punch on the dark
// Sentinel surface. Bright variants are visibly more saturated than their
// non-bright twins so semantic distinctions stay readable at small font sizes.
const TERMINAL_THEME = {
  background: '#0a0a0c',
  foreground: '#f3f4f6',
  cursor: '#38bdf8',
  cursorAccent: '#0a0a0c',
  selectionBackground: 'rgba(56, 189, 248, 0.35)',
  black: '#1f2128',
  red: '#ef4444',
  green: '#22c55e',
  yellow: '#eab308',
  blue: '#3b82f6',
  magenta: '#d946ef',
  cyan: '#06b6d4',
  white: '#e5e7eb',
  brightBlack: '#6b7280',
  brightRed: '#f87171',
  brightGreen: '#4ade80',
  brightYellow: '#facc15',
  brightBlue: '#60a5fa',
  brightMagenta: '#e879f9',
  brightCyan: '#22d3ee',
  brightWhite: '#ffffff',
};

export const TerminalPreview = memo(({ sessionId, terminalId }: TerminalPreviewProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Terminal + WebSocket live across renders for a stable (sessionId, terminalId)
  // pair; switching either tears down and rebuilds via the effect's cleanup.
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      theme: TERMINAL_THEME,
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Courier New", monospace',
      fontSize: 13,
      lineHeight: 1.2,
      scrollback: 5000,
      allowProposedApi: true,
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(container);

    // fitAddon.fit() reads container dimensions; if the parent hasn't been
    // laid out yet (flex item with min-h-0), it touches `undefined.dimensions`
    // and crashes the mount. Defer until dimensions are positive, and retry
    // every ResizeObserver tick. Safe to skip the call entirely while size is 0.
    const safeFit = () => {
      const rect = container.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      try {
        fitAddon.fit();
      } catch {
        // Swallow: usually means xterm internals haven't finished initializing
        // for this dimension change; the next ResizeObserver tick retries.
      }
    };
    // First attempt deferred to next frame so React can flush layout.
    requestAnimationFrame(safeFit);

    termRef.current = term;
    fitAddonRef.current = fitAddon;

    const wsUrl = `${wsSessionsBaseUrl()}/${sessionId}/terminals/${terminalId}`;
    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    const decoder = new TextDecoder('utf-8');
    ws.addEventListener('message', (event) => {
      if (event.data instanceof ArrayBuffer) {
        term.write(decoder.decode(event.data));
      } else if (typeof event.data === 'string') {
        // The backend pushes pane bytes as binary frames, but some servers
        // (or future event channels) might use text; tolerate both.
        term.write(event.data);
      }
    });

    // Send a resize control frame on open + on subsequent xterm resizes so
    // the guest tmux pane matches the viewport — otherwise `top`, `vim`, etc.
    // render at the wrong dimensions.
    const sendResize = () => {
      if (ws.readyState !== WebSocket.OPEN) return;
      try {
        ws.send(
          JSON.stringify({
            type: 'resize',
            cols: term.cols,
            rows: term.rows,
          }),
        );
      } catch {
        // No-op: socket may have closed between checks.
      }
    };
    ws.addEventListener('open', sendResize);
    const resizeDisposable = term.onResize(sendResize);

    // Forward keystrokes (including raw escape sequences for arrows, Ctrl-C,
    // etc.). The backend parses these and translates to tmux send-keys.
    const dataDisposable = term.onData((data: string) => {
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      ws.send(data);
    });

    let resizeFrame: number | null = null;
    const handleWindowResize = () => {
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(safeFit);
    };
    window.addEventListener('resize', handleWindowResize);
    const observer = new ResizeObserver(handleWindowResize);
    observer.observe(container);

    return () => {
      window.removeEventListener('resize', handleWindowResize);
      observer.disconnect();
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
      resizeDisposable.dispose();
      dataDisposable.dispose();
      try {
        ws.close();
      } catch {
        // Best-effort.
      }
      wsRef.current = null;
      try {
        term.dispose();
      } catch {
        // Best-effort.
      }
      // xterm's dispose() leaves the xterm wrapper div behind. In React 18
      // StrictMode the effect runs setup → cleanup → setup, and if we don't
      // explicitly clear the container the second setup grafts a new xterm
      // alongside the dead one — visible cursor on the corpse, live xterm
      // hidden / unfocused, onData never fires. Wipe the container so the
      // next mount starts from a clean DOM slate.
      const root = containerRef.current;
      if (root) {
        while (root.firstChild) root.removeChild(root.firstChild);
      }
      termRef.current = null;
      fitAddonRef.current = null;
    };
  }, [sessionId, terminalId]);

  return (
    <div className="flex flex-col h-full w-full bg-[color:var(--surface-0)]">
      <div ref={containerRef} className="flex-1 w-full overflow-hidden p-2" />
    </div>
  );
});

TerminalPreview.displayName = 'TerminalPreview';

export default TerminalPreview;
