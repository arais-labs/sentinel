import type { ReactNode } from 'react';
import { create } from 'zustand';

/**
 * Pane-actions store
 * ------------------
 * dockview renders a pane's tab ({@link PaneHeaderTab}) and its content
 * (the page wrapped in {@link AppShell}) in separate React trees/portals, so a
 * normal React context provided inside the content cannot reach the tab. This
 * global store bridges that gap: an AppShell-wrapped page registers its header
 * `actions` node under its `paneId`, and the matching {@link PaneHeaderTab}
 * reads it back to render those actions inside the pane header.
 *
 * Values are React nodes (a rendered element). The selector hook does a single
 * `paneId` lookup and returns the stored node reference directly — no array or
 * object derivation — so it stays referentially stable across unrelated store
 * writes and never needs `useShallow`.
 */
interface PaneActionsState {
  /** Maps a pane id to the header actions node that pane's page registered. */
  actions: Record<string, ReactNode>;
}

const usePaneActionsStore = create<PaneActionsState>(() => ({
  actions: {},
}));

/**
 * Register (or replace) the header actions node for a pane. Skips the write
 * when the node reference is unchanged so registering the same actions does not
 * churn the store (and thus the subscribed tab).
 */
export function setPaneActions(paneId: string, node: ReactNode): void {
  const current = usePaneActionsStore.getState().actions;
  if (current[paneId] === node) {
    return;
  }
  usePaneActionsStore.setState({ actions: { ...current, [paneId]: node } });
}

/** Remove a pane's registered actions (call on unmount). No-op if absent. */
export function clearPaneActions(paneId: string): void {
  const current = usePaneActionsStore.getState().actions;
  if (!(paneId in current)) {
    return;
  }
  const next = { ...current };
  delete next[paneId];
  usePaneActionsStore.setState({ actions: next });
}

/**
 * Hook: the actions node registered for `paneId`, or undefined when none. A
 * single map lookup keeps the selector stable — it returns the same node
 * reference until that pane's actions are re-registered.
 */
export function usePaneActions(paneId: string): ReactNode {
  return usePaneActionsStore((state) => state.actions[paneId]);
}
