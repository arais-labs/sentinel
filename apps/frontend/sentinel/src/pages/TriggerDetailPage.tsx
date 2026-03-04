import { useEffect, useMemo, useState } from 'react';
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
  Save,
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
import type { Session, SessionListResponse, Trigger, TriggerLog, TriggerLogListResponse } from '../types/api';

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
  const [sessions, setSessions] = useState<Session[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [isFiring, setIsFiring] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [editName, setEditName] = useState('');
  const [editEnabled, setEditEnabled] = useState(true);
  const [editType, setEditType] = useState('heartbeat');
  const [editActionType, setEditActionType] = useState('agent_message');
  const [editConfigText, setEditConfigText] = useState('{}');
  const [editActionConfigText, setEditActionConfigText] = useState('{}');
  const [useManualConfig, setUseManualConfig] = useState(false);
  const [useManualAction, setUseManualAction] = useState(false);
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [heartbeatInterval, setHeartbeatInterval] = useState(3600);
  const [agentMsg, setAgentMsg] = useState('');
  const [routeMode, setRouteMode] = useState<'main' | 'session'>('main');
  const [targetSessionId, setTargetSessionId] = useState('');
  const [toolName, setToolName] = useState('');
  const [toolArgs, setToolArgs] = useState('{}');
  const [httpUrl, setHttpUrl] = useState('');
  const [httpMethod, setHttpMethod] = useState('POST');
  const [invokePayloadText, setInvokePayloadText] = useState(
    '{\n  "source": "manual",\n  "signal": "force_invocation"\n}',
  );

  const routeSessions = useMemo(
    () => sessions.filter((session) => !session.parent_session_id),
    [sessions],
  );
  const isRouteTargetMissing = useMemo(
    () =>
      routeMode === 'session'
      && Boolean(targetSessionId)
      && !routeSessions.some((session) => session.id === targetSessionId),
    [routeMode, targetSessionId, routeSessions],
  );

  useEffect(() => {
    void loadAll(true);
  }, [id]);

  useEffect(() => {
    if (!trigger) return;
    const config = trigger.config as Record<string, unknown>;
    const action = trigger.action_config as Record<string, unknown>;
    const route = resolveAgentRoute(action);
    setEditName(trigger.name);
    setEditEnabled(trigger.enabled);
    setEditType(trigger.type);
    setEditActionType(trigger.action_type);
    setEditConfigText(toPrettyJson(trigger.config));
    setEditActionConfigText(toPrettyJson(trigger.action_config));
    setUseManualConfig(false);
    setUseManualAction(false);
    setCronExpr(readString(config, ['expr', 'cron'], '0 9 * * *'));
    setHeartbeatInterval(readNumber(config, ['interval_seconds', 'interval'], 3600));
    setAgentMsg(readString(action, ['message'], ''));
    setRouteMode(route.routeMode);
    setTargetSessionId(route.targetSessionId);
    setToolName(readString(action, ['name', 'tool_name'], ''));
    setToolArgs(toPrettyJson((action.arguments as Record<string, unknown>) ?? (action.payload as Record<string, unknown>) ?? {}));
    setHttpUrl(readString(action, ['url'], ''));
    setHttpMethod(readString(action, ['method'], 'POST'));
  }, [trigger]);

  async function loadAll(resetLogs: boolean) {
    if (!id) return;
    setLoading(true);
    try {
      const [triggerPayload, logPayload, sessionPayload] = await Promise.all([
        api.get<Trigger>(`/triggers/${id}`),
        api.get<TriggerLogListResponse>(`/triggers/${id}/logs?limit=20&offset=0`),
        api.get<SessionListResponse>('/sessions?limit=300&offset=0'),
      ]);
      setTrigger(triggerPayload);
      setLogs(logPayload.items);
      setSessions(sessionPayload.items);
      setOffset(resetLogs ? logPayload.items.length : offset + logPayload.items.length);
    } catch { toast.error('Failed to load trigger diagnostics'); }
    finally { setLoading(false); }
  }

  async function fireTrigger() {
    if (!id || isFiring) return;
    let parsedPayload: Record<string, unknown>;
    try {
      const parsed = JSON.parse(invokePayloadText || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast.error('Signal payload must be a JSON object');
        return;
      }
      parsedPayload = parsed as Record<string, unknown>;
    } catch {
      toast.error('Signal payload is not valid JSON');
      return;
    }

    setIsFiring(true);
    try {
      const result = await api.post<TriggerLog>(`/triggers/${id}/fire`, { input_payload: parsedPayload });
      if (result.status === 'failed') {
        toast.error(result.error_message || 'Invocation failed');
      } else {
        toast.success('Trigger invoked');
      }
      await loadAll(true);
    } catch { toast.error('Trigger invocation failed'); }
    finally { setIsFiring(false); }
  }

  async function saveTrigger() {
    if (!id || !trigger || isSaving) return;
    if (!editName.trim()) {
      toast.error('Trigger name is required');
      return;
    }

    const configPayload = buildConfigPayload({
      type: editType,
      useManualConfig,
      configText: editConfigText,
      cronExpr,
      heartbeatInterval,
    });
    if (!configPayload.ok) {
      toast.error(configPayload.error);
      return;
    }
    const actionConfigPayload = buildActionConfigPayload({
      actionType: editActionType,
      useManualAction,
      actionConfigText: editActionConfigText,
      agentMsg,
      routeMode,
      targetSessionId,
      toolName,
      toolArgs,
      httpUrl,
      httpMethod,
    });
    if (!actionConfigPayload.ok) {
      toast.error(actionConfigPayload.error);
      return;
    }

    setIsSaving(true);
    try {
      const updated = await api.patch<Trigger>(`/triggers/${id}`, {
        name: editName.trim(),
        type: editType,
        action_type: editActionType,
        config: configPayload.value,
        action_config: actionConfigPayload.value,
        enabled: editEnabled,
      });
      setTrigger(updated);
      toast.success('Trigger updated');
    } catch {
      toast.error('Failed to update trigger');
    } finally {
      setIsSaving(false);
    }
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

              <div className="space-y-2">
                <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Signal Payload (JSON)</p>
                <textarea
                  value={invokePayloadText}
                  onChange={(event) => setInvokePayloadText(event.target.value)}
                  className="input-field min-h-[100px] py-2.5 resize-none font-mono text-[11px]"
                />
              </div>
            </Panel>

            <Panel className="p-6 space-y-4">
              <div className="flex items-center gap-2">
                <Settings size={14} className="text-[color:var(--text-muted)]" />
                <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Modify Trigger</h3>
              </div>
              <div className="space-y-2">
                <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Name</p>
                <input
                  value={editName}
                  onChange={(event) => setEditName(event.target.value)}
                  className="input-field h-10"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Type</p>
                  <select
                    value={editType}
                    onChange={(event) => setEditType(event.target.value)}
                    className="input-field h-10 text-xs font-bold uppercase tracking-wider"
                  >
                    <option value="cron">cron</option>
                    <option value="heartbeat">heartbeat</option>
                    <option value="webhook">webhook</option>
                    <option value="event">event</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Action</p>
                  <select
                    value={editActionType}
                    onChange={(event) => setEditActionType(event.target.value)}
                    className="input-field h-10 text-xs font-bold uppercase tracking-wider"
                  >
                    <option value="agent_message">agent_message</option>
                    <option value="tool_call">tool_call</option>
                    <option value="http_request">http_request</option>
                  </select>
                </div>
              </div>

              <label className="flex items-center gap-2 text-[11px] text-[color:var(--text-secondary)]">
                <input
                  type="checkbox"
                  checked={editEnabled}
                  onChange={(event) => setEditEnabled(event.target.checked)}
                  className="w-4 h-4 accent-[color:var(--accent-solid)]"
                />
                Enabled
              </label>

              <div className="grid grid-cols-1 gap-5">
                <div className="space-y-3 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] p-4">
                  <div className="flex items-center justify-between">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                      <Clock size={12} /> Entry Point
                    </p>
                    <button
                      type="button"
                      onClick={() => setUseManualConfig((value) => !value)}
                      className={`text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border transition-colors ${useManualConfig ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent' : 'text-[color:var(--text-muted)] border-[color:var(--border-subtle)]'}`}
                    >
                      Manual
                    </button>
                  </div>
                  {!useManualConfig ? (
                    <div className="space-y-3">
                      {editType === 'cron' && (
                        <div className="space-y-2">
                          <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Cron Expression</p>
                          <input
                            value={cronExpr}
                            onChange={(event) => setCronExpr(event.target.value)}
                            className="input-field h-9 font-mono text-xs"
                          />
                        </div>
                      )}
                      {editType === 'heartbeat' && (
                        <div className="space-y-2">
                          <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Interval (Seconds)</p>
                          <input
                            type="number"
                            min={1}
                            value={heartbeatInterval}
                            onChange={(event) => setHeartbeatInterval(Number.parseInt(event.target.value, 10) || 1)}
                            className="input-field h-9 font-mono text-xs"
                          />
                        </div>
                      )}
                      {(editType === 'webhook' || editType === 'event') && (
                        <p className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">No assisted fields for this trigger type. Switch to manual mode for raw JSON.</p>
                      )}
                    </div>
                  ) : (
                    <textarea
                      value={editConfigText}
                      onChange={(event) => setEditConfigText(event.target.value)}
                      className="input-field min-h-[120px] py-2.5 resize-none font-mono text-[11px]"
                    />
                  )}
                </div>

                <div className="space-y-3 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] p-4">
                  <div className="flex items-center justify-between">
                    <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                      <Zap size={12} /> Execution Action
                    </p>
                    <button
                      type="button"
                      onClick={() => setUseManualAction((value) => !value)}
                      className={`text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border transition-colors ${useManualAction ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent' : 'text-[color:var(--text-muted)] border-[color:var(--border-subtle)]'}`}
                    >
                      Manual
                    </button>
                  </div>
                  {!useManualAction ? (
                    <div className="space-y-3">
                      {editActionType === 'agent_message' && (
                        <>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Instruction</p>
                            <textarea
                              value={agentMsg}
                              onChange={(event) => setAgentMsg(event.target.value)}
                              className="input-field min-h-[80px] py-2 text-xs resize-none"
                            />
                          </div>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Route</p>
                            <select
                              value={routeMode}
                              onChange={(event) => {
                                const nextMode = event.target.value === 'session' ? 'session' : 'main';
                                setRouteMode(nextMode);
                                if (nextMode === 'main') {
                                  setTargetSessionId('');
                                } else if (!targetSessionId && routeSessions[0]?.id) {
                                  setTargetSessionId(routeSessions[0].id);
                                }
                              }}
                              className="input-field h-9 text-[10px] font-bold uppercase tracking-wider"
                            >
                              <option value="main">Main Session</option>
                              <option value="session">Specific Session</option>
                            </select>
                          </div>
                          {routeMode === 'session' && (
                            <div className="space-y-2">
                              <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Target Session</p>
                              <select
                                value={targetSessionId}
                                onChange={(event) => setTargetSessionId(event.target.value)}
                                className="input-field h-9 text-[10px] font-bold tracking-wide"
                              >
                                {!targetSessionId && <option value="">Select session…</option>}
                                {routeSessions.map((session) => (
                                  <option key={session.id} value={session.id}>
                                    {session.is_main ? 'Main' : 'Session'} · {session.title || session.id.slice(0, 8)}
                                  </option>
                                ))}
                                {isRouteTargetMissing && (
                                  <option value={targetSessionId}>
                                    Missing session ({targetSessionId.slice(0, 8)})
                                  </option>
                                )}
                              </select>
                            </div>
                          )}
                        </>
                      )}
                      {editActionType === 'tool_call' && (
                        <>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Capability Name</p>
                            <input
                              value={toolName}
                              onChange={(event) => setToolName(event.target.value)}
                              className="input-field h-9 font-mono text-xs"
                            />
                          </div>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Arguments (JSON)</p>
                            <textarea
                              value={toolArgs}
                              onChange={(event) => setToolArgs(event.target.value)}
                              className="input-field min-h-[90px] py-2 text-xs font-mono resize-none"
                            />
                          </div>
                        </>
                      )}
                      {editActionType === 'http_request' && (
                        <>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Target URL</p>
                            <input
                              value={httpUrl}
                              onChange={(event) => setHttpUrl(event.target.value)}
                              className="input-field h-9 font-mono text-xs"
                            />
                          </div>
                          <div className="space-y-2">
                            <p className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Method</p>
                            <select
                              value={httpMethod}
                              onChange={(event) => setHttpMethod(event.target.value)}
                              className="input-field h-9 text-[10px] font-bold uppercase tracking-wider"
                            >
                              <option>GET</option>
                              <option>POST</option>
                              <option>PUT</option>
                              <option>PATCH</option>
                              <option>DELETE</option>
                            </select>
                          </div>
                        </>
                      )}
                    </div>
                  ) : (
                    <textarea
                      value={editActionConfigText}
                      onChange={(event) => setEditActionConfigText(event.target.value)}
                      className="input-field min-h-[130px] py-2.5 resize-none font-mono text-[11px]"
                    />
                  )}
                </div>
              </div>

              <button
                onClick={saveTrigger}
                disabled={isSaving}
                className="btn-secondary w-full h-10 gap-2 text-sm"
              >
                {isSaving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
                Save Changes
              </button>
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

function readString(source: Record<string, unknown>, keys: string[], fallback: string): string {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return fallback;
}

function readNumber(source: Record<string, unknown>, keys: string[], fallback: number): number {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return fallback;
}

function resolveAgentRoute(actionConfig: Record<string, unknown>): { routeMode: 'main' | 'session'; targetSessionId: string } {
  const hasRouteMode = Object.prototype.hasOwnProperty.call(actionConfig, 'route_mode');
  const routeModeRaw = readString(actionConfig, ['route_mode'], 'main').toLowerCase();
  const routeMode = routeModeRaw === 'session' ? 'session' : 'main';

  const canonicalTarget = readString(actionConfig, ['target_session_id'], '');
  if (routeMode === 'session') {
    const target = canonicalTarget || readString(actionConfig, ['session_id'], '');
    return { routeMode: 'session', targetSessionId: target };
  }
  if (!hasRouteMode) {
    const legacyTarget = readString(actionConfig, ['session_id'], '');
    if (legacyTarget) return { routeMode: 'session', targetSessionId: legacyTarget };
  }
  return { routeMode: 'main', targetSessionId: '' };
}

function buildConfigPayload({
  type,
  useManualConfig,
  configText,
  cronExpr,
  heartbeatInterval,
}: {
  type: string;
  useManualConfig: boolean;
  configText: string;
  cronExpr: string;
  heartbeatInterval: number;
}): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  if (useManualConfig) {
    try {
      const parsed = JSON.parse(configText || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Config must be a JSON object' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch {
      return { ok: false, error: 'Config JSON is invalid' };
    }
  }
  if (type === 'cron') {
    if (!cronExpr.trim()) return { ok: false, error: 'Cron expression is required' };
    return { ok: true, value: { expr: cronExpr.trim() } };
  }
  if (type === 'heartbeat') {
    if (!Number.isFinite(heartbeatInterval) || heartbeatInterval <= 0) {
      return { ok: false, error: 'Heartbeat interval must be positive' };
    }
    return { ok: true, value: { interval_seconds: heartbeatInterval } };
  }
  return { ok: true, value: {} };
}

function buildActionConfigPayload({
  actionType,
  useManualAction,
  actionConfigText,
  agentMsg,
  routeMode,
  targetSessionId,
  toolName,
  toolArgs,
  httpUrl,
  httpMethod,
}: {
  actionType: string;
  useManualAction: boolean;
  actionConfigText: string;
  agentMsg: string;
  routeMode: 'main' | 'session';
  targetSessionId: string;
  toolName: string;
  toolArgs: string;
  httpUrl: string;
  httpMethod: string;
}): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  if (useManualAction) {
    try {
      const parsed = JSON.parse(actionConfigText || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Action config must be a JSON object' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch {
      return { ok: false, error: 'Action config JSON is invalid' };
    }
  }

  if (actionType === 'agent_message') {
    if (!agentMsg.trim()) {
      return { ok: false, error: 'Agent message is required' };
    }
    return {
      ok: true,
      value: {
        message: agentMsg.trim(),
        route_mode: routeMode,
        target_session_id: routeMode === 'session' && targetSessionId ? targetSessionId : null,
      },
    };
  }
  if (actionType === 'tool_call') {
    if (!toolName.trim()) return { ok: false, error: 'Tool name is required' };
    try {
      const parsedArgs = JSON.parse(toolArgs || '{}');
      if (!parsedArgs || typeof parsedArgs !== 'object' || Array.isArray(parsedArgs)) {
        return { ok: false, error: 'Tool arguments must be a JSON object' };
      }
      return { ok: true, value: { name: toolName.trim(), arguments: parsedArgs } };
    } catch {
      return { ok: false, error: 'Tool arguments JSON is invalid' };
    }
  }
  if (actionType === 'http_request') {
    if (!httpUrl.trim()) return { ok: false, error: 'HTTP target URL is required' };
    return { ok: true, value: { url: httpUrl.trim(), method: httpMethod || 'POST' } };
  }
  return { ok: true, value: {} };
}
