import { useContext } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';

import { PaneIdContext, WorkspaceInstanceContext } from '../../lib/workspace-context-values';
import { WorkspaceProvider } from '../../lib/workspace-context';
import {
  getWorkspaceTab,
  isWorkspaceTabId,
  type WorkspaceTabId,
} from '../../lib/workspace-tabs';
import type { WorkspacePaneParams } from '../../store/workspace-store';
import { TabPicker } from './PaneHeaderTab';

/** Read the current `tabId` off the panel params, validated against the registry. */
function readTabId(params: Partial<WorkspacePaneParams> | undefined): WorkspaceTabId | null {
  const raw = params?.tabId;
  if (typeof raw === 'string' && isWorkspaceTabId(raw)) {
    return raw;
  }
  return null;
}

/**
 * Renderer for a single dockview panel's *content*. The pane's header bar (view
 * label + view switcher + split + close) lives in the custom dockview tab
 * ({@link PaneHeaderTab}) so the header is draggable to reposition the pane;
 * this component renders only the scrollable view body.
 *
 * dockview re-invokes this with fresh `props.params` whenever `updateParameters`
 * runs, so reading `props.params.tabId` reflects tab switches without extra
 * state. An empty pane shows a centered picker prompting the user to pick a tab.
 */
export function WorkspacePane(props: IDockviewPanelProps<WorkspacePaneParams>) {
  const paneId = props.api.id;
  const tabId = readTabId(props.params);
  const instanceName = useContext(WorkspaceInstanceContext);

  const tab = tabId ? getWorkspaceTab(tabId) : undefined;
  const TabComponent = tab?.component;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-(--surface-0) text-(--text-primary)">
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-(--app-bg)">
        {TabComponent ? (
          <PaneIdContext.Provider value={paneId}>
            <WorkspaceProvider instanceName={instanceName} workspaceMode>
              <TabComponent />
            </WorkspaceProvider>
          </PaneIdContext.Provider>
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-8 text-center">
            <p className="text-sm text-(--text-secondary)">
              This pane is empty. Choose a tab to display here.
            </p>
            <TabPicker paneId={paneId} activeTabId={null} variant="empty" />
          </div>
        )}
      </div>
    </div>
  );
}
