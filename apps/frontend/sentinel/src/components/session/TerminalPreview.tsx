import { RetainedSessionContext } from '../../lib/workspace-context-values';
import { DesktopSocket } from '../../lib/desktop-socket';
import { useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Plus, Columns2, Rows2, X, RotateCcw, Loader2 } from 'lucide-react';
import { notificationPublisher } from '../../lib/notifications';
import { useThemeStore } from '../../store/theme-store';
import { wsSessionsBaseUrl } from '../../lib/env';
import { PaneWriter, type Pane, type PaneMessage, type TerminalWindow } from './terminal-view';
import { loadTerminalCore, TerminalRenderer } from './terminal-renderer';

import { terminalTheme } from './terminal-theme';
import './terminal-chrome.css';
import { TerminalWindowTabs } from './TerminalWindowTabs';
import { PaneActions } from '../workspace/PaneActions';
import { clearPaneActions, setPaneActions } from '../../store/pane-actions-store';

const notify = notificationPublisher('Terminal');

const SCROLLBAR = 6;
const buttonClass = 'terminal-control chat-header-pill inline-flex h-7 items-center gap-2 rounded-md px-2 text-xs hover:bg-(--surface-2) hover:text-(--text-primary) disabled:opacity-40';
type Send = (message: Record<string, unknown>) => void;
type Register = (id: string, writer: PaneWriter | null) => void;

