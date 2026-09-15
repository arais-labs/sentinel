import { RetainedSessionContext } from '../lib/workspace-context-values';
import { useCallback, useContext } from 'react';
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useInstanceName } from '../lib/workspace-context';

interface ActiveSessionState {
  byInstance: Record<string, string | null>;
  composerFocusRequest: { instanceName: string; sessionId: string } | null;
  recentByInstance: Record<string, string[]>;
  draftReturnByInstance: Record<string, string | null>;
  setActiveSession: (instanceName: string, sessionId: string | null) => void;
  dismissRecentSession: (instanceName: string, sessionId: string) => void;
  closeActiveSession: (instanceName: string, availableIds: string[]) => string | null;
  moveRecentSession: (instanceName: string, sessionId: string, targetId: string, after: boolean) => void;
}

// Machine panes follow the selected session within their own instance.
export const useActiveSessionStore = create<ActiveSessionState>()(
  persist(
    (set) => ({
      byInstance: {},
      composerFocusRequest: null,
      recentByInstance: {},
      draftReturnByInstance: {},
      dismissRecentSession: (instanceName, sessionId) => set(state => ({
        recentByInstance: { ...state.recentByInstance, [instanceName]: (state.recentByInstance[instanceName] ?? []).filter(id => id !== sessionId) },
      })),
      closeActiveSession: (instanceName, availableIds) => {
        let next: string | null = null;
        set(state => {
          const active = state.byInstance[instanceName];
          if (!active) {
            const available = new Set(availableIds);
            const recent = (state.recentByInstance[instanceName] ?? []).filter(id => available.has(id));
            const previous = state.draftReturnByInstance[instanceName];
            next = previous && recent.includes(previous) ? previous : recent.at(-1) ?? null;
            return {
              byInstance: { ...state.byInstance, [instanceName]: next },
              draftReturnByInstance: { ...state.draftReturnByInstance, [instanceName]: null },
            };
          }
          const watched = state.recentByInstance[instanceName] ?? [];
          const available = new Set(availableIds);
          const visible = watched.filter(id => available.has(id));
          const index = Math.max(0, visible.indexOf(active));
          const remaining = visible.filter(id => id !== active);
          next = remaining[Math.min(index, remaining.length - 1)] ?? null;
          return {
            byInstance: { ...state.byInstance, [instanceName]: next },
            recentByInstance: { ...state.recentByInstance, [instanceName]: watched.filter(id => id !== active) },
          };
        });
        return next;
      },
      moveRecentSession: (instanceName, sessionId, targetId, after) => set(state => {
        const ids = state.recentByInstance[instanceName] ?? [];
        if (sessionId === targetId || !ids.includes(sessionId) || !ids.includes(targetId)) return state;
        const reordered = ids.filter(id => id !== sessionId);
        reordered.splice(reordered.indexOf(targetId) + Number(after), 0, sessionId);
        return { recentByInstance: { ...state.recentByInstance, [instanceName]: reordered } };
      }),
      setActiveSession: (instanceName, sessionId) => set((state) =>
        state.byInstance[instanceName] === sessionId && (!sessionId || state.recentByInstance[instanceName]?.includes(sessionId)) ? state : {
          byInstance: { ...state.byInstance, [instanceName]: sessionId },
          draftReturnByInstance: {
            ...state.draftReturnByInstance,
            [instanceName]: sessionId === null ? state.byInstance[instanceName] ?? null : null,
          },
          recentByInstance: sessionId && !state.recentByInstance[instanceName]?.includes(sessionId)
            ? { ...state.recentByInstance, [instanceName]: [...(state.recentByInstance[instanceName] ?? []), sessionId] }
            : state.recentByInstance,
        }),
    }),
    {
      name: 'sentinel.instance-sessions',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ byInstance: state.byInstance, recentByInstance: state.recentByInstance }),
    },
  ),
);

export function useActiveSessionId(): string | null {
  const instanceName = useInstanceName();
  const retained = useContext(RetainedSessionContext);
  const selected = useActiveSessionStore((state) => instanceName ? state.byInstance[instanceName] ?? null : null);
  return retained ? retained.sessionId : selected;
}

export function useSetActiveSession(): (sessionId: string | null) => void {
  const instanceName = useInstanceName();
  const setActiveSession = useActiveSessionStore((state) => state.setActiveSession);
  return useCallback((sessionId: string | null) => {
    if (instanceName) setActiveSession(instanceName, sessionId);
  }, [instanceName, setActiveSession]);
}
