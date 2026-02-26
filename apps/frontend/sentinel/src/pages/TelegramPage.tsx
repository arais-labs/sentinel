import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  Send, Eye, EyeOff, Check, Loader2, RefreshCw, Play, Square,
  Trash2, MessageCircle, User, Users, Info,
} from 'lucide-react';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';

// ── types ───────────────────────────────────────────────────────────────────

interface TelegramStatus {
  running: boolean;
  bot_username: string | null;
  can_read_all_group_messages?: boolean | null;
  connected_chats: Record<string, {
    chat_id: number;
    chat_type: string;
    title: string;
    connected_at: string;
    user_id?: number;
    user_name?: string;
    username?: string;
  }>;
  token_configured: boolean;
  masked_token: string | null;
  owner_user_id?: string | null;
  owner_chat_id?: string | null;
  owner_telegram_user_id?: string | null;
  target_session_id?: string | null;
}

// ── main page ───────────────────────────────────────────────────────────────

export function TelegramPage() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [selectedOwnerChatId, setSelectedOwnerChatId] = useState('');
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [bindingOwner, setBindingOwner] = useState(false);
  const [clearingOwner, setClearingOwner] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.get<TelegramStatus>('/telegram/status');
      setStatus(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = window.setInterval(fetchStatus, 10_000);
    return () => window.clearInterval(interval);
  }, [fetchStatus]);

  useEffect(() => {
    if (!selectedOwnerChatId && status?.owner_chat_id) {
      setSelectedOwnerChatId(status.owner_chat_id);
    }
  }, [status?.owner_chat_id, selectedOwnerChatId]);

  async function handleSave() {
    if (!token.trim()) return;
    setSaving(true);
    try {
      await api.post('/telegram/configure', { bot_token: token.trim() });
      toast.success('Telegram bot configured and started');
      setToken('');
      await fetchStatus();
    } catch {
      toast.error('Failed to configure Telegram bot');
    } finally {
      setSaving(false);
    }
  }

  async function handleStart() {
    setStarting(true);
    try {
      await api.post('/telegram/start');
      toast.success('Telegram bot started');
      await fetchStatus();
    } catch {
      toast.error('Failed to start Telegram bot');
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    setStopping(true);
    try {
      await api.post('/telegram/stop');
      toast.success('Telegram bot stopped');
      await fetchStatus();
    } catch {
      toast.error('Failed to stop Telegram bot');
    } finally {
      setStopping(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await api.delete('/telegram/configure');
      toast.success('Telegram bot token removed');
      setConfirmDelete(false);
      await fetchStatus();
    } catch {
      toast.error('Failed to remove Telegram bot');
    } finally {
      setDeleting(false);
    }
  }

  async function handleBindOwner() {
    if (!selectedOwnerChatId.trim()) return;
    setBindingOwner(true);
    try {
      await api.post('/telegram/owner', { chat_id: Number(selectedOwnerChatId) });
      toast.success('Owner Telegram identity linked');
      await fetchStatus();
    } catch {
      toast.error('Failed to bind owner Telegram identity');
    } finally {
      setBindingOwner(false);
    }
  }

  async function handleClearOwner() {
    setClearingOwner(true);
    try {
      await api.delete('/telegram/owner');
      toast.success('Owner Telegram identity removed');
      setSelectedOwnerChatId('');
      await fetchStatus();
    } catch {
      toast.error('Failed to remove owner Telegram identity');
    } finally {
      setClearingOwner(false);
    }
  }

  const chats = status?.connected_chats ? Object.values(status.connected_chats) : [];
  const privateChats = chats.filter(chat => chat.chat_type === 'private');

  return (
    <AppShell
      title="Telegram Integration"
      subtitle="Bridge Telegram chats to Sentinel"
    >
      <div className="max-w-3xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">

        {/* Status Panel */}
        <Panel className="p-6 space-y-4">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <Send size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Bot Status</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Telegram bridge connection</p>
            </div>
            <button
              onClick={() => { setLoading(true); fetchStatus(); }}
              className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors p-1"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          {loading && !status ? (
            <div className="flex items-center justify-center py-8 text-[color:var(--text-muted)]">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Status</span>
                <StatusChip
                  label={status?.running ? 'Running' : 'Stopped'}
                  tone={status?.running ? 'good' : 'danger'}
                />
              </div>

              {status?.bot_username && (
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Bot Username</span>
                  <span className="text-xs font-mono font-bold">@{status.bot_username}</span>
                </div>
              )}

              {status?.masked_token && (
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Token</span>
                  <span className="text-[10px] font-mono text-[color:var(--text-muted)]">{status.masked_token}</span>
                </div>
              )}

              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Owner Main Session</span>
                <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                  {status?.target_session_id || 'not set'}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Owner Telegram Chat</span>
                <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                  {status?.owner_chat_id || 'not set'}
                </span>
              </div>
            </div>
          )}
        </Panel>

        {/* Controls Panel */}
        <Panel className="p-6 space-y-4">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--text-primary)]">
              <MessageCircle size={20} />
            </div>
            <div>
              <h2 className="text-sm font-bold uppercase tracking-widest">Configuration</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Manage bot token &amp; connection</p>
            </div>
          </div>

          {/* Token Input */}
          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              Bot Token (from @BotFather)
            </label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showToken ? 'text' : 'password'}
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder={status?.token_configured ? 'Enter new token to update...' : 'Paste your Telegram bot token...'}
                  className="input-field h-10 pr-10 font-mono text-xs w-full"
                  onKeyDown={e => e.key === 'Enter' && handleSave()}
                />
                <button
                  type="button"
                  onClick={() => setShowToken(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]"
                >
                  {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <button
                onClick={handleSave}
                disabled={!token.trim() || saving}
                className="btn-primary h-10 px-4 text-[10px] font-bold uppercase tracking-widest shrink-0"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                Save
              </button>
            </div>
          </div>

          {/* Start / Stop / Delete */}
          {status?.token_configured && (
            <div className="flex items-center gap-2 pt-2">
              {!status.running ? (
                <button
                  onClick={handleStart}
                  disabled={starting}
                  className="btn-primary h-9 px-4 text-xs gap-2"
                >
                  {starting ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  Start Bot
                </button>
              ) : (
                <button
                  onClick={handleStop}
                  disabled={stopping}
                  className="btn-secondary h-9 px-4 text-xs gap-2 text-rose-500 hover:bg-rose-500/10"
                >
                  {stopping ? <Loader2 size={14} className="animate-spin" /> : <Square size={14} />}
                  Stop Bot
                </button>
              )}

              <div className="flex-1" />

              {!confirmDelete ? (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="text-[10px] font-bold uppercase tracking-widest text-rose-500/60 hover:text-rose-500 transition-colors"
                >
                  Remove Token
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:opacity-70 transition-opacity"
                  >
                    {deleting ? 'Removing...' : 'Confirm Remove'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          )}

          <div className="space-y-2 pt-2 border-t border-[color:var(--border-subtle)]">
            <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              Owner Telegram Identity
            </label>
            <div className="flex gap-2">
              <select
                value={selectedOwnerChatId}
                onChange={(e) => setSelectedOwnerChatId(e.target.value)}
                className="input-field h-9 text-[11px] w-full"
              >
                <option value="">Select owner private DM chat</option>
                {privateChats.map((chat) => (
                  <option key={chat.chat_id} value={String(chat.chat_id)}>
                    {chat.title} ({chat.chat_id})
                  </option>
                ))}
              </select>
              <button
                onClick={handleBindOwner}
                disabled={!selectedOwnerChatId.trim() || bindingOwner}
                className="btn-secondary h-9 px-3 text-[10px] font-bold uppercase tracking-widest"
              >
                {bindingOwner ? <Loader2 size={12} className="animate-spin" /> : 'Set Owner'}
              </button>
              <button
                onClick={handleClearOwner}
                disabled={!status?.owner_chat_id || clearingOwner}
                className="btn-secondary h-9 px-3 text-[10px] font-bold uppercase tracking-widest text-rose-500 disabled:opacity-50"
              >
                {clearingOwner ? <Loader2 size={12} className="animate-spin" /> : 'Remove Owner'}
              </button>
            </div>
            <p className="text-[10px] text-[color:var(--text-muted)]">
              Owner DM always routes to main session. Choose which private Telegram chat identity is treated as owner.
            </p>
          </div>
        </Panel>

        {/* Connected Chats */}
        <Panel className="p-6 space-y-4">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-emerald-500">
              <Users size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Connected Chats</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Chats that have sent /start to the bot</p>
            </div>
            <span className="text-[10px] bg-emerald-500/10 text-emerald-600 px-1.5 py-0.5 rounded font-bold">
              {chats.length}
            </span>
          </div>

          {chats.length === 0 ? (
            <div className="py-8 flex flex-col items-center justify-center text-[color:var(--text-muted)] gap-2 opacity-50">
              <MessageCircle size={24} strokeWidth={1} />
              <p className="text-[10px] font-medium uppercase tracking-widest">No chats connected yet</p>
              {status?.bot_username && (
                <p className="text-[10px] text-[color:var(--text-muted)]">
                  Send <span className="font-mono font-bold">/start</span> to <span className="font-mono font-bold">@{status.bot_username}</span> in Telegram
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {chats.map(chat => (
                <div
                  key={chat.chat_id}
                  className="flex items-center gap-3 p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]"
                >
                  {chat.chat_type === 'private' ? (
                    <User size={16} className="text-sky-500 shrink-0" />
                  ) : (
                    <Users size={16} className="text-emerald-500 shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-bold truncate">{chat.title}</p>
                    <p className="text-[10px] text-[color:var(--text-muted)] font-mono">{chat.chat_id}</p>
                  </div>
                  <StatusChip
                    label={chat.chat_type === 'private' ? 'DM' : 'Group'}
                    tone={chat.chat_type === 'private' ? 'info' : 'good'}
                    className="scale-90"
                  />
                </div>
              ))}
            </div>
          )}
        </Panel>

        {/* Info */}
        <div className="bg-[color:var(--surface-1)] p-4 rounded-xl border border-[color:var(--border-subtle)] flex items-start gap-3">
          <Info size={16} className="text-[color:var(--accent-solid)] shrink-0 mt-0.5" />
          <p className="text-[11px] text-[color:var(--text-secondary)] leading-relaxed font-medium">
            Owner DM always routes to your main session. Each Telegram group and each non-owner DM gets its own persistent channel session for stable context and safer isolation. Create a bot via <span className="font-mono">@BotFather</span> in Telegram to get a token.
          </p>
        </div>
      </div>
    </AppShell>
  );
}
