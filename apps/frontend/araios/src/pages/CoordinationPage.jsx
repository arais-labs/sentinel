import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import { api } from '../lib/api';
import { IconMessageCircle } from '../components/Icons';

const AGENT_COLORS = {
  esprit: '#5b7bf7',
  ronnor: '#a78bfa',
  admin:  '#f59e0b',
  agent:  '#34d399',
};

function agentColor(agent) {
  return AGENT_COLORS[agent] || '#6882a4';
}

function agentDisplay(msg) {
  const label = msg?.context?.agent_label;
  if (typeof label === 'string' && label.trim()) return label.trim();
  return msg.agent;
}

function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
}

export default function CoordinationPage({ notify, setRefresh }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  const load = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const data = await api('/api/coordination?limit=200');
      setMessages((data.messages || []).reverse());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  useEffect(() => { setRefresh(load); }, []);

  useEffect(() => { load(); }, []);

  useEffect(() => {
    const timer = setInterval(() => load(true), 5000);
    return () => clearInterval(timer);
  }, []);

  const sendMessage = async () => {
    const message = draft.trim();
    if (!message || sending) return;
    try {
      setSending(true);
      const created = await api('/api/coordination', {
        method: 'POST',
        body: JSON.stringify({
          message,
          context: { source: 'human_ui' },
        }),
      });
      setMessages((prev) => [created, ...prev]);
      setDraft('');
      notify('Message sent');
    } catch (err) {
      notify(err.message || 'Could not send message', 'warn');
    } finally {
      setSending(false);
    }
  };

  const onDraftKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="sub-header">
        <div className="sub-header-left">
           <div className="stat-chip active">
              <span className="stat-label">Telemetry Stream</span>
              <strong className="stat-value">{messages.length} pkts</strong>
           </div>
        </div>
        <div className="flex items-center gap-2">
           <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
           <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">Live Link Online</span>
        </div>
      </div>

      <div className="detail-content" style={{ padding: '24px', paddingBottom: '16px' }}>
        {loading && messages.length === 0 ? (
          <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Synchronizing relay...</div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full opacity-50 gap-4">
             <IconMessageCircle size={48} strokeWidth={1} />
             <p className="text-sm font-medium uppercase tracking-widest">No Coordination Detected</p>
          </div>
        ) : (
          <div style={{ maxWidth: '800px', margin: '0 auto', width: '100%', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {messages.map((msg) => (
              <div key={msg.id} className="panel" style={{ padding: '16px', display: 'flex', gap: '16px' }}>
                <div
                  style={{
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    backgroundColor: agentColor(msg.agent),
                    marginTop: '6px',
                    boxShadow: `0 0 8px ${agentColor(msg.agent)}`,
                    flexShrink: 0
                  }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                    <span style={{ fontSize: '11px', fontWeight: '800', textTransform: 'uppercase', color: agentColor(msg.agent), letterSpacing: '0.05em' }}>
                      {agentDisplay(msg)}
                    </span>
                    <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                      {timeAgo(msg.createdAt)}
                    </span>
                  </div>
                  {agentDisplay(msg) !== msg.agent && (
                    <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '8px', fontFamily: 'monospace' }}>
                      id: {msg.agent}
                    </div>
                  )}
                  <div className="text-sm leading-relaxed text-[color:var(--text-primary)]" style={{ wordBreak: 'break-word' }}>
                    <Markdown>{msg.message}</Markdown>
                  </div>
                  {msg.context && (
                    <details style={{ marginTop: '12px' }}>
                      <summary style={{ cursor: 'pointer', fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', color: 'var(--text-muted)', outline: 'none' }}>
                        DEBUG CONTEXT
                      </summary>
                      <pre style={{ marginTop: '8px', backgroundColor: 'var(--surface-1)', padding: '12px', borderRadius: '8px', fontSize: '11px', fontFamily: 'monospace', overflowX: 'auto', border: '1px solid var(--border-subtle)' }}>
                        {JSON.stringify(msg.context, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        style={{
          borderTop: '1px solid var(--border-subtle)',
          background: 'var(--surface-0)',
          padding: '12px 16px',
        }}
      >
        <div style={{ maxWidth: '800px', margin: '0 auto', width: '100%' }}>
          <label className="form-label">Send Supervision Message</label>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
            <textarea
              className="form-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onDraftKeyDown}
              rows={2}
              placeholder="Write a message to agents (Enter to send, Shift+Enter for newline)"
              style={{ height: 'auto', minHeight: '70px', paddingTop: '10px', paddingBottom: '10px', resize: 'vertical' }}
            />
            <button
              className="btn-primary"
              onClick={sendMessage}
              disabled={sending || !draft.trim()}
              style={{ height: '42px', minWidth: '96px' }}
            >
              {sending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
