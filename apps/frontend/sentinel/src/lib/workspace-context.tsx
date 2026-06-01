import { createContext, useContext, PropsWithChildren } from 'react';
import { useParams } from 'react-router-dom';

export interface WorkspaceContextValue {
  /** The instance the surrounding pane is scoped to. */
  instanceName: string | undefined;
  /** True when this subtree is rendered inside a tiling workspace pane. */
  workspaceMode: boolean;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export interface WorkspaceProviderProps extends PropsWithChildren {
  instanceName: string | undefined;
  workspaceMode?: boolean;
}

/**
 * Provides the instance scope to page components rendered inside a workspace
 * pane. Panes are not the active route, so they cannot rely on `useParams`.
 */
export function WorkspaceProvider({
  instanceName,
  workspaceMode = true,
  children,
}: WorkspaceProviderProps) {
  return (
    <WorkspaceContext.Provider value={{ instanceName, workspaceMode }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

/** Raw context access; null when rendered outside a workspace pane. */
export function useWorkspaceContext(): WorkspaceContextValue | null {
  return useContext(WorkspaceContext);
}

/**
 * Returns the instance name from the surrounding workspace pane when present,
 * otherwise falls back to the active route's `:instanceName` param. Page
 * components should use this instead of reading `useParams` directly so they
 * render correctly both as a route and inside a pane.
 */
export function useInstanceName(): string | undefined {
  const ctx = useContext(WorkspaceContext);
  const params = useParams<{ instanceName?: string }>();
  if (ctx) {
    return ctx.instanceName;
  }
  return params.instanceName;
}

/** True when the current subtree is rendered inside a tiling workspace pane. */
export function useWorkspaceMode(): boolean {
  const ctx = useContext(WorkspaceContext);
  return ctx?.workspaceMode ?? false;
}
