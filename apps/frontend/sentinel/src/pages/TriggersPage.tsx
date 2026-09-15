import { Menu, MenuItem } from '@mui/material';
import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  Plus,
  RefreshCw,
  Trash2,
  Clock,
  Zap,
  Activity,
  Filter,
  ChevronDown,
  Check,
  X,
  CheckCircle2,
  Loader2,
  Play,
  History,
  Info,
} from 'lucide-react';
import { notificationPublisher } from '../lib/notifications';

import './triggers-page.css';

import { AppShell } from '../components/AppShell';
import { Logo } from '../components/ui/Logo';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { Toggle } from '../components/ui/Toggle';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import type {
  Session,
  SessionListResponse,
  FireTriggerResponse,
  Trigger,
  TriggerListResponse,
  TriggerLog,
  TriggerLogListResponse,
} from '../types/api';

const notify = notificationPublisher('Triggers');

const triggerTypes = ['cron', 'webhook', 'heartbeat'];
const actionTypes = ['agent_message', 'tool_call', 'http_request'];

interface ModalState {
  open: boolean;
  mode: 'create' | 'edit';
  triggerId?: string;
  name: string;
  type: string;
  actionType: string;
  enabled: boolean;
  cronExpr: string;
  heartbeatInterval: number;
  agentMsg: string;
  targetSessionId: string;
  toolName: string;
  toolArgs: string;
  httpUrl: string;
  httpMethod: string;
  configText: string;
  actionConfigText: string;
  useManualConfig: boolean;
  useManualAction: boolean;
}

const modalDefault: ModalState = {
  open: false,
  mode: 'create',
  triggerId: undefined,
  name: '',
  type: 'cron',
  actionType: 'agent_message',
  enabled: true,
  cronExpr: '0 9 * * *',
  heartbeatInterval: 3600,
  agentMsg: '',
  targetSessionId: '',
  toolName: '',
  toolArgs: '{}',
  httpUrl: '',
  httpMethod: 'POST',
  configText: '{}',
  actionConfigText: '{}',
  useManualConfig: false,
  useManualAction: false,
};

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

function resolveAgentRoute(actionConfig: Record<string, unknown>): { targetSessionId: string } {
  return { targetSessionId: readString(actionConfig, ['target_session_id'], '') };
}

function statusTone(status: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (status === 'fired' || status === 'success') return 'good';
  if (status === 'error' || status === 'failed') return 'danger';
  return 'default';
}

const defaultManualInvokePayload: Record<string, string> = {
  source: 'manual',
  signal: 'force_invocation',
};

function pluralize(value: number, unit: string): string {
  return `${value} ${unit}${value === 1 ? '' : 's'}`;
}

function formatNextRunRelative(nextFireAt: string | null, nowMs: number): string {
  if (!nextFireAt) return 'No next run scheduled';
  const targetMs = Date.parse(nextFireAt);
  if (Number.isNaN(targetMs)) return `Next run: ${formatCompactDate(nextFireAt)}`;

  const diffSeconds = Math.max(0, Math.floor((targetMs - nowMs) / 1000));
  if (diffSeconds <= 5) return 'Next run in a few seconds';
  if (diffSeconds < 60) return `Next run in ${pluralize(diffSeconds, 'sec')}`;

  const totalMinutes = Math.floor(diffSeconds / 60);
  if (totalMinutes < 60) return `Next run in ${pluralize(totalMinutes, 'min')}`;

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (minutes === 0) return `Next run in ${pluralize(hours, 'hour')}`;
  return `Next run in ${pluralize(hours, 'hour')} ${pluralize(minutes, 'min')}`;
}

