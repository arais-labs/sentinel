import { createContext } from 'react';

export interface WorkspaceContextValue {
  instanceName: string | undefined;
  workspaceMode: boolean;
}

// Context identities must live outside the page/registry/renderer import graph.
// Dockview retains mounted portals during Fast Refresh; recreating a context
// in a refreshed renderer disconnects consumers from their host provider.
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export const WorkspaceInstanceContext = createContext<string | undefined>(undefined);
export const PaneIdContext = createContext<string | undefined>(undefined);

// Retained panes keep their conversation identity while another conversation is visible.
export const RetainedSessionContext = createContext<{ sessionId: string | null; visible: boolean } | null>(null);
