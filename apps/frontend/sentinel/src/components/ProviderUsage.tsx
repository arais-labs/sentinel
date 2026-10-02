import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { changedEvent } from '../hooks/useModelCatalog';

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
  connection: {
    auth_method: string | null;
    auth_source: string | null;
    masked_key: string | null;
    models: Record<'fast' | 'normal' | 'hard', string>;
  };
}) {
  const instance = useInstanceName();
  const [usage, setUsage] = useState<AccountUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  // A refreshed status object is not a new connection or a new set of models.
  const connectionKey = JSON.stringify([connection.auth_method, connection.auth_source, connection.masked_key,
    connection.models.fast, connection.models.normal, connection.models.hard]);

  useEffect(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ instance: string; kind?: string }>).detail;
      if (detail.instance === instance && detail.kind !== 'routing') setRefresh(value => value + 1);
    };
    window.addEventListener(changedEvent, changed);
    return () => window.removeEventListener(changedEvent, changed);
  }, [instance]);

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
  }, [provider, instance, active, connectionKey, refresh]);

  return <section aria-label={`${name} account usage`} className="provider-usage">
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
          return <div key={window.key} className="provider-usage-window">
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
