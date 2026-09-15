import { useSessionWorkspace } from '../../hooks/useSessionWorkspace';
import { Terminal } from 'lucide-react';

import { useInstanceName, usePaneId } from '../../lib/workspace-context';
import { useActiveSessionId } from '../../store/active-session-store';
import { useSessionRuntimeStream } from '../../hooks/useSessionRuntimeStream';
import { TerminalPreview } from '../session/TerminalPreview';

/** One native tmux session for the active conversation. */
export function TerminalTab() {
  const instanceName = useInstanceName() ?? null;
  const activeSessionId = useActiveSessionId();
  const headerPaneId = usePaneId();
  const { workspace, loading } = useSessionWorkspace(activeSessionId, instanceName);

  const {
    focusedPaneId,
  } = useSessionRuntimeStream(instanceName, activeSessionId, {
  });

  // Effective focus falls back to the first terminal when nothing is explicitly
  // selected — this is the auto-focus parity: the first terminal to open is
  // previewed without a manual click, matching SessionsPage's terminals view.
  const selectedPaneId = focusedPaneId;

  // Empty state: no session selected anywhere in the workspace.
  if (!activeSessionId) {
    return (
      <div className="terminal-empty flex h-full w-full flex-col items-center justify-center gap-3 px-6 text-center text-(--text-muted)">
        <div className="terminal-empty-icon rounded-2xl bg-(--surface-2) p-3">
          <Terminal size={24} strokeWidth={1} />
        </div>
        <p className="text-[10px] font-medium uppercase tracking-widest">No session selected</p>
        <p className="max-w-[220px] text-[10px] leading-relaxed">
          Select a session to view its terminals here.
        </p>
      </div>
    );
  }

  if (loading || !workspace) return <div className="terminal-empty flex h-full items-center justify-center p-6 text-center text-sm text-(--text-secondary)">{loading ? 'Loading workspace…' : 'Attach a workspace from the session toolbar to use the terminal.'}</div>;

  return (
    <TerminalPreview key={workspace.id}
      headerPaneId={headerPaneId}
      sessionId={activeSessionId}
      paneId={selectedPaneId}
      instanceName={instanceName ?? ''}
    />
  );
}
