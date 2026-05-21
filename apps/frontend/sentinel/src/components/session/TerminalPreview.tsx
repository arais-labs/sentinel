import { useEffect, useRef, useState } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { Terminal as XTerm } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';

import { wsSessionsBaseUrl } from '../../lib/env';

interface TerminalPreviewProps {
  sessionId: string;
  terminalId: string;
  instanceName: string;
}

export function TerminalPreview({ sessionId, terminalId, instanceName }: TerminalPreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'closed' | 'error'>('connecting');

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !instanceName || !sessionId || !terminalId) return undefined;

    setStatus('connecting');

    const terminal = new XTerm({
      cursorBlink: true,
      convertEol: true,
      fontFamily: 'JetBrains Mono, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.25,
      scrollback: 5000,
      theme: {
        background: '#050608',
        foreground: '#d6deeb',
        cursor: '#f8fafc',
        selectionBackground: '#334155',
        black: '#1f2937',
        red: '#f87171',
        green: '#34d399',
        yellow: '#fbbf24',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e5e7eb',
        brightBlack: '#64748b',
        brightRed: '#fb7185',
        brightGreen: '#4ade80',
        brightYellow: '#fde047',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#ffffff',
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.loadAddon(new WebLinksAddon());
    terminal.open(container);
    terminal.focus();
    fit.fit();

    terminalRef.current = terminal;
    fitRef.current = fit;

    const ws = new WebSocket(
      `${wsSessionsBaseUrl(instanceName)}/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}`,
    );
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    const sendResize = () => {
      if (ws.readyState !== WebSocket.OPEN || !terminal.cols || !terminal.rows) return;
      ws.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
    };

    const resizeObserver = new ResizeObserver(() => {
      fit.fit();
      sendResize();
    });
    resizeObserver.observe(container);

    const dataDisposable = terminal.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    ws.onopen = () => {
      setStatus('connected');
      sendResize();
    };
    ws.onmessage = async (event) => {
      if (typeof event.data === 'string') {
        terminal.write(event.data);
        return;
      }
      const buffer = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
      terminal.write(new Uint8Array(buffer));
    };
    ws.onerror = () => setStatus('error');
    ws.onclose = () => setStatus((current) => (current === 'error' ? 'error' : 'closed'));

    return () => {
      resizeObserver.disconnect();
      dataDisposable.dispose();
      ws.close();
      terminal.dispose();
      wsRef.current = null;
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [instanceName, sessionId, terminalId]);

  return (
    <div className="relative h-full min-h-[260px] w-full overflow-hidden bg-[#050608]">
      <div ref={containerRef} className="h-full w-full p-2" />
      {status !== 'connected' ? (
        <div className="pointer-events-none absolute right-3 top-3 rounded-md border border-white/10 bg-black/70 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-white/60">
          {status}
        </div>
      ) : null}
    </div>
  );
}
