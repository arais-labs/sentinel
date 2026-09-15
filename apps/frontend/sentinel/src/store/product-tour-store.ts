import { create } from 'zustand';

export const useProductTourStore = create<{
  instanceName: string | null;
  open: (instanceName: string) => void;
  close: () => void;
}>((set) => ({
  instanceName: null,
  open: (instanceName) => set({ instanceName }),
  close: () => set({ instanceName: null }),
}));
