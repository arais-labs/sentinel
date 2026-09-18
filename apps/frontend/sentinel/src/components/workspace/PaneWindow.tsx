import React, { Component, useEffect, useMemo, type ReactNode } from 'react';
import { PanelLeftClose } from 'lucide-react';
import { WorkspaceProvider } from '../../lib/workspace-context';
import { PaneIdContext, RetainedSessionContext, WorkspaceInstanceContext } from '../../lib/workspace-context-values';
import { getWorkspaceTab, isWorkspaceTabId } from '../../lib/workspace-tabs';
import { usePaneActions } from '../../store/pane-actions-store';
import { useThemeStore } from '../../store/theme-store';
import { PaneActions } from './PaneActions';

class PaneErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error('Pane window failed to render', error); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="p-8 text-sm text-(--text-secondary) space-y-2">
      <p className="text-(--text-primary) font-semibold">This pane failed to render.</p>
      <pre className="whitespace-pre-wrap break-words text-xs">{String(this.state.error.stack || this.state.error.message)}</pre>
    </div>;
  }
}

/** Header subscribes to the pane-actions store; it must not be an ancestor of the tab, or every
 *  re-registration would re-render the tab and loop. */
function PaneWindowHeader({ paneId, label, icon: Icon }: { paneId: string; label: string; icon?: typeof PanelLeftClose }) {
  const actions = usePaneActions(paneId);
  const dock = () => { void window.sentinelDesktop?.dockPaneWindow(); };
  return <header className="pane-window-titlebar sentinel-pane-header">
    <div className="pane-header-title flex items-center gap-1.5">
      {Icon && <Icon size={15} className="shrink-0" />}
      <span>{label}</span>
    </div>
    {actions != null && <div className="pane-header-actions pane-window-actions"><PaneActions>{actions}</PaneActions></div>}
    <button type="button" className="pane-window-dock" onClick={dock} title="Move back into the main window" aria-label="Dock back">
      <PanelLeftClose size={15} />Dock back
    </button>
  </header>;
}

function PaneWindowBody({ paneId, instance, session, component: TabComponent }: { paneId: string; instance: string; session: string | null; component: React.ComponentType }) {
  const retained = useMemo(() => ({ sessionId: session, visible: true }), [session]);
  return <div className="pane-window-content bg-(--app-bg) text-(--text-primary)">
    <PaneIdContext.Provider value={paneId}>
      <WorkspaceInstanceContext.Provider value={instance}>
        <RetainedSessionContext.Provider value={retained}>
          <WorkspaceProvider instanceName={instance} workspaceMode>
            <PaneErrorBoundary><TabComponent /></PaneErrorBoundary>
          </WorkspaceProvider>
        </RetainedSessionContext.Provider>
      </WorkspaceInstanceContext.Provider>
    </PaneIdContext.Provider>
  </div>;
}

/** One workspace tab in its own desktop window. No Dockview, no shell chrome, never writes layouts. */
export function PaneWindow() {
  const params = new URLSearchParams(window.location.search);
  const instance = decodeURIComponent(window.location.pathname.match(/^\/instances\/([^/]+)\/pane$/)?.[1] ?? '');
  const session = params.get('session');
  const rawTab = params.get('tab') ?? '';
  const tabId = isWorkspaceTabId(rawTab) ? rawTab : null;
  const paneId = params.get('pane') || `pane-${rawTab}-window`;
  const tab = tabId ? getWorkspaceTab(tabId) : undefined;
  const initializeTheme = useThemeStore(s => s.initializeTheme);

  useEffect(() => { initializeTheme(); }, [initializeTheme]);
  useEffect(() => {
    document.documentElement.classList.add('pane-window-root');
    document.title = tab ? `${tab.label} — ${instance}` : 'Sentinel';
    return () => document.documentElement.classList.remove('pane-window-root');
  }, [tab, instance]);

  return <div className="pane-window">
    <PaneWindowHeader paneId={paneId} label={tab?.label ?? 'Unknown pane'} icon={tab?.icon} />
    {tab?.component && instance
      ? <PaneWindowBody paneId={paneId} instance={instance} session={session} component={tab.component} />
      : <div className="pane-window-content"><p className="p-8 text-sm text-(--text-secondary)">This pane can't be shown in a separate window.</p></div>}
  </div>;
}
