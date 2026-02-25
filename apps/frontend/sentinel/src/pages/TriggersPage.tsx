import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  Plus,
  RefreshCw,
  Trash2,
  Settings2,
  Clock,
  Zap,
  Activity,
  Filter,
  X,
  Save,
  CheckCircle2,
  Loader2,
  Terminal,
  Globe,
  Bell,
  Wrench,
  Link,
  Edit3,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { Toggle } from '../components/ui/Toggle';
import { Logo } from '../components/ui/Logo';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import type { Trigger, TriggerListResponse } from '../types/api';

const triggerTypes = ['cron', 'webhook', 'heartbeat', 'event'];
const actionTypes = ['agent_message', 'tool_call', 'http_request'];

interface ModalState {
  open: boolean;
  mode: 'create' | 'edit';
  triggerId?: string;
  name: string;
  type: string;
  actionType: string;
  // Assisted fields for config
  cronExpr: string;
  heartbeatInterval: number;
  // Assisted fields for actions
  agentMsg: string;
  toolName: string;
  toolArgs: string;
  httpUrl: string;
  httpMethod: string;
  // Fallbacks
  configText: string;
  actionConfigText: string;
  useManualConfig: boolean;
  useManualAction: boolean;
}

const modalDefault: ModalState = {
  open: false,
  mode: 'create',
  name: '',
  type: 'cron',
  actionType: 'agent_message',
  cronExpr: '0 9 * * *',
  heartbeatInterval: 3600,
  agentMsg: '',
  toolName: '',
  toolArgs: '{}',
  httpUrl: '',
  httpMethod: 'POST',
  configText: '{}',
  actionConfigText: '{}',
  useManualConfig: false,
  useManualAction: false,
};