function TerminalPane({ pane, register, send, measure, selected, connected, visible }: {
  pane: Pane; register: Register; send: Send; measure: (width: number, height: number) => void;
  selected: boolean; connected: boolean; visible: boolean;
}) {
  const conversationVisible = useContext(RetainedSessionContext)?.visible ?? true;
  visible = visible && conversationVisible;
  const container = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<TerminalRenderer | null>(null);
  const latest = useRef({ pane, selected, connected, visible });
  latest.current = { pane, selected, connected, visible };
  const [opened, setOpened] = useState(visible);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const theme = useThemeStore(state => state.theme);
  useEffect(() => { if (visible) setOpened(true); }, [visible]);
  useEffect(() => {
    const containerElement = container.current;
    if (!containerElement || !opened) return;
    let disposed = false;
    let terminal: TerminalRenderer | undefined;
    let writer: PaneWriter | undefined;
    let observer: ResizeObserver | undefined;
    const element = document.createElement('div');
    element.className = 'sentinel-terminal';
    containerElement.appendChild(element);
    setError('');
    const input = (data: string) => {
      if (disposed || !latest.current.connected || latest.current.pane.dead) return;
      const bytes = new TextEncoder().encode(data);
      for (let offset = 0; offset < bytes.length; offset += 8192) {
        const chunk = bytes.subarray(offset, offset + 8192);
        send({ type: 'input', pane_id: pane.pane_id, data: btoa(Array.from(chunk, b => String.fromCharCode(b)).join('')) });
      }
    };
    const copy = (text: string) => {
      void navigator.clipboard.writeText(text).catch(() => notify.error('Could not copy terminal text'));
    };
    const keydown = (event: KeyboardEvent) => {
      const shortcut = (event.metaKey && !event.ctrlKey) || (event.ctrlKey && event.shiftKey);
      if (!shortcut || event.altKey) return;
      const selection = window.getSelection();
      if (event.key.toLowerCase() === 'c' && selection?.toString() && element.contains(selection.anchorNode)) {
        event.preventDefault(); event.stopImmediatePropagation(); copy(selection.toString());
      } else if (event.key.toLowerCase() === 'v' && event.ctrlKey) {
        event.preventDefault(); event.stopImmediatePropagation();
        void navigator.clipboard.readText().then(text => { if (!disposed) terminal?.paste(text); })
          .catch(() => notify.error('Could not paste clipboard text'));
      }
    };
    const paste = (event: ClipboardEvent) => {
      if (!event.clipboardData) return;
      event.preventDefault(); event.stopImmediatePropagation();
      terminal?.paste(event.clipboardData.getData('text/plain'));
      terminal?.focus();
    };
    element.addEventListener('keydown', keydown, true);
    element.addEventListener('paste', paste, true);
    void (async () => {
      await document.fonts.load('12px "JetBrains Mono"');
      if (disposed) return;
      const core = await loadTerminalCore();
      if (disposed) { core.dispose(); return; }
      const current = latest.current;
      terminal = new TerminalRenderer(element, core, current.pane.width, current.pane.height, input);
      const previousFocus = document.activeElement;
      await terminal.init();
      if (disposed) return;
      terminalRef.current = terminal;
      terminal.setVisible(latest.current.visible);
      if (!latest.current.selected || !latest.current.visible) {
        if (previousFocus instanceof HTMLElement) previousFocus.focus({ preventScroll: true });
      }
      writer = new PaneWriter(terminal);
      observer = new ResizeObserver(() => {
        if (!element.clientWidth || !element.clientHeight) return;
        const probe = document.createElement('span');
        probe.textContent = 'MMMMMMMMMM';
        probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
        element.appendChild(probe);
        const width = probe.getBoundingClientRect().width / 10;
        probe.remove();
        const height = parseFloat(getComputedStyle(element).getPropertyValue('--term-row-height'));
        if (width && height) measure(width, height);
      });
      observer.observe(element);
      register(pane.pane_id, writer);
    })().catch(cause => {
      terminal?.dispose(); terminal = undefined;
      if (!disposed) setError(cause instanceof Error ? cause.message : 'Could not load terminal');
    });
    return () => {
      disposed = true; observer?.disconnect();
      if (writer) register(pane.pane_id, null);
      element.removeEventListener('keydown', keydown, true);
      element.removeEventListener('paste', paste, true);
      writer?.dispose(); terminal?.dispose(); terminalRef.current = null;
      element.remove();
    };
    // Keep initialized panes alive across window switches to retain native scrollback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pane.pane_id, opened, attempt, register, send, measure]);
  useLayoutEffect(() => { terminalRef.current?.setVisible(visible); }, [visible]);
  useEffect(() => { terminalRef.current?.setTheme(terminalTheme()); }, [theme]);
  useEffect(() => { if (selected && visible) terminalRef.current?.focus(); }, [selected, visible]);
  return <div ref={container} className="terminal-shell relative h-full w-full" onFocusCapture={() => {
    if (!latest.current.selected && latest.current.visible) send({ type: 'select_pane', pane_id: pane.pane_id });
  }}>
    <button type="button" className="terminal-shell-close" disabled={!connected}
      aria-label={`Close ${pane.title || pane.command || 'shell'} pane`} title="Close this terminal pane"
      onPointerDown={event => event.stopPropagation()}
      onClick={event => { event.stopPropagation(); send({ type: 'close_pane', pane_id: pane.pane_id }); }}>
      <X size={13} aria-hidden="true" />
    </button>
    {error && <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-(--surface-0) px-4 text-center text-sm text-(--text-secondary)">
      <span>{error}</span><button type="button" className={buttonClass} onClick={() => setAttempt(value => value + 1)}>Retry terminal</button>
    </div>}
  </div>;
}

interface TerminalPreviewProps { sessionId: string; paneId?: string | null; instanceName: string; headerPaneId?: string }
export function TerminalPreview({ sessionId, paneId, instanceName, headerPaneId }: TerminalPreviewProps) {
  const root = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const terminals = useRef(new Map<string, PaneWriter>());
  const pending = useRef(new Map<string, PaneMessage[]>());
  const requestedPane = useRef(paneId); requestedPane.current = paneId;
  const retrySetup = useRef(false);
  const [attempt, setAttempt] = useState(0);
  const [windows, setWindows] = useState<TerminalWindow[]>([]);
  const [activeWindow, setActiveWindow] = useState('');
  const [status, setStatus] = useState<'connecting' | 'connected' | 'closed' | 'error' | 'empty'>('connecting');
  const [error, setError] = useState('');
  const [cell, setCell] = useState({ width: 7.2, height: 15 });
  const measure = useCallback((width: number, height: number) => {
    setCell(old => Math.abs(old.width - width) < 0.01 && Math.abs(old.height - height) < 0.01 ? old : { width, height });
  }, []);
  const send = useCallback<Send>(message => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(message));
  }, []);
  const register = useCallback<Register>((id, terminal) => {
    if (!terminal) { terminals.current.delete(id); return; }
    terminals.current.set(id, terminal);
    for (const message of pending.current.get(id) || []) terminal.push(message);
    pending.current.delete(id);
  }, []);

  useEffect(() => {
    setStatus('connecting'); setError(''); setWindows([]); pending.current.clear(); terminals.current.clear();
    let disposed = false, empty = false;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const ws = new DesktopSocket(`${wsSessionsBaseUrl(instanceName)}/${encodeURIComponent(sessionId)}/terminal${retrySetup.current ? '?retry=true' : ''}`);
    retrySetup.current = false; wsRef.current = ws;
    ws.onmessage = event => {
      if (disposed || typeof event.data !== 'string') return;
      const message = JSON.parse(event.data);
      if (message.type === 'terminal_layout') {
        const layout: TerminalWindow[] = message.windows;
        empty = !layout.length;
        for (const window of layout) for (const pane of window.panes) terminals.current.get(pane.pane_id)?.resize(pane.width, pane.height);
        setWindows(layout); setStatus(empty ? 'empty' : 'connected');
        setActiveWindow(current => layout.some(w => w.window_id === current) ? current :
          (layout.find(w => w.panes.some(p => p.pane_id === requestedPane.current)) || layout[0])?.window_id || '');
        const ids = new Set(layout.flatMap(w => w.panes.map(p => p.pane_id)));
        for (const id of pending.current.keys()) if (!ids.has(id)) pending.current.delete(id);
      } else if (message.type === 'pane_output' || message.type === 'pane_snapshot' || message.type === 'pane_resize') {
        const id = message.type === 'pane_snapshot' ? message.pane.pane_id : message.pane_id;
        const terminal = terminals.current.get(id);
        if (terminal) terminal.push(message);
        else {
          const queue = message.type === 'pane_snapshot' ? [] : pending.current.get(id) || [];
          queue.push(message); pending.current.set(id, queue);
        }
      } else if (message.type === 'terminal_error') {
        setError(message.message || 'Workspace terminal unavailable'); setStatus('error');
      } else if (message.type === 'terminal_action_error') notify.error(message.message);
    };
    ws.onerror = () => setStatus('error');
    ws.onclose = event => {
      if (disposed || empty) return;
      setStatus(current => current === 'error' ? current : 'closed');
      if (![1000, 4004, 4005].includes(event.code)) reconnect = setTimeout(() => setAttempt(value => value + 1), 1000);
    };
    return () => { disposed = true; clearTimeout(reconnect); ws.close(); wsRef.current = null; };
  }, [instanceName, sessionId, attempt]);

  useEffect(() => {
    if (!paneId) return;
    const window = windows.find(w => w.panes.some(p => p.pane_id === paneId));
    if (window) { setActiveWindow(window.window_id); send({ type: 'select_pane', pane_id: paneId }); }
    // Respond to external pane selection once; subsequent user window switches stay local.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneId, send]);

  const current = windows.find(w => w.window_id === activeWindow);
  const selected = current?.panes.find(p => p.active) || current?.panes[0];
  useEffect(() => {
    const element = root.current;
    if (!element || !current || status !== 'connected') return;
    let timer: ReturnType<typeof setTimeout>;
    let previous = '';
    const resize = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const box = element.getBoundingClientRect();
        if (box.width < 20 || box.height < 20) return;
        const cols = Math.max(20, Math.floor((box.width - SCROLLBAR) / cell.width));
        const rows = Math.max(5, Math.floor(box.height / cell.height));
        const key = `${cols}:${rows}`;
        if (key !== previous) { previous = key; send({ type: 'resize', window_id: current.window_id, cols, rows }); }
      }, 100);
    };
    const observer = new ResizeObserver(resize); observer.observe(element); resize();
    return () => { clearTimeout(timer); observer.disconnect(); };
  }, [current?.window_id, cell, send, status]);

  const controls = <>
    <button type="button" className={buttonClass} title="New window" aria-label="New window" disabled={status !== 'connected'} onClick={() => send({ type: 'new_window' })}><Plus size={15} /><span>New</span></button>
    <button type="button" className={buttonClass} title="Split side by side" aria-label="Split side by side" disabled={!selected || status !== 'connected'} onClick={() => send({ type: 'split', pane_id: selected?.pane_id, direction: 'horizontal' })}><Columns2 size={15} /><span>Split right</span></button>
    <button type="button" className={buttonClass} title="Split above and below" aria-label="Split above and below" disabled={!selected || status !== 'connected'} onClick={() => send({ type: 'split', pane_id: selected?.pane_id, direction: 'vertical' })}><Rows2 size={15} /><span>Split down</span></button>
    {selected?.dead && <button type="button" className={buttonClass} onClick={() => send({ type: 'restart_pane', pane_id: selected.pane_id })} title="Restart shell" aria-label="Restart shell"><RotateCcw size={13} /><span>Restart</span></button>}
  </>;

  // Only the dedicated Terminal pane owns its header. Chat embeds keep their
  // controls local so they cannot replace the conversation's header actions.
  useEffect(() => {
    if (headerPaneId) setPaneActions(headerPaneId, controls);
  }, [headerPaneId, controls]);
  useEffect(() => {
    if (headerPaneId) return () => clearPaneActions(headerPaneId);
  }, [headerPaneId]);

  return <div className="terminal-preview relative flex h-full min-h-0 w-full flex-col overflow-hidden bg-(--surface-0)">
    {!headerPaneId && <div className="terminal-toolbar sentinel-pane-header flex shrink-0 items-center gap-1 border-b border-(--border-subtle) px-2 py-2 text-(--text-secondary)"><PaneActions>{controls}</PaneActions></div>}
    {windows.length > 0 && <TerminalWindowTabs windows={windows} selected={activeWindow} onSelect={setActiveWindow} />}
    <div className="min-h-0 flex-1 overflow-hidden px-2 pt-6 pb-2"><div ref={root} className="relative h-full w-full">
      {windows.map(window => <div key={`${instanceName}:${sessionId}:${attempt}:${window.window_id}`} className="absolute inset-0" style={{ display: window.window_id === activeWindow ? 'block' : 'none' }}>
        {window.panes.map(pane => <div key={pane.pane_id} className="absolute"
          style={{ left: pane.left * cell.width, top: pane.top * cell.height, width: pane.width * cell.width + SCROLLBAR, height: pane.height * cell.height }}>
          <TerminalPane pane={pane} register={register} send={send} measure={measure} selected={pane.pane_id === selected?.pane_id}
            visible={window.window_id === activeWindow} connected={status === 'connected'} />
        </div>)}
      </div>)}
    </div></div>
    {status !== 'connected' && <div className={`terminal-connection-state absolute inset-0 flex flex-col items-center justify-center bg-(--surface-0)/95 text-center text-sm text-(--text-secondary) ${status === 'connecting' ? 'gap-4 p-8' : 'gap-3 px-6'}`}>
      {status === 'connecting' ? <div role="status" className="flex flex-col items-center gap-4">
        <Loader2 size={25} className="animate-spin" aria-hidden="true" />
        <h3 className="text-base font-medium text-(--text-primary)">Starting terminal</h3>
      </div> : <span>{status === 'empty' ? 'Terminal closed' : error || 'Terminal disconnected'}</span>}
      {status !== 'connecting' && <button type="button" className="terminal-reconnect rounded-md border border-(--border-strong) px-4 py-2 text-(--text-primary)" onClick={() => { retrySetup.current = true; setAttempt(value => value + 1); }}>{status === 'empty' ? 'Open terminal' : 'Reconnect'}</button>}
    </div>}
  </div>;
}
