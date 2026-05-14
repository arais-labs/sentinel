import { useEffect, useState } from 'react';
import {
  RefreshCw,
  History,
  Info,
  Server,
  Key,
  Clock,
  Terminal,
  Activity,
  User,
  ChevronDown,
} from 'lucide-react';
import { Navigate, useLocation } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import { instanceRouteFromPath } from '../lib/routes';
import { useAuthStore } from '../store/auth-store';
import type { AuditLog, AuditLogListResponse, ConfigResponse } from '../types/api';

export function AdminPage() {
  const location = useLocation();
  const role = useAuthStore((s) => s.role);

  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    void loadAdminData(true);
  }, []);

  if (role !== 'admin') {
    return <Navigate to={instanceRouteFromPath(location.pathname, 'settings')} replace />;
  }

  async function loadAdminData(resetLogs: boolean) {
    setLoading(true);
    try {
      const [configPayload, logsPayload] = await Promise.all([
        api.get<ConfigResponse>('/admin/config'),
        api.get<AuditLogListResponse>(`/admin/audit?limit=20&offset=${resetLogs ? 0 : offset}`),
      ]);

      setConfig(configPayload);
      if (resetLogs) {
        setLogs(logsPayload.items);
        setOffset(logsPayload.items.length);
      } else {
        setLogs((current) => [...current, ...logsPayload.items]);
        setOffset((current) => current + logsPayload.items.length);
      }
    } catch { toast.error('Access denied or system failure during registry fetch'); }
    finally { setLoading(false); }
  }

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    try {
      const payload = await api.get<AuditLogListResponse>(`/admin/audit?limit=20&offset=${offset}`);
      setLogs((current) => [...current, ...payload.items]);
      setOffset((current) => current + payload.items.length);
    } finally { setLoadingMore(false); }
  }

  return (
    <AppShell
      title="System Administration"
      subtitle="Critical Overrides & Audit Protocol"
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => void loadAdminData(true)} className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-all active:scale-95">
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      }    >
      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-6 items-start animate-in fade-in duration-300">
        <div className="space-y-6">
          {/* System Parameters */}
          <Panel className="p-6 space-y-6">
            <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
              <Server size={18} className="text-[color:var(--text-muted)]" />
              <h2 className="text-sm font-bold uppercase tracking-widest">System Parameters</h2>
            </div>

            <div className="space-y-4">
              <div className="space-y-1">
                <span className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
                  <Activity size={10} /> Node Environment
                </span>
                <p className="text-xs font-mono font-bold pl-4 border-l border-[color:var(--border-subtle)] uppercase">
                  {config?.app_env || 'N/A'}
                </p>
              </div>

              <div className="space-y-1">
                <span className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
                  <Key size={10} /> Encryption Protocol
                </span>
                <p className="text-xs font-mono font-bold pl-4 border-l border-[color:var(--border-subtle)]">
                  {config?.jwt_algorithm || 'N/A'}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <span className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
                    <Clock size={10} /> Access TTL
                  </span>
                  <p className="text-xs font-mono font-bold pl-4 border-l border-[color:var(--border-subtle)]">
                    {config?.access_token_ttl_seconds}s
                  </p>
                </div>
                <div className="space-y-1">
                  <span className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
                    <History size={10} /> Refresh TTL
                  </span>
                  <p className="text-xs font-mono font-bold pl-4 border-l border-[color:var(--border-subtle)]">
                    {config?.refresh_token_ttl_seconds}s
                  </p>
                </div>
              </div>

              <p className="text-[10px] text-[color:var(--text-muted)]">
                Instance URLs are managed in Settings.
              </p>
            </div>
          </Panel>
        </div>

        {/* Audit Log Protocol */}
        <Panel className="flex flex-col min-h-[700px]">
          <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)] rounded-t-lg">
            <div className="flex items-center gap-3">
              <Terminal size={18} className="text-[color:var(--text-muted)]" />
              <h2 className="font-bold text-sm uppercase tracking-widest">Audit Protocol</h2>
            </div>
            <div className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
              <Activity size={14} />
              {logs.length} Sequential Events
            </div>
          </div>

          <div className="flex-1 p-0 overflow-y-auto">
            {logs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center opacity-30 py-20">
                <Info size={48} strokeWidth={1} />
                <p className="text-sm font-bold uppercase tracking-widest">No protocol logs detected</p>
              </div>
            ) : (
              <div className="divide-y divide-[color:var(--border-subtle)]">
                {logs.map((log) => (
                  <div key={log.id} className="p-4 hover:bg-[color:var(--surface-1)]/50 transition-colors group">
                    <div className="flex items-start justify-between gap-4 mb-2">
                      <div className="flex items-center gap-3">
                        <span className="text-[13px] font-bold font-mono tracking-tight text-[color:var(--text-primary)] group-hover:text-[color:var(--accent-solid)] transition-colors">
                          {log.action}
                        </span>
                        <StatusChip 
                          label={String(log.status_code ?? '-')} 
                          tone={log.status_code && log.status_code >= 400 ? 'danger' : 'good'} 
                          className="scale-75 origin-left font-mono"
                        />
                      </div>
                      <span className="text-[10px] font-mono text-[color:var(--text-muted)] whitespace-nowrap">
                        {formatCompactDate(log.timestamp)}
                      </span>
                    </div>
                    
                    <div className="flex flex-wrap gap-x-6 gap-y-2 mt-3">
                      <div className="flex items-center gap-2 text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
                        <User size={12} /> {log.user_id || 'SYSTEM'}
                      </div>
                      {log.request_id && (
                        <div className="flex items-center gap-2 text-[10px] font-mono text-[color:var(--text-muted)]">
                          REQ: {log.request_id.slice(0, 8)}...
                        </div>
                      )}
                      {log.resource_type && (
                        <div className="flex items-center gap-2 text-[10px] font-bold text-[color:var(--accent-solid)] uppercase tracking-widest opacity-70">
                          OBJ: {log.resource_type}:{log.resource_id?.slice(0, 8)}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="p-4 bg-[color:var(--surface-1)] border-t border-[color:var(--border-subtle)] rounded-b-lg">
            <button 
              onClick={loadMore}
              disabled={loadingMore || logs.length < 20}
              className="btn-secondary w-full h-10 gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              {loadingMore ? <RefreshCw size={14} className="animate-spin" /> : <ChevronDown size={14} />}
              Load Preceding Sequence
            </button>
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
