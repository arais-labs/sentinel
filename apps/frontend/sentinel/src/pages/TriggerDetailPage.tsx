import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  RefreshCw,
  Trash2,
  Zap,
  Clock,
  History,
  Info,
  Play,
  Settings,
  AlertTriangle,
  FileCode,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate, toPrettyJson } from '../lib/format';
import type { Trigger, TriggerLog, TriggerLogListResponse } from '../types/api';

function statusTone(status: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (status === 'fired' || status === 'success') return 'good';
  if (status === 'error' || status === 'failed') return 'danger';
  return 'default';
}

export function TriggerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [trigger, setTrigger] = useState<Trigger | null>(null);
  const [logs, setLogs] = useState<TriggerLog[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [isFiring, setIsFiring] = useState(false);

  useEffect(() => {
    void loadAll(true);
  }, [id]);

  async function loadAll(resetLogs: boolean) {
    if (!id) return;
    setLoading(true);
    try {
      const [triggerPayload, logPayload] = await Promise.all([
        api.get<Trigger>(`/triggers/${id}`),
        api.get<TriggerLogListResponse>(`/triggers/${id}/logs?limit=20&offset=0`),
      ]);
      setTrigger(triggerPayload);
      setLogs(logPayload.items);
      setOffset(resetLogs ? logPayload.items.length : offset + logPayload.items.length);
    } catch { toast.error('Failed to load trigger diagnostics'); }
    finally { setLoading(false); }
  }

  async function fireTrigger() {
    if (!id || isFiring) return;
    setIsFiring(true);
    try {
      await api.post(`/triggers/${id}/fire`, { input_payload: {} });
      toast.success('Inbound signal simulated');
      await loadAll(true);
    } catch { toast.error('Signal simulation failed'); }
    finally { setIsFiring(false); }
  }

  async function loadMoreLogs() {
    if (!id || loadingMore) return;
    setLoadingMore(true);
    try {
      const payload = await api.get<TriggerLogListResponse>(`/triggers/${id}/logs?limit=20&offset=${offset}`);
      setLogs((current) => [...current, ...payload.items]);
      setOffset((current) => current + payload.items.length);
    } catch { toast.error('Failed to load telemetry'); }
    finally { setLoadingMore(false); }
  }

  async function deleteTrigger() {
    if (!id || !trigger) return;
    if (!window.confirm(`Permanently de-list trigger "${trigger.name}"?`)) return;
    try {
      await api.delete(`/triggers/${id}`);
      toast.success('Trigger purged from registry');
      navigate('/triggers');
    } catch { toast.error('Purge failed'); }
  }

  return (
    <AppShell
      title={trigger?.name || 'Automation Diagnostics'}
      subtitle={trigger ? `Type: ${trigger.type} • Protocol: ${trigger.action_type}` : 'Analyzing telemetry...'}
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => navigate('/triggers')} className="btn-secondary h-9 px-3 text-xs gap-2">
            <ArrowLeft size={14} /> Back
          </button>
          <div className="h-6 w-px bg-[color:var(--border-subtle)] mx-1" />
          <button onClick={() => void loadAll(true)} className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          </button>
          <button 
            onClick={deleteTrigger} 
            disabled={!trigger}
            className="p-2 rounded-md hover:bg-rose-500/10 text-rose-500 transition-colors"
          >
            <Trash2 size={18} />
          </button>
        </div>
      }
    >
      {loading && !trigger ? (
        <div className="h-full flex items-center justify-center py-20">
          <RefreshCw size={32} className="animate-spin text-[color:var(--text-muted)]" />
        </div>
      ) : !trigger ? (
        <div className="flex flex-col items-center justify-center py-20 opacity-40">
          <AlertTriangle size={48} className="mb-4" />
          <p className="text-sm font-bold uppercase tracking-widest">Automation node not found</p>
        </div>
      ) : (
        <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-6 items-start">
          {/* Left: Technical Specs */}
          <div className="space-y-6">
            <Panel className="p-6 space-y-6">
              <div className="flex items-center justify-between">
                <h2 className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Configuration</h2>
                <StatusChip label={trigger.enabled ? 'operational' : 'offline'} tone={trigger.enabled ? 'good' : 'default'} />
              </div>

              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">Type</p>
                    <StatusChip label={trigger.type} tone="info" className="scale-90 origin-left" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">Action</p>
                    <StatusChip label={trigger.action_type} className="scale-90 origin-left opacity-70" />
                  </div>
                </div>

                <div className="pt-4 border-t border-[color:var(--border-subtle)] space-y-3">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-[color:var(--text-muted)] font-medium uppercase tracking-wider flex items-center gap-2">
                      <Zap size={12} /> Fire Count
                    </span>
                    <span className="font-mono font-bold">{trigger.fire_count}</span>
                  </div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-[color:var(--text-muted)] font-medium uppercase tracking-wider flex items-center gap-2 text-rose-500">
                      <AlertTriangle size={12} /> Errors
                    </span>
                    <span className="font-mono font-bold text-rose-500">{trigger.error_count}</span>
                  </div>
                </div>

                <div className="pt-4 space-y-2">
                   <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-wider flex items-center gap-2">
                    <Clock size={12} /> Last Run: {formatCompactDate(trigger.last_fired_at)}
                  </p>
                  <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-wider flex items-center gap-2">
                    <History size={12} /> Next Run: {formatCompactDate(trigger.next_fire_at)}
                  </p>
                </div>
              </div>

              <button 
                onClick={fireTrigger} 
                disabled={isFiring}
                className="btn-primary w-full h-11 gap-2"
              >
                {isFiring ? <RefreshCw size={16} className="animate-spin" /> : <Play size={16} fill="currentColor" />}
                Force Invocation
              </button>
            </Panel>

            <Panel className="p-6 space-y-4">
              <div className="flex items-center gap-2">
                <Settings size={14} className="text-[color:var(--text-muted)]" />
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Entry Schema</h3>
              </div>
              <JsonBlock value={toPrettyJson(trigger.config)} className="max-h-[300px]" />
              
              <div className="flex items-center gap-2 pt-2">
                <FileCode size={14} className="text-[color:var(--text-muted)]" />
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Action Payload</h3>
              </div>
              <JsonBlock value={toPrettyJson(trigger.action_config)} className="max-h-[300px]" />
            </Panel>
          </div>

          {/* Right: Execution Log Timeline */}
          <Panel className="flex flex-col min-h-[600px]">
            <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)] rounded-t-lg">
              <div className="flex items-center gap-3">
                <History size={18} className="text-[color:var(--text-muted)]" />
                <h2 className="font-bold text-sm uppercase tracking-widest">Execution Telemetry</h2>
              </div>
              <div className="flex items-center gap-2 text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
                {logs.length} Data Points
              </div>
            </div>

            <div className="flex-1 p-6 space-y-4">
              {logs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center opacity-30 gap-4">
                  <Info size={48} strokeWidth={1} />
                  <p className="text-[10px] font-bold uppercase tracking-widest text-center">No telemetry received for this node</p>
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    {logs.map((log) => (
                      <div key={log.id} className="p-4 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] transition-all animate-in fade-in slide-in-from-left-2 duration-300">
                        <div className="flex items-center justify-between mb-3">
                          <div className="flex items-center gap-3">
                            {log.status === 'success' || log.status === 'fired' ? (
                              <CheckCircle2 size={16} className="text-emerald-500" />
                            ) : (
                              <XCircle size={16} className="text-rose-500" />
                            )}
                            <StatusChip label={log.status} tone={statusTone(log.status)} className="scale-90 origin-left" />
                          </div>
                          <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                            {formatCompactDate(log.fired_at)}
                          </span>
                        </div>

                        {log.error_message && (
                          <div className="mb-3 p-3 rounded-lg bg-rose-500/5 border border-rose-500/10 text-xs text-rose-600 font-medium">
                            {log.error_message}
                          </div>
                        )}

                        {log.output_summary && (
                          <p className="text-[13px] text-[color:var(--text-secondary)] leading-relaxed mb-3">
                            {log.output_summary}
                          </p>
                        )}

                        {log.input_payload && (
                          <details className="group">
                            <summary className="cursor-pointer list-none flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
                              <FileCode size={12} /> 
                              <span>Signal Payload</span>
                            </summary>
                            <div className="mt-3">
                              <JsonBlock value={toPrettyJson(log.input_payload)} className="max-h-[200px]" />
                            </div>
                          </details>
                        )}
                      </div>
                    ))}
                  </div>

                  {offset < 1000 && logs.length >= 20 && (
                    <button 
                      onClick={loadMoreLogs}
                      disabled={loadingMore}
                      className="btn-secondary w-full h-11 gap-2 text-[10px] uppercase tracking-widest mt-4"
                    >
                      {loadingMore ? <RefreshCw size={14} className="animate-spin" /> : <History size={14} />}
                      Load Older Telemetry
                    </button>
                  )}
                </>
              )}
            </div>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
