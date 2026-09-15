import type { NavigateFunction } from 'react-router-dom';
import { useActiveSessionStore } from '../store/active-session-store';
import { useFocusModeStore } from '../store/focus-mode-store';
import { requestWorkspaceTab, sessionLayoutKey } from '../store/workspace-store';
import { instanceRoute } from './routes';
import type { WorkspaceTabId } from './workspace-tabs';

/** Select the destination layout before opening its pane, even across instances. */
export function openWorkspaceTab(
  navigate: NavigateFunction,
  instanceName: string,
  tabId: WorkspaceTabId,
  options: { sessionId?: string; replace?: boolean } = {},
): void {
  const sessions = useActiveSessionStore.getState();
  const sessionId = options.sessionId ?? sessions.byInstance[instanceName] ?? null;
  useFocusModeStore.getState().setPaneId(null, { animate: false });
  // Queue against the destination key before selection remounts Dockview. Its
  // saved layout is restored first, then the requested pane is opened/focused.
  requestWorkspaceTab(sessionLayoutKey(instanceName, sessionId), tabId);
  if (options.sessionId !== undefined) sessions.setActiveSession(instanceName, sessionId);
  navigate(instanceRoute(instanceName, 'workspace'), { replace: options.replace });
}
