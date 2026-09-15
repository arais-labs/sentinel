import { create } from 'zustand';

/**
 * Which pane is focused, per session. Shared so every surface bound to the
 * same session — the SessionsPage pills and the standalone Terminal tab — agrees
 * on the focused pane: clicking a pill in one moves the view in the other.
 * Keyed by session id (ephemeral; not persisted).
 */
interface FocusedPaneState {
  bySession: Record<string, string | null>;
  setFocus: (sessionId: string, paneId: string | null) => void;
}

const useFocusedPaneStore = create<FocusedPaneState>((set) => ({
  bySession: {},
  setFocus: (sessionId, paneId) =>
    set((state) => ({ bySession: { ...state.bySession, [sessionId]: paneId } })),
}));

/** Hook: focused pane id for a session (null when none/no session). */
export function useFocusedPane(sessionId: string | null): string | null {
  return useFocusedPaneStore((state) => (sessionId ? state.bySession[sessionId] ?? null : null));
}

/** Imperative setter for non-reactive call sites. */
export function setFocusedPane(sessionId: string, paneId: string | null): void {
  useFocusedPaneStore.getState().setFocus(sessionId, paneId);
}

/** Non-reactive read of a session's focused pane. */
export function getFocusedPane(sessionId: string | null): string | null {
  if (!sessionId) return null;
  return useFocusedPaneStore.getState().bySession[sessionId] ?? null;
}
