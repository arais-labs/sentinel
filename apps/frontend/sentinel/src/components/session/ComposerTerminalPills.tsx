import { useMemo } from 'react';
import { Terminal } from 'lucide-react';
import type { ActivePane } from '../../hooks/useSessionRuntimeStream';
import { ComposerActivityPills } from './ComposerActivityPills';

export function ComposerTerminalPills({ panes, focusedPaneId, onOpen, onClose }: {
  panes: ActivePane[];
  focusedPaneId: string | null;
  onOpen: (id: string) => void;
  onClose: (id: string) => void;
}) {
  const entries = useMemo(() => {
    const labels = panes.map(pane => [pane.windowName, pane.title, pane.dead ? 'Exited' : pane.lastCommand || 'Shell'].filter(Boolean).join(' · '));
    return panes.map((pane, index) => {
      const duplicates = labels.map((label, i) => label === labels[index] ? i : -1).filter(i => i >= 0);
      const label = duplicates.length > 1 ? `${labels[index]} · ${duplicates.indexOf(index) + 1}` : labels[index];
      return { id: pane.id, label, busy: pane.busy, title: `${label} (${pane.windowId} / ${pane.id})` };
    });
  }, [panes]);
  return <ComposerActivityPills entries={entries} focusedId={focusedPaneId} onOpen={onOpen} onClose={onClose} icon={Terminal} label="Terminals" />;
}
