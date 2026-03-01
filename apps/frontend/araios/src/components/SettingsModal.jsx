import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import Modal from './Modal';

export default function SettingsModal({ open, onClose, notify }) {
  const [baseUrl, setBaseUrl] = useState('');
  const [savingBaseUrl, setSavingBaseUrl] = useState(false);
  const [agents, setAgents] = useState([]);
  const [loadingAgents, setLoadingAgents] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [agentLabel, setAgentLabel] = useState('');
  const [agentId, setAgentId] = useState('');
  const [agentSubject, setAgentSubject] = useState('');
  const [createdApiKey, setCreatedApiKey] = useState('');

  useEffect(() => {
    if (!open) return;
    setCreatedApiKey('');
    api('/api/settings')
      .then((data) => {
        setBaseUrl(data.settings?.manifest_base_url || '');
      })
      .catch(() => {});
    setLoadingAgents(true);
    api('/platform/auth/agents')
      .then((data) => {
        setAgents(Array.isArray(data.agents) ? data.agents : []);
      })
      .catch((err) => {
        notify(err.message || 'Could not load agent senders', 'warn');
      })
      .finally(() => {
        setLoadingAgents(false);
      });
  }, [open]);

  const saveBaseUrl = async () => {
    if (!baseUrl.trim()) return;
    try {
      setSavingBaseUrl(true);
      await api('/api/settings/manifest_base_url', {
        method: 'PUT',
        body: JSON.stringify({ value: baseUrl.trim() }),
      });
      notify('Settings saved');
    } catch (err) {
      notify(err.message || 'Could not save', 'warn');
    } finally {
      setSavingBaseUrl(false);
    }
  };

  const createAgentSender = async () => {
    try {
      setCreatingAgent(true);
      setCreatedApiKey('');
      const data = await api('/platform/auth/agents', {
        method: 'POST',
        body: JSON.stringify({
          label: agentLabel.trim() || undefined,
          agent_id: agentId.trim() || undefined,
          subject: agentSubject.trim() || undefined,
        }),
      });
      setCreatedApiKey(data.api_key || '');
      setAgentLabel('');
      setAgentId('');
      setAgentSubject('');
      setAgents((prev) => {
        const next = [data.agent, ...prev.filter((row) => row.id !== data.agent?.id)];
        return next;
      });
      notify('Agent sender created');
    } catch (err) {
      notify(err.message || 'Could not create agent sender', 'warn');
    } finally {
      setCreatingAgent(false);
    }
  };

  const copyCreatedKey = async () => {
    if (!createdApiKey) return;
    try {
      await navigator.clipboard.writeText(createdApiKey);
      notify('Agent API key copied');
    } catch {
      notify('Could not copy key', 'warn');
    }
  };

  if (!open) return null;

  return (
    <Modal onClose={onClose} title="Settings">
      <div className="modal-body">
        <div className="form-field">
          <label className="form-label">Manifest Base URL</label>
          <input
            className="form-input"
            value={baseUrl}
            onChange={e => setBaseUrl(e.target.value)}
            placeholder="http://localhost:9000"
          />
          <p className="text-xs text-[color:var(--text-muted)] mt-1">
            The URL agents use to reach araiOS. Used in GET /api/manifest.
          </p>
          <div style={{ marginTop: '10px' }}>
            <button className="btn-primary" onClick={saveBaseUrl} disabled={savingBaseUrl || !baseUrl.trim()}>
              {savingBaseUrl ? 'Saving…' : 'Save URL'}
            </button>
          </div>
        </div>

        <hr className="detail-divider" />

        <div className="form-field">
          <label className="form-label">Agent Senders</label>
          <p className="text-xs text-[color:var(--text-muted)] mt-1" style={{ marginBottom: '10px' }}>
            Create dedicated agent keys so each agent has a distinct identity and can be revoked independently.
          </p>
          {loadingAgents ? (
            <p className="text-xs text-[color:var(--text-muted)]">Loading agent senders…</p>
          ) : (
            <div style={{ display: 'grid', gap: '8px', marginBottom: '14px' }}>
              {agents.length === 0 ? (
                <p className="text-xs text-[color:var(--text-muted)]">No active agent senders found.</p>
              ) : (
                agents.map((agent) => (
                  <div
                    key={agent.id}
                    style={{
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      padding: '10px 12px',
                      background: 'var(--surface-1)',
                    }}
                  >
                    <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                      {agent.label}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                      agent_id: {agent.agent_id || '—'} | subject: {agent.subject}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          <div className="form-row">
            <div className="form-field">
              <label className="form-label">Label</label>
              <input
                className="form-input"
                value={agentLabel}
                onChange={(e) => setAgentLabel(e.target.value)}
                placeholder="Agent Sender"
              />
            </div>
            <div className="form-field">
              <label className="form-label">Agent ID</label>
              <input
                className="form-input"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="agent-review"
              />
            </div>
          </div>
          <div className="form-field">
            <label className="form-label">Subject (Optional)</label>
            <input
              className="form-input"
              value={agentSubject}
              onChange={(e) => setAgentSubject(e.target.value)}
              placeholder="Defaults to Agent ID"
            />
          </div>
          <button className="btn-primary" onClick={createAgentSender} disabled={creatingAgent}>
            {creatingAgent ? 'Creating…' : 'Create Agent Sender Key'}
          </button>

          {createdApiKey && (
            <div style={{ marginTop: '12px' }}>
              <label className="form-label">New API Key (Shown Once)</label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input className="form-input" value={createdApiKey} readOnly />
                <button className="btn-secondary" onClick={copyCreatedKey}>Copy</button>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="modal-footer">
        <button className="btn-secondary" onClick={onClose}>Close</button>
      </div>
    </Modal>
  );
}
