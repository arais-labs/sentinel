import { create } from 'zustand';

export type ThemeMode = 'light' | 'dark';

const storageKey = 'sentinel.theme';

interface ThemeState {
  theme: ThemeMode;
  initializeTheme: () => void;
  setTheme: (theme: ThemeMode) => void;
  toggleTheme: () => void;
}

function applyTheme(theme: ThemeMode) {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.dataset.theme = theme;
}

function resolveInitialTheme(): ThemeMode {
  const persisted = localStorage.getItem(storageKey);
  if (persisted === 'dark' || persisted === 'light') {
    return persisted;
  }

  return 'dark';
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: 'dark',

  initializeTheme: () => {
    const theme = resolveInitialTheme();
    applyTheme(theme);
    set({ theme });
  },

  setTheme: (theme) => {
    localStorage.setItem(storageKey, theme);
    applyTheme(theme);
    set({ theme });
  },

  toggleTheme: () => {
    const next: ThemeMode = get().theme === 'dark' ? 'light' : 'dark';
    get().setTheme(next);
  },
}));
