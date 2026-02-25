import { useState } from 'react';
import { Key, LogIn, Moon, Sun, Loader2 } from 'lucide-react';
import { Logo } from './Icons';
import { setToken } from '../lib/api';

export default function AuthGate({ onAuth }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [testing, setTesting] = useState(false);
  const [theme, setTheme] = useState('dark');

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;

    setTesting(true);
    setError('');

    try {
      const res = await fetch('/health', {
        headers: { 'Authorization': `Bearer ${trimmed}` },
      });
      if (!res.ok) {
        setError('Invalid token provided');
        setTesting(false);
        return;
      }
      setToken(trimmed);
      onAuth();
    } catch {
      setError('Cannot reach operator backend');
      setTesting(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center p-4 bg-[color:var(--app-bg)] relative overflow-hidden text-[color:var(--text-primary)]">
      {/* Background Decor */}
      <div className="absolute top-0 left-0 w-full h-full pointer-events-none opacity-20 dark:opacity-40">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-500/20 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-indigo-500/20 blur-[120px] rounded-full" />
      </div>

      <button
        onClick={toggleTheme}
        className="absolute top-6 right-6 p-2.5 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] transition-all shadow-sm"
      >
        {theme === 'dark' ? <Sun size={20} className="text-amber-400" /> : <Moon size={20} className="text-slate-600" />}
      </button>

      <div className="w-full max-w-[420px] animate-in relative z-10">
        <div className="panel p-8 shadow-2xl shadow-black/5 dark:shadow-black/40 bg-[color:var(--surface-0)] border-[color:var(--border-subtle)] rounded-2xl">
          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="space-y-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-lg shadow-black/10 mb-4">
                <Logo size={24} />
              </div>
              <h1 className="text-2xl font-bold tracking-tight">
                Welcome to araiOS
              </h1>
              <p className="text-sm text-[color:var(--text-secondary)]">
                Sign in with your operator token to access the control plane.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                Access Token
              </label>
              <div className="relative">
                <Key size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                <input
                  type="password"
                  className="input-field pl-10 h-12"
                  placeholder="Paste your bearer token here..."
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  autoFocus
                  disabled={testing}
                />
              </div>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 text-[11px] font-medium animate-in">
                {error}
              </div>
            )}

            <div>
              <button
                type="submit"
                disabled={testing || !value.trim()}
                className="btn-primary h-12 w-full text-xs uppercase tracking-widest gap-2"
              >
                {testing ? <Loader2 size={18} className="animate-spin" /> : <LogIn size={18} />}
                Sign In
              </button>
            </div>
          </form>

          <div className="mt-8 pt-6 border-t border-[color:var(--border-subtle)] flex items-center justify-between">
             <div className="flex items-center gap-1.5">
                <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span className="text-[9px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">SYSTEM OPERATIONAL</span>
             </div>
             <span className="text-[9px] font-mono text-[color:var(--text-muted)]">v1.0.0</span>
          </div>
        </div>
        
        <p className="mt-6 text-center text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-[0.2em] opacity-60">
          &copy; 2026 ARAIS INTEL • SECURE CONSOLE
        </p>
      </div>
    </div>
  );
}
