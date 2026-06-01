import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

/**
 * Active-session store
 * --------------------
 * Workspace-wide "which session is the user looking at" pointer. SessionsPage
 * owns selection today (URL / local state) and publishes it here via
 * {@link useActiveSessionStore}'s `setActiveSession`. Standalone runtime tabs
 * (Desktop / Terminal / Files panes in the tiling workspace) read it so they
 * know which session's runtime to show without re-implementing selection.
 *
 * Kept intentionally tiny: a single id plus a setter. The persisted value lets a
 * freshly opened runtime tab pick up the last-selected session across reloads;
 * SessionsPage still re-asserts the value on mount so the two never drift.
 */

const STORAGE_KEY = 'sentinel.active-session';

export interface ActiveSessionState {
  /** The workspace-wide selected session id, or null when none is selected. */
  activeSessionId: string | null;
  /** Set (or clear) the workspace-wide selected session. */
  setActiveSession: (sessionId: string | null) => void;
}

export const useActiveSessionStore = create<ActiveSessionState>()(
  persist(
    (set) => ({
      activeSessionId: null,
      setActiveSession: (sessionId) =>
        set((state) => (state.activeSessionId === sessionId ? state : { activeSessionId: sessionId })),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ activeSessionId: state.activeSessionId }),
    },
  ),
);

// --- selectors ---------------------------------------------------------------
// Both return primitives (string | null), so no useShallow is required. Any
// future selector that returns an array/object MUST wrap with useShallow to
// avoid the useSyncExternalStore "getSnapshot should be cached" loop.

/** Hook: the workspace-wide active session id (null when none). */
export function useActiveSessionId(): string | null {
  return useActiveSessionStore((state) => state.activeSessionId);
}

/** Hook: the stable `setActiveSession` action. */
export function useSetActiveSession(): (sessionId: string | null) => void {
  return useActiveSessionStore((state) => state.setActiveSession);
}

/** Non-reactive read for imperative call sites. */
export function getActiveSessionId(): string | null {
  return useActiveSessionStore.getState().activeSessionId;
}
