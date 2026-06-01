import { Loader2, Terminal } from 'lucide-react';

import { useInstanceName } from '../../lib/workspace-context';
import { useActiveSessionId } from '../../store/active-session-store';
import { useSessionRuntimeStream } from '../../hooks/useSessionRuntimeStream';
import { getTerminalLabel, summarizeCommand } from '../../lib/terminalIdentity';
import { TerminalPreview } from '../session/TerminalPreview';

/**
 * Standalone TERMINAL workspace tab.
 *
 * Hosts multiple terminals for the workspace-wide active session: a strip of
 * terminal pills (label + busy badge + focused selection) when more than one is
 * open, and a single {@link TerminalPreview} for the focused terminal. The
 * terminal list, focus, and create/close lifecycle all come from the shared
 * {@link useSessionRuntimeStream} hook — the same socket SessionsPage uses — so
 * opening this tab alongside the chat view yields ONE WebSocket per session.
 *
 * `desktopViewActive: false` here: this tab never drives the live-view poll or
 * runtime-status fetch (those belong to the Desktop tab), so we don't spin up
 * polling we won't use.
 */
export function TerminalTab() {
  const instanceName = useInstanceName() ?? null;
  const activeSessionId = useActiveSessionId();

  const {
    activeTerminals,
    focusedTerminalId,
    setFocusedTerminalId,
  } = useSessionRuntimeStream(instanceName, activeSessionId, {
    desktopViewActive: false,
  });

  // Effective focus falls back to the first terminal when nothing is explicitly
  // selected — this is the auto-focus parity: the first terminal to open is
  // previewed without a manual click, matching SessionsPage's terminals view.
  const effectiveTerminalId = focusedTerminalId ?? activeTerminals[0]?.id ?? null;

  // Empty state: no session selected anywhere in the workspace.
  if (!activeSessionId) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-6 text-center text-[color:var(--text-muted)] opacity-60">
        <div className="rounded-2xl bg-[color:var(--surface-2)] p-3">
          <Terminal size={24} strokeWidth={1} />
        </div>
        <p className="text-[10px] font-medium uppercase tracking-widest">No session selected</p>
        <p className="max-w-[220px] text-[10px] leading-relaxed">
          Select a session to view its terminals here.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-[color:var(--surface-0)]">
      {/* Terminal strip: only when more than one terminal is open. */}
      {activeTerminals.length > 1 ? (
        <div className="flex items-center gap-1 overflow-x-auto custom-scrollbar border-b border-[color:var(--border-subtle)] px-3 py-2">
          {activeTerminals.map((terminal) => {
            // Label is derived from terminal_id: '0' → "main", auto/bg ids →
            // first command summary, otherwise the agent's chosen name verbatim.
            const display = getTerminalLabel(terminal.id, terminal.lastCommand ?? terminal.label);
            const isFocused = effectiveTerminalId === terminal.id;
            const tooltip = terminal.lastCommand
              ? `${display} — last: ${summarizeCommand(terminal.lastCommand)}`
              : display;
            return (
              <button
                key={terminal.id}
                type="button"
                onClick={() => setFocusedTerminalId(terminal.id)}
                className={`inline-flex h-6 shrink-0 items-center gap-1 rounded-md px-2 text-[10px] font-medium transition-colors ${
                  isFocused
                    ? 'bg-sky-500/20 text-sky-300'
                    : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                }`}
                title={tooltip}
              >
                <span className="max-w-[120px] truncate">{display}</span>
                {terminal.busy ? <Loader2 size={9} className="animate-spin" /> : null}
              </button>
            );
          })}
          <span className="ml-auto shrink-0 rounded-full bg-[color:var(--surface-2)] px-2 py-0.5 text-[10px] font-bold tabular-nums text-[color:var(--text-muted)]">
            {activeTerminals.length}
          </span>
        </div>
      ) : null}

      {/* Focused terminal preview, or empty state when none are open. */}
      <div className="min-h-0 flex-1">
        {effectiveTerminalId ? (
          // Keyed on the (session, terminal) pair so React tears down and
          // re-creates the xterm + WS when the user switches terminals; sharing
          // state across terminals would mix scrollback.
          <TerminalPreview
            key={`${activeSessionId}:${effectiveTerminalId}`}
            sessionId={activeSessionId}
            terminalId={effectiveTerminalId}
            instanceName={instanceName ?? ''}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-[color:var(--text-muted)] opacity-60">
            <div className="rounded-2xl bg-[color:var(--surface-2)] p-3">
              <Terminal size={24} strokeWidth={1} />
            </div>
            <p className="text-[10px] font-medium uppercase tracking-widest">No terminals open</p>
            <p className="max-w-[220px] text-[10px] leading-relaxed">
              The agent will open one here when it runs a shell command.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
