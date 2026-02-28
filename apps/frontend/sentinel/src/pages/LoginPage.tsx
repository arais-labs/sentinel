import { FormEvent, useState } from 'react';
import { Key, LogIn, Moon, Sun, Loader2 } from 'lucide-react';
import { Navigate } from 'react-router-dom';

import { API_BASE_URL } from '../lib/env';
import { useAuthStore } from '../store/auth-store';
import { useThemeStore } from '../store/theme-store';
import { Logo } from '../components/ui/Logo';

export function LoginPage() {
  const status = useAuthStore((state) => state.status);
  const login = useAuthStore((state) => state.login);
  const error = useAuthStore((state) => state.errorMessage);
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);

  const [token, setToken] = useState('');

  if (status === 'authenticated') {
    return <Navigate to="/sessions" replace />;
  }

  const isLoading = status === 'loading';

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const value = token.trim();
    if (!value) return;
    await login(value);
  }

  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center p-4 bg-[color:var(--app-bg)] relative overflow-hidden">
      {/* Background Decor */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none opacity-20 dark:opacity-40">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-sky-500/20 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-indigo-500/20 blur-[120px] rounded-full" />
      </div>

      <button
        onClick={toggleTheme}
        className="absolute top-6 right-6 p-2.5 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] transition-all shadow-sm"
      >
        {theme === 'dark' ? <Sun size={20} className="text-amber-400" /> : <Moon size={20} className="text-slate-600" />}
      </button>

      <div className="w-full max-w-[420px] animate-in relative z-10">
        <div className="panel p-8 shadow-2xl shadow-black/5 dark:shadow-black/40">
          <form onSubmit={onSubmit} className="space-y-6">
            <div className="space-y-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-lg shadow-black/10 mb-4">
                <Logo size={24} />
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-[color:var(--text-primary)]">
                Welcome to Sentinel
              </h1>
              <p className="text-sm text-[color:var(--text-secondary)]">
                Sign in with your araiOS operator token to access the control plane.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                Access Token
              </label>
              <div className="relative">
                <Key size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                <input
                  type="password"
                  className="input-field pl-10 h-12"
                  placeholder="Paste your token here..."
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  autoFocus
                  disabled={isLoading}
                />
              </div>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-xs font-medium animate-in">
                {error}
              </div>
            )}

            <div>
              <button
                type="submit"
                disabled={isLoading || !token.trim()}
                className="btn-primary h-12 w-full"
              >
                {isLoading ? <Loader2 size={18} className="animate-spin" /> : <LogIn size={18} />}
                Sign In
              </button>
            </div>
          </form>

          <div className="mt-8 pt-6 border-t border-[color:var(--border-subtle)] flex items-center justify-between">
             <div className="flex items-center gap-1.5">
                <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">SYSTEM ONLINE</span>
             </div>
             <span className="text-[10px] font-mono text-[color:var(--text-muted)]">{API_BASE_URL}</span>
          </div>
        </div>
        
        <p className="mt-6 text-center text-[11px] text-[color:var(--text-muted)] font-medium uppercase tracking-widest">
          &copy; 2026 ARAIS INTEL • SECURE OPERATOR PORTAL
        </p>
      </div>
    </div>
  );
}
