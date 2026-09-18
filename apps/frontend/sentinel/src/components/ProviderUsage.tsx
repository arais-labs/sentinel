import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';

interface AccountUsage {
  status: 'available' | 'unavailable' | 'not_connected';
  windows: { key: string; label: string; remaining_percent: number; resets_at: string | null }[];
  checked_at: string;
  message: string | null;
}

export function ProviderUsage({ provider, name, active, connection }: {
  provider: 'anthropic' | 'openai' | 'gemini';
  name: string;
  active: boolean;
  connection: object;
}) {
  const instance = useInstanceName();
  const [usage, setUsage] = useState<AccountUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setUsage(null);
    const load = async () => {
      if (document.hidden) return;
      setLoading(true);
      try {
        const result = await api.get<AccountUsage>(`/settings/providers/${provider}/usage`);
        if (!cancelled) setUsage(result);
      } catch {
        if (!cancelled) setUsage({ status: 'unavailable', windows: [], checked_at: '', message: 'Usage is temporarily unavailable.' });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 5 * 60_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [provider, instance, active, connection, refresh]);

  return <section aria-label={`${name} account usage`} className="mx-3.5 mb-3 border-t border-(--border-subtle) pt-3 space-y-2">
    <div className="flex items-center justify-between gap-2 text-[10px] text-(--text-muted)">
      <span>Remaining usage</span>
      <button type="button" aria-label={`Refresh ${name} usage`} disabled={loading}
        title="Account usage is cached for up to 5 minutes"
        onClick={() => setRefresh(value => value + 1)} className="hover:text-(--text-primary) disabled:opacity-50">
        <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
      </button>
    </div>
    {!usage ? <p className="text-[10px] text-(--text-muted)" role="status">Loading usage…</p>
      : usage.status !== 'available' ? <p className="text-[10px] text-(--text-muted)" role="status">{usage.message}</p>
      : <>
        {usage.windows.map(window => {
          const remaining = window.remaining_percent;
          const reset = window.resets_at ? new Date(window.resets_at) : null;
          const percent = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(remaining);
          return <div key={window.key} className="space-y-1">
            <div className="flex justify-between gap-2 text-[10px]">
              <span className="text-(--text-muted) truncate" title={window.label}>{window.label}</span>
              <span className="shrink-0 tabular-nums">{percent}% left</span>
            </div>
            <div role="progressbar" aria-label={`${window.label} remaining`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={remaining}
              className="h-1 rounded-full bg-(--surface-2) overflow-hidden">
              <div className={remaining <= 10 ? 'h-full bg-rose-400' : remaining <= 25 ? 'h-full bg-amber-400' : 'h-full bg-emerald-400'} style={{ width: `${remaining}%` }} />
            </div>
            {reset && <p className="text-[9px] text-(--text-muted)" title={reset.toLocaleString()}>
              {reset.getTime() <= Date.now() ? 'Reset pending' : `Resets ${reset.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}`}
            </p>}
          </div>;
        })}
        <p className="text-[9px] text-(--text-muted)" title="Refreshes every 5 minutes">Updated {new Date(usage.checked_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</p>
      </>}
  </section>;
}