export function TriggersPage() {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState('all');
  const [filterAnchor, setFilterAnchor] = useState<HTMLButtonElement | null>(null);
  const [enabledOnly, setEnabledOnly] = useState(false);
  const [modal, setModal] = useState<ModalState>(modalDefault);
  const [savingModal, setSavingModal] = useState(false);
  const [isFiring, setIsFiring] = useState(false);
  const [invokePayloadText, setInvokePayloadText] = useState(
    JSON.stringify(defaultManualInvokePayload, null, 2),
  );
  const [modalLogs, setModalLogs] = useState<TriggerLog[]>([]);
  const [modalLogsLoading, setModalLogsLoading] = useState(false);
  const [modalLogOffset, setModalLogOffset] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [firingCardTriggerId, setFiringCardTriggerId] = useState<string | null>(null);

  const visible = useMemo(
    () =>
      triggers.filter((trigger) => {
        if (typeFilter !== 'all' && trigger.type !== typeFilter) return false;
        if (enabledOnly && !trigger.enabled) return false;
        return true;
      }),
    [triggers, typeFilter, enabledOnly],
  );

  const routeSessions = useMemo(
    () => sessions.filter((session) => !session.parent_session_id),
    [sessions],
  );

  const isRouteTargetMissing = useMemo(
    () =>
      modal.actionType === 'agent_message'
      && Boolean(modal.targetSessionId)
      && !routeSessions.some((session) => session.id === modal.targetSessionId),
    [modal.actionType, modal.targetSessionId, routeSessions],
  );

  useEffect(() => {
    void loadData();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  async function loadData() {
    setLoading(true);
    try {
      const [triggerPayload, sessionPayload] = await Promise.all([
        api.get<TriggerListResponse>('/triggers?limit=100&offset=0'),
        api.get<SessionListResponse>('/sessions?limit=300&offset=0'),
      ]);
      setTriggers(triggerPayload.items);
      setSessions(sessionPayload.items.filter((session) => !session.parent_session_id));
    } catch {
      notify.error('Failed to load triggers');
    } finally {
      setLoading(false);
    }
  }

  function buildConfig(): Record<string, unknown> {
    if (modal.useManualConfig) {
      try {
        const parsed = JSON.parse(modal.configText);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
      } catch {
        return {};
      }
      return {};
    }
    if (modal.type === 'cron') return { expr: modal.cronExpr };
    if (modal.type === 'heartbeat') return { interval_seconds: modal.heartbeatInterval };
    return {};
  }

  function buildActionConfig(): Record<string, unknown> {
    if (modal.useManualAction) {
      try {
        const parsed = JSON.parse(modal.actionConfigText);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
      } catch {
        return {};
      }
      return {};
    }
    if (modal.actionType === 'agent_message') {
      return {
        message: modal.agentMsg,
        target_session_id: modal.targetSessionId || null,
      };
    }
    if (modal.actionType === 'tool_call') {
      try {
        const args = JSON.parse(modal.toolArgs);
        return { name: modal.toolName, arguments: args };
      } catch {
        return { name: modal.toolName, arguments: {} };
      }
    }
    if (modal.actionType === 'http_request') {
      return { url: modal.httpUrl, method: modal.httpMethod };
    }
    return {};
  }

  async function handleModalSubmit(event: FormEvent) {
    event.preventDefault();
    if (!modal.name.trim() || savingModal) return;
    if (!modal.useManualAction && modal.actionType === 'agent_message' && !modal.targetSessionId) {
      notify.error('Choose a target session');
      return;
    }

    const config = buildConfig();
    const action_config = buildActionConfig();

    setSavingModal(true);
    try {
      if (modal.mode === 'create') {
        const created = await api.post<Trigger>('/triggers', {
          name: modal.name.trim(),
          type: modal.type,
          config,
          action_type: modal.actionType,
          action_config,
          enabled: modal.enabled,
        });
        setTriggers((current) => [created, ...current]);
        notify.success('Autonomous trigger established');
        closeModal();
      } else if (modal.triggerId) {
        const updated = await api.patch<Trigger>(`/triggers/${modal.triggerId}`, {
          name: modal.name.trim(),
          type: modal.type,
          config,
          action_type: modal.actionType,
          action_config,
          enabled: modal.enabled,
        });
        setTriggers((current) => current.map((item) => (item.id === modal.triggerId ? updated : item)));
        notify.success('Trigger configuration updated');
      }
    } catch {
      notify.error(modal.mode === 'create' ? 'Failed to establish trigger' : 'Failed to update trigger');
    } finally {
      setSavingModal(false);
    }
  }

  function openCreateModal() {
    setInvokePayloadText(JSON.stringify(defaultManualInvokePayload, null, 2));
    setModalLogs([]);
    setModalLogOffset(0);
    setModal({
      ...modalDefault,
      open: true,
      mode: 'create',
      targetSessionId: routeSessions[0]?.id ?? '',
    });
  }

  function openEditModal(trigger: Trigger) {
    const isCron = trigger.type === 'cron';
    const isHeartbeat = trigger.type === 'heartbeat';
    const isAgentMsg = trigger.action_type === 'agent_message';
    const isToolCall = trigger.action_type === 'tool_call';
    const isHttp = trigger.action_type === 'http_request';
    const route = resolveAgentRoute(trigger.action_config);

    setInvokePayloadText(JSON.stringify(defaultManualInvokePayload, null, 2));
    setModal({
      open: true,
      mode: 'edit',
      triggerId: trigger.id,
      name: trigger.name,
      type: trigger.type,
      actionType: trigger.action_type,
      enabled: trigger.enabled,
      cronExpr: isCron ? readString(trigger.config, ['expr', 'cron'], '0 9 * * *') : '0 9 * * *',
      heartbeatInterval: isHeartbeat ? readNumber(trigger.config, ['interval_seconds', 'interval'], 3600) : 3600,
      agentMsg: isAgentMsg ? readString(trigger.action_config, ['message'], '') : '',
      targetSessionId: isAgentMsg ? route.targetSessionId : '',
      toolName: isToolCall ? readString(trigger.action_config, ['name', 'tool_name'], '') : '',
      toolArgs: isToolCall ? JSON.stringify(trigger.action_config.arguments ?? trigger.action_config.payload ?? {}, null, 2) : '{}',
      httpUrl: isHttp ? readString(trigger.action_config, ['url'], '') : '',
      httpMethod: isHttp ? readString(trigger.action_config, ['method'], 'POST') : 'POST',
      configText: JSON.stringify(trigger.config, null, 2),
      actionConfigText: JSON.stringify(trigger.action_config, null, 2),
      useManualConfig: false,
      useManualAction: false,
    });
    void loadModalLogs(trigger.id, true);
  }

  function closeModal() {
    setModal(modalDefault);
    setModalLogs([]);
    setModalLogOffset(0);
    setModalLogsLoading(false);
    setSavingModal(false);
    setIsFiring(false);
  }

  async function loadModalLogs(triggerId: string, reset: boolean) {
    setModalLogsLoading(true);
    try {
      const offset = reset ? 0 : modalLogOffset;
      const payload = await api.get<TriggerLogListResponse>(`/triggers/${triggerId}/logs?limit=10&offset=${offset}`);
      if (reset) {
        setModalLogs(payload.items);
        setModalLogOffset(payload.items.length);
      } else {
        setModalLogs((current) => [...current, ...payload.items]);
        setModalLogOffset((current) => current + payload.items.length);
      }
    } catch {
      notify.error('Failed to load trigger logs');
    } finally {
      setModalLogsLoading(false);
    }
  }

  async function fireFromModal() {
    if (modal.mode !== 'edit' || !modal.triggerId || isFiring) return;

    let parsedPayload: Record<string, unknown>;
    try {
      const parsed = JSON.parse(invokePayloadText || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        notify.error('Signal payload must be a JSON object');
        return;
      }
      parsedPayload = parsed as Record<string, unknown>;
    } catch {
      notify.error('Signal payload is not valid JSON');
      return;
    }

    setIsFiring(true);
    try {
      const result = await api.post<FireTriggerResponse>(`/triggers/${modal.triggerId}/fire`, {
        input_payload: parsedPayload,
      });
      if (result.log.status === 'failed') {
        notify.error(result.log.error_message || 'Invocation failed');
        await Promise.all([loadModalLogs(modal.triggerId, true), loadData()]);
        return;
      }
      notify.success('Trigger invoked');
      await Promise.all([loadModalLogs(modal.triggerId, true), loadData()]);
    } catch {
      notify.error('Trigger invocation failed');
    } finally {
      setIsFiring(false);
    }
  }

  async function fireFromCard(triggerId: string) {
    if (firingCardTriggerId) return;
    setFiringCardTriggerId(triggerId);
    try {
      const result = await api.post<FireTriggerResponse>(`/triggers/${triggerId}/fire`, {
        input_payload: defaultManualInvokePayload,
      });
      if (result.log.status === 'failed') {
        notify.error(result.log.error_message || 'Invocation failed');
        await loadData();
        return;
      }
      notify.success('Trigger invoked');
      await loadData();
    } catch {
      notify.error('Trigger invocation failed');
    } finally {
      setFiringCardTriggerId(null);
    }
  }

  async function removeTrigger(trigger: Trigger) {
    if (!window.confirm(`Permanently de-list trigger "${trigger.name}"?`)) return;
    try {
      await api.delete<{ status: string }>(`/triggers/${trigger.id}`);
      setTriggers((current) => current.filter((item) => item.id !== trigger.id));
      notify.success('Trigger decommissioned');
      if (modal.mode === 'edit' && modal.triggerId === trigger.id) {
        closeModal();
      }
    } catch {
      notify.error('Failed to decommission');
    }
  }

  async function toggleTrigger(trigger: Trigger) {
    const nextState = !trigger.enabled;
    try {
      const updated = await api.patch<Trigger>(`/triggers/${trigger.id}`, {
        enabled: nextState,
      });
      setTriggers((current) => current.map((item) => (item.id === trigger.id ? updated : item)));
      if (modal.mode === 'edit' && modal.triggerId === trigger.id) {
        setModal((prev) => ({ ...prev, enabled: updated.enabled }));
      }
      notify.success(`Trigger ${nextState ? 'enabled' : 'disabled'}`);
    } catch {
      notify.error('Failed to update trigger state');
    }
  }

  return (
    <AppShell
      title="Triggers"
      contentClassName="triggers-pane"
      actions={
        <div className="chat-header-actions triggers-actions flex items-center gap-2">
          <button type="button" className="chat-header-pill inline-flex items-center uppercase" aria-label="Trigger type" aria-haspopup="menu" aria-expanded={Boolean(filterAnchor)} onClick={event => setFilterAnchor(event.currentTarget)}>
            <Filter size={13} /><span>{typeFilter === 'all' ? 'All types' : typeFilter}</span><ChevronDown size={12} />
          </button>
          <button className="chat-header-pill inline-flex items-center uppercase" aria-pressed={enabledOnly} onClick={() => setEnabledOnly(!enabledOnly)}>
            <Activity size={13} /> Enabled only
          </button>
          <button onClick={() => void loadData()} disabled={loading} aria-label="Refresh triggers" className="chat-header-pill inline-flex items-center">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      }
    >
      <Menu open={Boolean(filterAnchor)} anchorEl={filterAnchor} onClose={() => setFilterAnchor(null)} disableScrollLock data-pane-menu sx={{ zIndex:10001 }} slotProps={{ paper: { className:'triggers-filter-menu' } }}>
        {['all', ...triggerTypes].map(type => <MenuItem key={type} selected={typeFilter === type} onClick={() => { setTypeFilter(type); setFilterAnchor(null); }}><span>{type === 'all' ? 'All types' : type}</span>{typeFilter === type && <Check size={13} />}</MenuItem>)}
      </Menu>
      <div className="triggers-content">
        <div className="triggers-list-controls"><p className="triggers-count">{visible.length} {visible.length === 1 ? 'trigger' : 'triggers'}{typeFilter !== 'all' || enabledOnly ? ' matching filters' : ''} · {triggers.filter((trigger) => trigger.enabled).length} enabled</p><button type="button" className="triggers-add-button" onClick={openCreateModal} title="New trigger" aria-label="New trigger"><Plus size={16} /></button></div>

        {loading ? (
          <div className="py-20 flex justify-center">
            <Loader2 size={32} className="animate-spin text-(--text-muted)" />
          </div>
        ) : visible.length === 0 ? (
          <div className="triggers-empty">
            <Logo size={48} />
            <p className="text-sm font-bold uppercase tracking-widest">{triggers.length ? 'No matching triggers' : 'No triggers yet'}</p>
          </div>
        ) : (
          <div className="triggers-grid">
            {visible.map((trigger) => (
              <Panel
                key={trigger.id}
                role="button"
                tabIndex={0}
                onClick={() => openEditModal(trigger)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    openEditModal(trigger);
                  }
                }}
                className="trigger-card group flex flex-col gap-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 space-y-1">
                    <p className="trigger-name text-sm font-bold transition-colors text-left group-hover:text-(--accent-solid)">
                      {trigger.name}
                    </p>
                    <div className="flex items-center gap-2">
                      <StatusChip label={trigger.type} tone="info" className="scale-90 origin-left" />
                      <StatusChip label={trigger.action_type} className="scale-90 origin-left opacity-70" />
                    </div>
                  </div>
                  <div onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                    <Toggle enabled={trigger.enabled} onChange={() => toggleTrigger(trigger)} />
                  </div>
                </div>

                <div className="trigger-metrics grid grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-(--text-muted) uppercase tracking-widest flex items-center gap-1.5">
                      <Zap size={10} /> Invocations
                    </p>
                    <p className="text-sm font-mono font-bold">{trigger.fire_count}</p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-(--text-muted) uppercase tracking-widest flex items-center gap-1.5">
                      <Activity size={10} /> Errors
                    </p>
                    <p className={`text-sm font-mono font-medium ${trigger.error_count ? 'text-rose-400' : 'text-(--text-secondary)'}`}>{trigger.error_count}</p>
                  </div>
                </div>

                <div className="trigger-card-footer flex items-end justify-between gap-3">
                  <div className="space-y-1">
                    <p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-wider flex items-center gap-1.5">
                      <Clock size={12} />
                      {trigger.last_fired_at ? `Last run: ${formatCompactDate(trigger.last_fired_at)}` : 'Never invoked'}
                    </p>
                    <p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-wider">
                      {formatNextRunRelative(trigger.next_fire_at, nowMs)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                   <button
                     onClick={() => void fireFromCard(trigger.id)}
                     disabled={firingCardTriggerId === trigger.id}
                     className="inline-flex h-8 items-center gap-1.5 rounded-full border border-(--border-subtle) bg-(--surface-0) px-3 text-[10px] font-bold uppercase tracking-widest text-(--text-secondary) transition-all hover:bg-(--surface-1) hover:text-(--text-primary) hover:border-(--border-strong) active:scale-95 disabled:opacity-40 shadow-xs"
                     title="Run now"
                   >
                     {firingCardTriggerId === trigger.id ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} fill="currentColor" className="text-emerald-500/80" />}
                     Run
                   </button>
                   <button
                     onClick={() => void removeTrigger(trigger)}
                     className="p-2 rounded-full hover:bg-rose-500/10 text-rose-500 transition-all active:scale-95"
                     title="Delete trigger" aria-label={`Delete ${trigger.name}`}
                   >
                     <Trash2 size={16} />
                   </button>
                  </div>                </div>
              </Panel>
            ))}
          </div>
        )}
      </div>

      {modal.open && (
        <div className="triggers-modal fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-xs" onClick={closeModal} />
          <Panel role="dialog" aria-modal="true" aria-label={modal.mode === 'create' ? 'Create trigger' : 'Edit trigger'} className="trigger-dialog relative flex w-full max-w-4xl h-[84vh] flex-col overflow-hidden bg-(--surface-0) shadow-2xl animate-in zoom-in-95 duration-200">
            <form onSubmit={handleModalSubmit} className="flex h-full min-h-0 flex-col">
              {/* Modal Header */}
              <div className="flex items-center justify-between border-b border-(--border-subtle) bg-(--surface-1) px-5 py-3">
                <div className="flex items-center gap-4">
                  <div className="p-2 rounded-xl bg-(--surface-2) text-(--accent-solid) shadow-xs">
                    {modal.mode === 'create' ? <Plus size={18} /> : <History size={18} />}
                  </div>
                  <div>
                    <h2 className="text-[11px] font-bold uppercase tracking-[0.2em] text-(--text-muted) leading-none mb-1.5">
                      {modal.mode === 'create' ? 'Create trigger' : 'Edit trigger'}
                    </h2>
                    <p className="text-sm font-bold text-(--text-primary)">
                      {modal.mode === 'create' ? 'New automation' : modal.name || 'Edit Trigger'}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-6">
                  <div className="flex items-center gap-3 pr-6 border-r border-(--border-subtle)">
                    <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Status</span>
                    <Toggle
                      enabled={modal.enabled}
                      onChange={(enabled) => setModal((prev) => ({ ...prev, enabled }))}
                    />
                  </div>
                  <button type="button" onClick={closeModal} aria-label="Close trigger editor" className="text-(--text-muted) hover:text-(--text-primary) transition-colors">
                    <X size={22} />
                  </button>
                </div>
              </div>

              {/* Modal Body */}
              <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5 space-y-6">
                {/* Details */}
                <section className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Info size={14} className="text-(--text-muted)" />
                    <h3 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Details</h3>
                  </div>
                  <div className="grid gap-4">
                    <div className="space-y-2">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Identifier / Name</label>
                      <input
                        className="input-field h-11 text-sm font-medium"
                        placeholder="e.g. Daily Inventory Synchronization"
                        value={modal.name}
                        onChange={(event) => setModal((prev) => ({ ...prev, name: event.target.value }))}
                        required
                        autoFocus
                      />
                    </div>
                  </div>
                </section>

                {/* Trigger & Action Configuration */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* Trigger Configuration */}
                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Clock size={14} className="text-(--text-muted)" />
                        <h3 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Entry Point</h3>
                      </div>
                      <button
                        type="button"
                        onClick={() => setModal((prev) => ({ ...prev, useManualConfig: !prev.useManualConfig }))}
                        className={`text-[9px] font-bold uppercase tracking-[0.15em] px-2.5 py-1 rounded border transition-all ${
                          modal.useManualConfig
                            ? 'bg-(--accent-solid) text-(--app-bg) border-transparent shadow-xs'
                            : 'bg-transparent text-(--text-muted) border-(--border-subtle) hover:border-(--border-strong)'
                        }`}
                      >
                        {modal.useManualConfig ? 'Switch to Assisted' : 'Manual JSON'}
                      </button>
                    </div>

                    <div className="p-4 rounded-2xl border border-(--border-subtle) bg-(--surface-1)/50 space-y-4">
                      {!modal.useManualConfig ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Protocol Type</label>
                            <select
                              className="input-field h-10 text-xs font-bold uppercase tracking-wider"
                              value={modal.type}
                              onChange={(event) => {
                                const nextType = event.target.value;
                                setModal((prev) => ({
                                  ...prev,
                                  type: nextType,
                                  cronExpr: nextType === 'cron' ? prev.cronExpr || '0 9 * * *' : prev.cronExpr,
                                  heartbeatInterval: nextType === 'heartbeat' ? prev.heartbeatInterval || 3600 : prev.heartbeatInterval,
                                }));
                              }}
                            >
                              {triggerTypes.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
                            </select>
                          </div>

                          {modal.type === 'cron' && (
                            <div className="space-y-2 animate-in fade-in slide-in-from-top-1 duration-200">
                              <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Cron Expression</label>
                              <input
                                className="input-field h-10 font-mono text-xs tracking-wider"
                                value={modal.cronExpr}
                                onChange={(event) => setModal((prev) => ({ ...prev, cronExpr: event.target.value }))}
                              />
                              <p className="text-[9px] text-(--text-muted) font-medium">Standard crontab format (e.g., "0 9 * * *" for daily at 9 AM)</p>
                            </div>
                          )}

                          {modal.type === 'heartbeat' && (
                            <div className="space-y-2 animate-in fade-in slide-in-from-top-1 duration-200">
                              <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Frequency (Seconds)</label>
                              <input
                                type="number"
                                min={1}
                                className="input-field h-10 font-mono text-xs"
                                value={modal.heartbeatInterval}
                                onChange={(event) => setModal((prev) => ({ ...prev, heartbeatInterval: Number.parseInt(event.target.value, 10) || 1 }))}
                              />
                            </div>
                          )}

                          {modal.type === 'webhook' && (
                            <div className="py-4 px-4 rounded-xl border border-dashed border-(--border-subtle) bg-(--surface-0)">
                              <p className="text-[11px] text-(--text-muted) text-center font-medium leading-relaxed">
                                Assisted configuration is not available for <span className="text-(--text-primary) font-bold">{modal.type.toUpperCase()}</span>.
                                <br />Please use <span className="text-(--accent-solid) font-bold">Manual JSON</span> mode.
                              </p>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="space-y-2 animate-in fade-in duration-200">
                          <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Raw Configuration</label>
                          <textarea
                            className="input-field min-h-[160px] resize-none py-3 font-mono text-[11px] leading-relaxed"
                            placeholder='{ "key": "value" }'
                            value={modal.configText}
                            onChange={(event) => setModal((prev) => ({ ...prev, configText: event.target.value }))}
                          />
                        </div>
                      )}
                    </div>
                  </section>

                  {/* Action Configuration */}
                  <section className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Zap size={14} className="text-(--text-muted)" />
                        <h3 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Execution Action</h3>
                      </div>
                      <button
                        type="button"
                        onClick={() => setModal((prev) => ({ ...prev, useManualAction: !prev.useManualAction }))}
                        className={`text-[9px] font-bold uppercase tracking-[0.15em] px-2.5 py-1 rounded border transition-all ${
                          modal.useManualAction
                            ? 'bg-(--accent-solid) text-(--app-bg) border-transparent shadow-xs'
                            : 'bg-transparent text-(--text-muted) border-(--border-subtle) hover:border-(--border-strong)'
                        }`}
                      >
                        {modal.useManualAction ? 'Switch to Assisted' : 'Manual JSON'}
                      </button>
                    </div>

                    <div className="p-4 rounded-2xl border border-(--border-subtle) bg-(--surface-1)/50 space-y-4">
                      {!modal.useManualAction ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Action Protocol</label>
                            <select
                              className="input-field h-10 text-xs font-bold uppercase tracking-wider"
                              value={modal.actionType}
                              onChange={(event) => {
                                const nextActionType = event.target.value;
                                setModal((prev) => ({
                                  ...prev,
                                  actionType: nextActionType,
                                  agentMsg: nextActionType === 'agent_message' ? prev.agentMsg : '',
                                  toolName: nextActionType === 'tool_call' ? prev.toolName : '',
                                  toolArgs: nextActionType === 'tool_call' ? prev.toolArgs : '{}',
                                  httpUrl: nextActionType === 'http_request' ? prev.httpUrl : '',
                                  httpMethod: nextActionType === 'http_request' ? prev.httpMethod : 'POST',
                                }));
                              }}
                            >
                              {actionTypes.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
                            </select>
                          </div>

                          {modal.actionType === 'agent_message' && (
                            <div className="space-y-5 animate-in fade-in slide-in-from-top-1 duration-200">
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">System Instruction</label>
                                <textarea
                                  className="input-field min-h-[92px] resize-none py-3 text-xs leading-relaxed"
                                  placeholder="Describe what the agent should do when triggered..."
                                  value={modal.agentMsg}
                                  onChange={(event) => setModal((prev) => ({ ...prev, agentMsg: event.target.value }))}
                                />
                              </div>
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Target Session</label>
                                <select
                                  className="input-field h-10 text-xs font-bold"
                                  value={modal.targetSessionId}
                                  onChange={(event) => setModal((prev) => ({ ...prev, targetSessionId: event.target.value }))}
                                >
                                  {!modal.targetSessionId && <option value="">Select session...</option>}
                                  {routeSessions.map((session) => (
                                    <option key={session.id} value={session.id}>
                                      {session.title || session.id.slice(0, 8)}
                                    </option>
                                  ))}
                                  {isRouteTargetMissing && (
                                    <option value={modal.targetSessionId}>
                                      Missing ({modal.targetSessionId.slice(0, 8)})
                                    </option>
                                  )}
                                </select>
                              </div>
                            </div>
                          )}

                          {modal.actionType === 'tool_call' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Tool Identifier</label>
                                <input
                                  className="input-field h-10 font-mono text-xs"
                                  placeholder="e.g. web_search"
                                  value={modal.toolName}
                                  onChange={(event) => setModal((prev) => ({ ...prev, toolName: event.target.value }))}
                                />
                              </div>
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Arguments (JSON Object)</label>
                                <textarea
                                  className="input-field min-h-[100px] resize-none py-3 font-mono text-[11px]"
                                  placeholder='{ "query": "..." }'
                                  value={modal.toolArgs}
                                  onChange={(event) => setModal((prev) => ({ ...prev, toolArgs: event.target.value }))}
                                />
                              </div>
                            </div>
                          )}

                          {modal.actionType === 'http_request' && (
                            <div className="space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Endpoint URL</label>
                                <input
                                  className="input-field h-10 font-mono text-xs"
                                  placeholder="https://api.example.com/webhook"
                                  value={modal.httpUrl}
                                  onChange={(event) => setModal((prev) => ({ ...prev, httpUrl: event.target.value }))}
                                />
                              </div>
                              <div className="space-y-2">
                                <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">HTTP Method</label>
                                <select
                                  className="input-field h-10 text-xs font-bold"
                                  value={modal.httpMethod}
                                  onChange={(event) => setModal((prev) => ({ ...prev, httpMethod: event.target.value }))}
                                >
                                  <option>GET</option>
                                  <option>POST</option>
                                  <option>PUT</option>
                                  <option>PATCH</option>
                                  <option>DELETE</option>
                                </select>
                              </div>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="space-y-2 animate-in fade-in duration-200">
                          <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Raw Action Payload</label>
                          <textarea
                            className="input-field min-h-[160px] resize-none py-3 font-mono text-[11px] leading-relaxed"
                            placeholder='{ "action": "..." }'
                            value={modal.actionConfigText}
                            onChange={(event) => setModal((prev) => ({ ...prev, actionConfigText: event.target.value }))}
                          />
                        </div>
                      )}
                    </div>
                  </section>
                </div>

                {/* Edit-Only: Execution & Logs */}
                {modal.mode === 'edit' && (
                  <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-6 pt-6 border-t border-(--border-subtle)">
                    {/* Manual Invocation */}
                    <section className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Play size={14} className="text-(--text-muted)" />
                          <h3 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Run manually</h3>
                        </div>
                        <button
                          type="button"
                          onClick={fireFromModal}
                          disabled={isFiring}
                          className="btn-primary h-8 px-4 text-[10px] font-bold uppercase tracking-widest gap-2 shadow-xs"
                        >
                          {isFiring ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} fill="currentColor" />}
                          Run now
                        </button>
                      </div>
                      <div className="p-4 rounded-2xl border border-(--border-subtle) bg-(--surface-1)/50 space-y-2">
                        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Input Payload (JSON)</label>
                        <textarea
                          value={invokePayloadText}
                          onChange={(event) => setInvokePayloadText(event.target.value)}
                          className="input-field min-h-[120px] resize-none py-2.5 font-mono text-[11px] leading-relaxed"
                        />
                      </div>
                    </section>

                    {/* Telemetry Logs */}
                    <section className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <History size={14} className="text-(--text-muted)" />
                          <h3 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Run history</h3>
                        </div>
                        {modal.triggerId && (
                          <button
                            type="button"
                            onClick={() => void loadModalLogs(modal.triggerId as string, true)}
                            className="text-[9px] font-bold uppercase tracking-widest flex items-center gap-1.5 text-(--text-muted) hover:text-(--text-primary) transition-colors"
                          >
                            <RefreshCw size={12} className={modalLogsLoading ? 'animate-spin' : ''} />
                            Refresh
                          </button>
                        )}
                      </div>
                      <div className="p-4 rounded-2xl border border-(--border-subtle) bg-(--surface-1)/50 h-[200px] flex flex-col">
                        <div className="flex-1 overflow-y-auto pr-2 space-y-3 custom-scrollbar">
                          {modalLogsLoading && modalLogs.length === 0 ? (
                            <div className="flex items-center justify-center h-full gap-2 text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                              <Loader2 size={16} className="animate-spin" />
                              Synchronizing...
                            </div>
                          ) : modalLogs.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-full opacity-30 gap-3">
                              <Activity size={32} strokeWidth={1} />
                              <p className="text-[10px] font-bold uppercase tracking-widest text-center">No telemetry data available</p>
                            </div>
                          ) : (
                            modalLogs.map((log) => (
                              <div key={log.id} className="group p-3 rounded-xl border border-(--border-subtle) bg-(--surface-0) hover:bg-(--surface-1) transition-all">
                                <div className="flex items-center justify-between mb-2">
                                  <StatusChip label={log.status} tone={statusTone(log.status)} className="scale-90 origin-left" />
                                  <span className="font-mono text-[9px] font-bold text-(--text-muted) group-hover:text-(--text-secondary) transition-colors">{formatCompactDate(log.fired_at)}</span>
                                </div>
                                {log.error_message ? (
                                  <p className="text-[11px] text-rose-500 font-medium leading-relaxed">{log.error_message}</p>
                                ) : (
                                  <p className="text-[11px] text-(--text-secondary) leading-relaxed line-clamp-2 italic">
                                    {log.output_summary || 'No output summary provided.'}
                                  </p>
                                )}
                              </div>
                            ))
                          )}
                        </div>
                        {!modalLogsLoading && modal.triggerId && modalLogs.length >= 10 && (
                          <button
                            type="button"
                            onClick={() => void loadModalLogs(modal.triggerId as string, false)}
                            className="mt-4 btn-secondary h-8 px-4 text-[10px] font-bold uppercase tracking-widest gap-2"
                          >
                            <History size={12} />
                            Load older runs
                          </button>
                        )}
                      </div>
                    </section>
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="flex items-center justify-between border-t border-(--border-subtle) bg-(--surface-1) px-5 py-3">
                <div className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                  {modal.mode === 'create' ? 'Ready for deployment' : `Node ID: ${modal.triggerId?.slice(0, 12)}...`}
                </div>
                <div className="flex items-center gap-3">
                  <button type="button" onClick={closeModal} className="btn-secondary h-10 px-5 text-[11px] font-bold uppercase tracking-widest">
                    Cancel
                  </button>
                  <button type="submit" disabled={savingModal} className="btn-primary h-10 px-6 text-[11px] font-bold uppercase tracking-widest gap-2 shadow-lg shadow-black/5">
                    {savingModal ? <Loader2 size={18} className="animate-spin" /> : <CheckCircle2 size={18} />}
                    {modal.mode === 'create' ? 'Create trigger' : 'Save changes'}
                  </button>
                </div>
              </div>
            </form>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
