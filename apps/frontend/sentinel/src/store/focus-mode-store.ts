import { flushSync } from 'react-dom';
import { create } from 'zustand';

let transition: ViewTransition | undefined;

// Focus is temporary UI state; never persist it into a saved workspace layout.
export const useFocusModeStore = create<{
  paneId: string | null;
  setPaneId: (paneId: string | null, options?: { animate?: boolean }) => void;
}>((set, get) => ({
  paneId: null,
  setPaneId: (paneId, options) => {
    if (get().paneId === paneId) return;
    transition?.skipTransition();
    if (options?.animate === false || !document.startViewTransition || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      set({ paneId });
      return;
    }
    transition = document.startViewTransition(() => {
      flushSync(() => set({ paneId }));
    });
  },
}));
