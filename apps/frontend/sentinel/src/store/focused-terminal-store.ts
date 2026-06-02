import { create } from 'zustand';

/**
 * Which terminal is focused, per session. Shared so every surface bound to the
 * same session — the SessionsPage pills and the standalone Terminal tab — agrees
 * on the focused terminal: clicking a pill in one moves the view in the other.
 * Keyed by session id (ephemeral; not persisted).
 */
interface FocusedTerminalState {
  bySession: Record<string, string | null>;
  setFocus: (sessionId: string, terminalId: string | null) => void;
}

const useFocusedTerminalStore = create<FocusedTerminalState>((set) => ({
  bySession: {},
  setFocus: (sessionId, terminalId) =>
    set((state) => ({ bySession: { ...state.bySession, [sessionId]: terminalId } })),
}));

/** Hook: focused terminal id for a session (null when none/no session). */
export function useFocusedTerminal(sessionId: string | null): string | null {
  return useFocusedTerminalStore((state) => (sessionId ? state.bySession[sessionId] ?? null : null));
}

/** Imperative setter for non-reactive call sites. */
export function setFocusedTerminal(sessionId: string, terminalId: string | null): void {
  useFocusedTerminalStore.getState().setFocus(sessionId, terminalId);
}

/** Non-reactive read of a session's focused terminal. */
export function getFocusedTerminal(sessionId: string | null): string | null {
  if (!sessionId) return null;
  return useFocusedTerminalStore.getState().bySession[sessionId] ?? null;
}
