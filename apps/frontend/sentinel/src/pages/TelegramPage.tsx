import { useState, useEffect, useCallback } from 'react';
import { notificationPublisher } from '../lib/notifications';
import {
  Send, Eye, EyeOff, Check, Loader2, RefreshCw, Play, Square,
  MessageCircle, User, Users, Info,
} from 'lucide-react';

import { SettingsIntegrationFrame } from '../components/SettingsIntegrationFrame';
import { AppShell } from '../components/AppShell';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import '../components/session/chat-header.css';
import './telegram-page.css';

const notify = notificationPublisher('Telegram');

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
}

// ── main page ───────────────────────────────────────────────────────────────

export function TelegramPage({ embedded = false }: { embedded?: boolean } = {}) {
  const Shell = embedded ? SettingsIntegrationFrame : AppShell;
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
      notify.success('Telegram bot configured and started');
      setToken('');
      await fetchStatus();
    } catch {
      notify.error('Failed to configure Telegram bot');
    } finally {
      setSaving(false);
    }
  }

  async function handleStart() {
    setStarting(true);
    try {
      await api.post('/telegram/start');
      notify.success('Telegram bot started');
      await fetchStatus();
    } catch {
      notify.error('Failed to start Telegram bot');
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    setStopping(true);
    try {
      await api.post('/telegram/stop');
      notify.success('Telegram bot stopped');
      await fetchStatus();
    } catch {
      notify.error('Failed to stop Telegram bot');
    } finally {
      setStopping(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await api.delete('/telegram/configure');
      notify.success('Telegram bot token removed');
      setConfirmDelete(false);
      await fetchStatus();
    } catch {
      notify.error('Failed to remove Telegram bot');
    } finally {
      setDeleting(false);
    }
  }

  async function handleBindOwner() {
    if (!selectedOwnerChatId.trim()) return;
    setBindingOwner(true);
    try {
      await api.post('/telegram/owner', { chat_id: Number(selectedOwnerChatId) });
      notify.success('Owner Telegram identity linked');
      await fetchStatus();
    } catch {
      notify.error('Failed to bind owner Telegram identity');
    } finally {
      setBindingOwner(false);
    }
  }

  async function handleClearOwner() {
    setClearingOwner(true);
    try {
      await api.delete('/telegram/owner');
      notify.success('Owner Telegram identity removed');
      setSelectedOwnerChatId('');
      await fetchStatus();
    } catch {
      notify.error('Failed to remove owner Telegram identity');
    } finally {
      setClearingOwner(false);
    }
  }

  const chats = status?.connected_chats ? Object.values(status.connected_chats) : [];
  const privateChats = chats.filter(chat => chat.chat_type === 'private');

  const connectionLabel = loading && !status ? 'Connecting' : status?.running ? 'Running' : status?.token_configured ? 'Stopped' : 'Not configured';

  return (
    <Shell title="Telegram" contentClassName="telegram-pane" actions={
      <div className="chat-header-actions telegram-header-actions">
        <span className="chat-header-pill telegram-connection"><i data-running={status?.running || undefined} />{connectionLabel}</span>
        {status?.token_configured && (
          <button className="chat-header-pill" onClick={status.running ? handleStop : handleStart} disabled={starting || stopping}>
            {starting || stopping ? <Loader2 size={14} className="animate-spin" /> : status.running ? <Square size={14} /> : <Play size={14} />}
            {status.running ? 'Stop bot' : 'Start bot'}
          </button>
        )}
        <button className="chat-header-pill" aria-label="Refresh Telegram status" title="Refresh status" disabled={loading} onClick={() => { setLoading(true); void fetchStatus(); }}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
    }>
      <div className="telegram-content">
        <section className="telegram-card" aria-labelledby="telegram-status-title">
          <div className="telegram-section-heading"><Send size={18} /><div><h2 id="telegram-status-title">Telegram bridge</h2><p>Connect your conversations to Sentinel.</p></div></div>
          {loading && !status ? <div className="telegram-empty" role="status"><Loader2 size={20} className="animate-spin" /><span>Loading connection…</span></div> : (
            <dl className="telegram-status-grid">
              <div><dt>Connection</dt><dd><StatusChip label={connectionLabel} tone={status?.running ? 'good' : 'default'} /></dd></div>
              {status?.bot_username && <div><dt>Bot</dt><dd>@{status.bot_username}</dd></div>}
              {status?.masked_token && <div><dt>Saved token</dt><dd className="telegram-mono">{status.masked_token}</dd></div>}
              <div><dt>Owner chat</dt><dd className="telegram-mono">{status?.owner_chat_id || 'Not linked'}</dd></div>
            </dl>
          )}
        </section>

        <section className="telegram-card" aria-labelledby="telegram-config-title">
          <div className="telegram-section-heading"><MessageCircle size={18} /><div><h2 id="telegram-config-title">Bot configuration</h2><p>Use a token from @BotFather to connect your bot.</p></div></div>
          <div className="telegram-field">
            <label htmlFor="telegram-token">Bot token</label>
            <div className="telegram-field-row">
              <div className="telegram-token-input">
                <input id="telegram-token" type={showToken ? 'text' : 'password'} value={token} onChange={e => setToken(e.target.value)} placeholder={status?.token_configured ? 'Enter a replacement token…' : 'Paste your bot token…'} className="input-field" autoComplete="off" onKeyDown={e => e.key === 'Enter' && !saving && handleSave()} />
                <button type="button" aria-label={showToken ? 'Hide bot token' : 'Show bot token'} aria-pressed={showToken} onClick={() => setShowToken(v => !v)}>{showToken ? <EyeOff size={15} /> : <Eye size={15} />}</button>
              </div>
              <button className="telegram-button" onClick={handleSave} disabled={!token.trim() || saving}>{saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}Save token</button>
            </div>
            {status?.token_configured && <div className="telegram-remove-token">
              {!confirmDelete ? <button className="telegram-text-button telegram-danger" onClick={() => setConfirmDelete(true)}>Remove token</button> : <div className="telegram-confirm" role="group" aria-label="Confirm token removal"><span>Remove the saved bot token?</span><button className="telegram-text-button telegram-danger" onClick={handleDelete} disabled={deleting}>{deleting ? 'Removing…' : 'Confirm remove'}</button><button className="telegram-text-button" onClick={() => setConfirmDelete(false)}>Cancel</button></div>}
            </div>}
          </div>

          <div className="telegram-field telegram-owner">
            <label htmlFor="telegram-owner">Owner identity</label>
            <p>Link a private chat to the owner. Its messages go to the session selected with <code>/session</code> in Telegram.</p>
            <div className="telegram-field-row">
              <select id="telegram-owner" value={selectedOwnerChatId} onChange={e => setSelectedOwnerChatId(e.target.value)} className="input-field">
                <option value="">Select an owner private chat</option>
                {privateChats.map(chat => <option key={chat.chat_id} value={String(chat.chat_id)}>{chat.title} ({chat.chat_id})</option>)}
              </select>
              <button className="telegram-button" onClick={handleBindOwner} disabled={!selectedOwnerChatId.trim() || bindingOwner}>{bindingOwner ? <Loader2 size={14} className="animate-spin" /> : <User size={14} />}Set owner</button>
              <button className="telegram-button telegram-danger" onClick={handleClearOwner} disabled={!status?.owner_chat_id || clearingOwner}>{clearingOwner ? <Loader2 size={14} className="animate-spin" /> : null}Remove owner</button>
            </div>
            {privateChats.length === 0 && <p>Send <code>/start</code> to your bot in a private chat first.</p>}
          </div>
        </section>

        <section className="telegram-card" aria-labelledby="telegram-chats-title">
          <div className="telegram-section-heading"><Users size={18} /><div><h2 id="telegram-chats-title">Connected chats <span className="telegram-count">{chats.length}</span></h2><p>Conversations that have sent /start to the bot.</p></div></div>
          {chats.length === 0 ? <div className="telegram-empty"><MessageCircle size={24} strokeWidth={1.5} /><span>No chats connected yet</span>{status?.bot_username && <p>Send <code>/start</code> to @{status.bot_username} in Telegram.</p>}</div> : <div className="telegram-chats">
            {chats.map(chat => <div key={chat.chat_id} className="telegram-chat-row">
              {chat.chat_type === 'private' ? <User size={17} /> : <Users size={17} />}
              <div className="telegram-chat-identity"><p title={chat.title}>{chat.title}</p><span>{chat.chat_id}</span></div>
              <StatusChip label={chat.chat_type === 'private' ? 'DM' : 'Group'} tone={chat.chat_type === 'private' ? 'info' : 'good'} />
            </div>)}
          </div>}
        </section>
        <div className="telegram-note"><Info size={16} /><p>Each group and non-owner private chat has its own persistent session. The owner chat follows the session you explicitly select in Telegram.</p></div>
      </div>
    </Shell>
  );
}
