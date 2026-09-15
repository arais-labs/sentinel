import { useEffect, useState } from 'react';
import {
  RefreshCw,
  Info,
  Terminal,
  Activity,
  ChevronDown,
} from 'lucide-react';
import { notificationPublisher } from '../lib/notifications';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import type { AuditLog, AuditLogListResponse } from '../types/api';

const notify = notificationPublisher('Activity');

export function AuditPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    void loadActivity(true);
  }, []);

  async function loadActivity(resetLogs: boolean) {
    setLoading(true);
    try {
      const logsPayload = await api.get<AuditLogListResponse>(`/admin/audit?limit=20&offset=${resetLogs ? 0 : offset}`);
      if (resetLogs) {
        setLogs(logsPayload.items);
        setOffset(logsPayload.items.length);
      } else {
        setLogs((current) => [...current, ...logsPayload.items]);
        setOffset((current) => current + logsPayload.items.length);
      }
    } catch { notify.error('Could not load instance activity'); }
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
      title="Instance activity"
      subtitle="Recent instance actions"
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => void loadActivity(true)} aria-label="Refresh activity" className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-(--border-subtle) bg-(--surface-1) text-(--text-secondary) transition-all hover:bg-(--surface-2) hover:text-(--text-primary) hover:border-(--border-strong) active:scale-95 shadow-xs">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      }    >
      <div className="max-w-5xl mx-auto animate-in fade-in duration-300">
        {/* Audit Log Protocol */}
        <Panel className="flex flex-col min-h-[700px]">
          <div className="px-6 py-4 border-b border-(--border-subtle) flex items-center justify-between bg-(--surface-1) rounded-t-lg">
            <div className="flex items-center gap-3">
              <Terminal size={18} className="text-(--text-muted)" />
              <h2 className="font-bold text-sm uppercase tracking-widest">Activity</h2>
            </div>
            <div className="text-[10px] font-bold text-(--text-muted) uppercase tracking-widest flex items-center gap-2">
              <Activity size={14} />
              {logs.length} events
            </div>
          </div>

          <div className="flex-1 p-0 overflow-y-auto">
            {logs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center opacity-30 py-20">
                <Info size={48} strokeWidth={1} />
                <p className="text-sm font-bold uppercase tracking-widest">No activity yet</p>
              </div>
            ) : (
              <div className="divide-y divide-(--border-subtle)">
                {logs.map((log) => (
                  <div key={log.id} className="p-4 hover:bg-(--surface-1)/50 transition-colors group">
                    <div className="flex items-start justify-between gap-4 mb-2">
                      <div className="flex items-center gap-3">
                        <span className="text-[13px] font-bold font-mono tracking-tight text-(--text-primary) group-hover:text-(--accent-solid) transition-colors">
                          {log.action}
                        </span>
                        <StatusChip
                          label={String(log.status_code ?? '-')}
                          tone={log.status_code && log.status_code >= 400 ? 'danger' : 'good'}
                          className="scale-75 origin-left font-mono"
                        />
                      </div>
                      <span className="text-[10px] font-mono text-(--text-muted) whitespace-nowrap">
                        {formatCompactDate(log.timestamp)}
                      </span>
                    </div>

                    <div className="flex flex-wrap gap-x-6 gap-y-2 mt-3">
                      {log.request_id && (
                        <div className="flex items-center gap-2 text-[10px] font-mono text-(--text-muted)">
                          REQ: {log.request_id.slice(0, 8)}...
                        </div>
                      )}
                      {log.resource_type && (
                        <div className="flex items-center gap-2 text-[10px] font-bold text-(--accent-solid) uppercase tracking-widest opacity-70">
                          OBJ: {log.resource_type}:{log.resource_id?.slice(0, 8)}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="p-4 bg-(--surface-1) border-t border-(--border-subtle) rounded-b-lg">
            <button
              onClick={loadMore}
              disabled={loadingMore || logs.length < 20}
              className="btn-secondary w-full h-10 gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              {loadingMore ? <RefreshCw size={14} className="animate-spin" /> : <ChevronDown size={14} />}
              Load more
            </button>
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
