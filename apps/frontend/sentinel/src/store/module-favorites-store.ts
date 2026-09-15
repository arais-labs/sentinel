import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

interface ModuleFavoritesState {
  favorites: Record<string, string[]>;
  toggle: (instance: string, module: string) => void;
}

export const useModuleFavorites = create<ModuleFavoritesState>()(
  persist(
    (set) => ({
      favorites: {},
      toggle: (instance, module) => set(state => {
        const current = state.favorites[instance] ?? [];
        return { favorites: { ...state.favorites, [instance]: current.includes(module)
          ? current.filter(name => name !== module) : [...current, module] } };
      }),
    }),
    {
      name: 'sentinel.module-favorites',
      storage: createJSONStorage(() => localStorage),
      partialize: state => ({ favorites: state.favorites }),
    },
  ),
);
