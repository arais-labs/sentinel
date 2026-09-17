import { create } from 'zustand';

/** The resolved Voice session per instance, shared between the overlay and the island controls. */
export const useVoiceSessionStore = create<{
  ids: Record<string, string | null>;
  setSessionId: (instance: string, sessionId: string | null) => void;
}>(set => ({
  ids: {},
  setSessionId: (instance, sessionId) => set(state => ({ ids: { ...state.ids, [instance]: sessionId } })),
}));