export function TriggersPage() {
  const navigate = useNavigate();
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState('all');
  const [enabledOnly, setEnabledOnly] = useState(false);
  const [modal, setModal] = useState<ModalState>(modalDefault);

  const visible = useMemo(
    () =>
      triggers.filter((trigger) => {
        if (typeFilter !== 'all' && trigger.type !== typeFilter) return false;
        if (enabledOnly && !trigger.enabled) return false;
        return true;
      }),
    [triggers, typeFilter, enabledOnly],
  );

  useEffect(() => {
    void loadTriggers();
  }, []);

  async function loadTriggers() {
    setLoading(true);
    try {
      const payload = await api.get<TriggerListResponse>('/triggers?limit=100&offset=0');
      setTriggers(payload.items);
    } catch { toast.error('Failed to load triggers'); }
    finally { setLoading(false); }
  }

  function buildConfig(): Record<string, any> {
    if (modal.useManualConfig) {
      try { return JSON.parse(modal.configText); } catch { return {}; }
    }
    if (modal.type === 'cron') return { expr: modal.cronExpr };
    if (modal.type === 'heartbeat') return { interval_seconds: modal.heartbeatInterval };
    return {};
  }

  function buildActionConfig(): Record<string, any> {
    if (modal.useManualAction) {
      try { return JSON.parse(modal.actionConfigText); } catch { return {}; }
    }
    if (modal.actionType === 'agent_message') return { message: modal.agentMsg };
    if (modal.actionType === 'tool_call') {
      try {
        return { name: modal.toolName, arguments: JSON.parse(modal.toolArgs) };
      } catch {
        return { name: modal.toolName, arguments: {} };
      }
    }
    if (modal.actionType === 'http_request') return { url: modal.httpUrl, method: modal.httpMethod };
    return {};
  }

  async function handleModalSubmit(event: FormEvent) {
    event.preventDefault();
    if (!modal.name.trim()) return;

    const config = buildConfig();
    const action_config = buildActionConfig();

    try {
      if (modal.mode === 'create') {
        const created = await api.post<Trigger>('/triggers', {
          name: modal.name.trim(),
          type: modal.type,
          config,
          action_type: modal.actionType,
          action_config,
          enabled: true,
        });
        setTriggers((current) => [created, ...current]);
        toast.success('Autonomous trigger established');
      } else {
        const updated = await api.patch<Trigger>(`/triggers/${modal.triggerId}`, {
          name: modal.name.trim(),
          config,
          action_config,
        });
        setTriggers((current) => current.map(t => t.id === modal.triggerId ? updated : t));
        toast.success('Trigger configuration updated');
      }
      setModal(modalDefault);
    } catch { 
      toast.error(modal.mode === 'create' ? 'Failed to establish trigger' : 'Failed to update trigger'); 
    }
  }

  function openEditModal(trigger: Trigger) {
    // Attempt to deconstruct config for assisted fields
    const isCron = trigger.type === 'cron';
    const isHeartbeat = trigger.type === 'heartbeat';
    const isAgentMsg = trigger.action_type === 'agent_message';
    const isToolCall = trigger.action_type === 'tool_call';
    const isHttp = trigger.action_type === 'http_request';

    setModal({
      open: true,
      mode: 'edit',
      triggerId: trigger.id,
      name: trigger.name,
      type: trigger.type,
      actionType: trigger.action_type,
      cronExpr: isCron ? (trigger.config.expr || trigger.config.cron || '0 9 * * *') : '0 9 * * *',
      heartbeatInterval: isHeartbeat ? (trigger.config.interval_seconds || trigger.config.interval || 3600) : 3600,
      agentMsg: isAgentMsg ? (trigger.action_config.message || '') : '',
      toolName: isToolCall ? (trigger.action_config.name || trigger.action_config.tool_name || '') : '',
      toolArgs: isToolCall ? JSON.stringify(trigger.action_config.arguments || trigger.action_config.payload || {}, null, 2) : '{}',
      httpUrl: isHttp ? (trigger.action_config.url || '') : '',
      httpMethod: isHttp ? (trigger.action_config.method || 'POST') : 'POST',
      configText: JSON.stringify(trigger.config, null, 2),
      actionConfigText: JSON.stringify(trigger.action_config, null, 2),
      useManualConfig: false,
      useManualAction: false,
    });
  }

  async function removeTrigger(trigger: Trigger) {
    if (!window.confirm(`Permanently de-list trigger "${trigger.name}"?`)) return;
    try {
      await api.delete<{ status: string }>(`/triggers/${trigger.id}`);
      setTriggers((current) => current.filter((item) => item.id !== trigger.id));
      toast.success('Trigger decommissioned');
    } catch { toast.error('Failed to decommission'); }
  }

  async function toggleTrigger(trigger: Trigger) {
    const nextState = !trigger.enabled;
    try {
      const updated = await api.patch<Trigger>(`/triggers/${trigger.id}`, {
        enabled: nextState,
      });
      setTriggers((current) =>
        current.map((t) => (t.id === trigger.id ? updated : t))
      );
      toast.success(`Trigger ${nextState ? 'enabled' : 'disabled'}`);
    } catch {
      toast.error('Failed to update trigger state');
    }
  }

  return (
    <AppShell
      title="Triggers"
      subtitle="Autonomous Event Scheduling & Webhooks"
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => void loadTriggers()} className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          </button>
          <div className="h-6 w-px bg-[color:var(--border-subtle)] mx-1" />
          <button onClick={() => setModal({ ...modalDefault, open: true })} className="btn-primary h-9 px-3 text-xs gap-2">
            <Plus size={14} />
            New Automation
          </button>
        </div>
      }
    >
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Filter Bar */}
        <Panel className="p-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4 flex-1">
            <div className="relative min-w-[200px]">
              <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
              <select 
                className="input-field pl-9 h-10 text-xs font-bold uppercase tracking-wider appearance-none"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="all">All Trigger Types</option>
                {triggerTypes.map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}
              </select>
            </div>
            
            <label className="flex items-center gap-2 px-3 py-2 rounded-md hover:bg-[color:var(--surface-1)] cursor-pointer transition-colors border border-transparent hover:border-[color:var(--border-subtle)]">
              <input 
                type="checkbox" 
                className="w-4 h-4 accent-[color:var(--accent-solid)]"
                checked={enabledOnly} 
                onChange={(e) => setEnabledOnly(e.target.checked)} 
              />
              <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-secondary)]">Enabled Only</span>
            </label>
          </div>
          
          <div className="flex items-center gap-2 text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
            <Activity size={14} />
            {visible.length} Active Automations
          </div>
        </Panel>

        {loading ? (
          <div className="py-20 flex justify-center">
            <Loader2 size={32} className="animate-spin text-[color:var(--text-muted)]" />
          </div>
        ) : visible.length === 0 ? (
          <div className="py-20 flex flex-col items-center justify-center opacity-30 gap-4 border-2 border-dashed border-[color:var(--border-subtle)] rounded-2xl">
            <Logo size={48} />
            <p className="text-sm font-bold uppercase tracking-widest">No triggers matched filters</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {visible.map((trigger) => (
              <Panel key={trigger.id} className="p-5 group hover:border-[color:var(--border-strong)] transition-all flex flex-col gap-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <button 
                      onClick={() => navigate(`/triggers/${trigger.id}`)}
                      className="text-sm font-bold hover:text-[color:var(--accent-solid)] transition-colors text-left"
                    >
                      {trigger.name}
                    </button>
                    <div className="flex items-center gap-2">
                      <StatusChip label={trigger.type} tone="info" className="scale-90 origin-left" />
                      <StatusChip label={trigger.action_type} className="scale-90 origin-left opacity-70" />
                    </div>
                  </div>
                  <Toggle 
                    enabled={trigger.enabled} 
                    onChange={() => toggleTrigger(trigger)} 
                  />
                </div>

                <div className="grid grid-cols-2 gap-4 py-2 border-y border-[color:var(--border-subtle)] border-dashed">
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-1.5">
                      <Zap size={10} /> Invocations
                    </p>
                    <p className="text-sm font-mono font-bold">{trigger.fire_count}</p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-[9px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-1.5">
                      <Activity size={10} /> Errors
                    </p>
                    <p className="text-sm font-mono font-bold text-rose-500">{trigger.error_count}</p>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-wider flex items-center gap-1.5">
                    <Clock size={12} />
                    {trigger.last_fired_at ? `Last run: ${formatCompactDate(trigger.last_fired_at)}` : 'Never invoked'}
                  </p>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button 
                      onClick={() => openEditModal(trigger)}
                      className="p-2 rounded-md hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]"
                      title="Edit Configuration"
                    >
                      <Edit3 size={16} />
                    </button>
                    <button 
                      onClick={() => navigate(`/triggers/${trigger.id}`)}
                      className="p-2 rounded-md hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]"
                      title="Execution Logs"
                    >
                      <Settings2 size={16} />
                    </button>
                    <button 
                      onClick={() => void removeTrigger(trigger)}
                      className="p-2 rounded-md hover:bg-rose-500/10 text-rose-500"
                      title="Purge"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </div>

      {/* Automation Modal */}
      {modal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setModal(modalDefault)} />
          <Panel className="relative w-full max-w-2xl bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <form onSubmit={handleModalSubmit}>
              <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)]">
                <div className="flex items-center gap-3">
                  {modal.mode === 'create' ? <Plus size={18} className="text-[color:var(--accent-solid)]" /> : <Edit3 size={18} className="text-[color:var(--accent-solid)]" />}
                  <h2 className="font-bold text-sm uppercase tracking-widest">
                    {modal.mode === 'create' ? 'Initialize Automation' : 'Modify Automation'}
                  </h2>
                </div>
                <button type="button" onClick={() => setModal(modalDefault)} className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                  <X size={20} />
                </button>
              </div>

              <div className="p-6 space-y-6 overflow-y-auto max-h-[70vh]">
                <div className="space-y-2">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Identifier</label>
                  <input 
                    className="input-field h-11 font-bold"
                    placeholder="e.g. Daily Sync Protocol"
                    value={modal.name}
                    onChange={(e) => setModal(prev => ({ ...prev, name: e.target.value }))}
                    required
                  />
                </div>

                <div className="grid grid-cols-2 gap-6">
                  {/* Trigger Section */}
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                        <Clock size={12} /> Entry Point
                      </label>
                      <button 
                        type="button" 
                        className={`text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border transition-colors ${modal.useManualConfig ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent' : 'text-[color:var(--text-muted)] border-[color:var(--border-subtle)]'}`}
                        onClick={() => setModal(p => ({ ...p, useManualConfig: !p.useManualConfig }))}
                      >
                        Manual
                      </button>
                    </div>
                    
                    {!modal.useManualConfig ? (
                      <div className="space-y-3 p-4 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)]">
                        <div className="space-y-2">
                          <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Type</span>
                          <select 
                            className="input-field h-9 text-[10px] font-bold uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                            value={modal.type}
                            onChange={(e) => setModal(prev => ({ ...prev, type: e.target.value }))}
                            disabled={modal.mode === 'edit'}
                          >
                            {triggerTypes.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        </div>

                        {modal.type === 'cron' && (
                          <div className="space-y-2">
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Cron Expression</span>
                            <input 
                              className="input-field h-9 font-mono text-xs"
                              value={modal.cronExpr}
                              onChange={(e) => setModal(p => ({ ...p, cronExpr: e.target.value }))}
                            />
                          </div>
                        )}

                        {modal.type === 'heartbeat' && (
                          <div className="space-y-2">
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Interval (Seconds)</span>
                            <input 
                              type="number"
                              className="input-field h-9 font-mono text-xs"
                              value={modal.heartbeatInterval}
                              onChange={(e) => setModal(p => ({ ...p, heartbeatInterval: parseInt(e.target.value) }))}
                            />
                          </div>
                        )}

                        {(modal.type === 'webhook' || modal.type === 'event') && (
                          <div className="p-3 text-[10px] font-medium text-[color:var(--text-muted)] italic leading-relaxed">
                            No immediate parameters required. Configuration will be refined post-establishment.
                          </div>
                        )}
                      </div>
                    ) : (
                      <textarea 
                        className="input-field min-h-[140px] py-3 resize-none font-mono text-[11px]"
                        placeholder="Raw Config JSON..."
                        value={modal.configText}
                        onChange={(e) => setModal(prev => ({ ...prev, configText: e.target.value }))}
                      />
                    )}
                  </div>

                  {/* Action Section */}
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                        <Zap size={12} /> Execution Action
                      </label>
                      <button 
                        type="button" 
                        className={`text-[9px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border transition-colors ${modal.useManualAction ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent' : 'text-[color:var(--text-muted)] border-[color:var(--border-subtle)]'}`}
                        onClick={() => setModal(p => ({ ...p, useManualAction: !p.useManualAction }))}
                      >
                        Manual
                      </button>
                    </div>

                    {!modal.useManualAction ? (
                      <div className="space-y-3 p-4 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)]">
                        <div className="space-y-2">
                          <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Protocol</span>
                          <select 
                            className="input-field h-9 text-[10px] font-bold uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                            value={modal.actionType}
                            onChange={(e) => setModal(prev => ({ ...prev, actionType: e.target.value }))}
                            disabled={modal.mode === 'edit'}
                          >
                            {actionTypes.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        </div>

                        {modal.actionType === 'agent_message' && (
                          <div className="space-y-2">
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Instruction</span>
                            <textarea 
                              className="input-field min-h-[80px] py-2 text-xs resize-none"
                              placeholder="Message for Sentinel..."
                              value={modal.agentMsg}
                              onChange={(e) => setModal(p => ({ ...p, agentMsg: e.target.value }))}
                            />
                          </div>
                        )}

                        {modal.actionType === 'tool_call' && (
                          <div className="space-y-2">
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Capability Name</span>
                            <input 
                              className="input-field h-9 font-mono text-xs"
                              placeholder="e.g. web_search"
                              value={modal.toolName}
                              onChange={(e) => setModal(p => ({ ...p, toolName: e.target.value }))}
                            />
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Arguments (JSON)</span>
                            <input 
                              className="input-field h-9 font-mono text-xs"
                              value={modal.toolArgs}
                              onChange={(e) => setModal(p => ({ ...p, toolArgs: e.target.value }))}
                            />
                          </div>
                        )}

                        {modal.actionType === 'http_request' && (
                          <div className="space-y-2">
                            <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Target URL</span>
                            <input 
                              className="input-field h-9 font-mono text-xs"
                              placeholder="https://api.example.com/webhook"
                              value={modal.httpUrl}
                              onChange={(e) => setModal(p => ({ ...p, httpUrl: e.target.value }))}
                            />
                            <div className="flex items-center justify-between pt-1">
                               <span className="text-[9px] font-bold uppercase text-[color:var(--text-muted)]">Method</span>
                               <select 
                                 className="bg-transparent text-[10px] font-bold outline-none"
                                 value={modal.httpMethod}
                                 onChange={(e) => setModal(p => ({ ...p, httpMethod: e.target.value }))}
                               >
                                 <option>GET</option>
                                 <option>POST</option>
                                 <option>PUT</option>
                               </select>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <textarea 
                        className="input-field min-h-[140px] py-3 resize-none font-mono text-[11px]"
                        placeholder="Raw Action JSON..."
                        value={modal.actionConfigText}
                        onChange={(e) => setModal(prev => ({ ...prev, actionConfigText: e.target.value }))}
                      />
                    )}
                  </div>
                </div>
              </div>

              <div className="p-6 bg-[color:var(--surface-1)] border-t border-[color:var(--border-subtle)] flex items-center justify-end gap-3">
                <button type="button" onClick={() => setModal(modalDefault)} className="btn-secondary h-11 px-6">Cancel</button>
                <button type="submit" className="btn-primary h-11 px-8 gap-2">
                  <CheckCircle2 size={18} />
                  {modal.mode === 'create' ? 'Establish Automation' : 'Update Configuration'}
                </button>
              </div>
            </form>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
