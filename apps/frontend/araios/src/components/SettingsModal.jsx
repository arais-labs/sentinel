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
  const [editingAgents, setEditingAgents] = useState({});
  const [savingAgents, setSavingAgents] = useState({});
  const [revokingAgents, setRevokingAgents] = useState({});
  const [rotatingAgents, setRotatingAgents] = useState({});

  useEffect(() => {
    if (!open) return;
    setCreatedApiKey('');
    setEditingAgents({});
    setSavingAgents({});
    setRevokingAgents({});
    setRotatingAgents({});
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
        notify(err.message || 'Could not load agent tokens', 'warn');
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

  const createAgentToken = async () => {
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
      notify('Agent token created');
    } catch (err) {
      notify(err.message || 'Could not create agent token', 'warn');
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

  const startEditAgent = (agent) => {
    setEditingAgents((prev) => ({
      ...prev,
      [agent.id]: {
        label: agent.label || '',
        agent_id: agent.agent_id || '',
        subject: agent.subject || '',
      },
    }));
  };

  const cancelEditAgent = (agentIdToCancel) => {
    setEditingAgents((prev) => {
      const next = { ...prev };
      delete next[agentIdToCancel];
      return next;
    });
  };

  const updateEditField = (agentIdToUpdate, field, value) => {
    setEditingAgents((prev) => ({
      ...prev,
      [agentIdToUpdate]: {
        ...(prev[agentIdToUpdate] || {}),
        [field]: value,
      },
    }));
  };

  const saveAgent = async (agentIdToSave) => {
    const draft = editingAgents[agentIdToSave];
    if (!draft) return;
    if (!draft.label.trim() || !draft.agent_id.trim() || !draft.subject.trim()) {
      notify('Label, Agent ID, and Subject are required', 'warn');
      return;
    }
    try {
      setSavingAgents((prev) => ({ ...prev, [agentIdToSave]: true }));
      const updated = await api(`/platform/auth/agents/${agentIdToSave}`, {
        method: 'PATCH',
        body: JSON.stringify({
          label: draft.label.trim(),
          agent_id: draft.agent_id.trim(),
          subject: draft.subject.trim(),
        }),
      });
      setAgents((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      cancelEditAgent(agentIdToSave);
      notify('Agent token updated');
    } catch (err) {
      notify(err.message || 'Could not update agent token', 'warn');
    } finally {
      setSavingAgents((prev) => ({ ...prev, [agentIdToSave]: false }));
    }
  };

  const revokeAgent = async (agentIdToRevoke) => {
    try {
      setRevokingAgents((prev) => ({ ...prev, [agentIdToRevoke]: true }));
      await api(`/platform/auth/agents/${agentIdToRevoke}`, { method: 'DELETE' });
      setAgents((prev) => prev.filter((row) => row.id !== agentIdToRevoke));
      cancelEditAgent(agentIdToRevoke);
      notify('Agent token revoked');
    } catch (err) {
      notify(err.message || 'Could not revoke agent token', 'warn');
    } finally {
      setRevokingAgents((prev) => ({ ...prev, [agentIdToRevoke]: false }));
    }
  };

  const rotateAgent = async (agentIdToRotate) => {
    try {
      setRotatingAgents((prev) => ({ ...prev, [agentIdToRotate]: true }));
      const rotated = await api(`/platform/auth/agents/${agentIdToRotate}/rotate`, { method: 'POST' });
      setAgents((prev) =>
        prev.map((row) => (row.id === rotated.agent?.id ? rotated.agent : row))
      );
      setCreatedApiKey(rotated.api_key || '');
      notify('Agent token rotated');
    } catch (err) {
      notify(err.message || 'Could not rotate agent token', 'warn');
    } finally {
      setRotatingAgents((prev) => ({ ...prev, [agentIdToRotate]: false }));
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
          <label className="form-label">Agent Tokens</label>
          <p className="text-xs text-[color:var(--text-muted)] mt-1" style={{ marginBottom: '10px' }}>
            Create dedicated keys so each agent has a distinct identity. You can edit metadata, rotate, and revoke keys.
          </p>
          {loadingAgents ? (
            <p className="text-xs text-[color:var(--text-muted)]">Loading agent tokens...</p>
          ) : (
            <div style={{ display: 'grid', gap: '8px', marginBottom: '14px' }}>
              {agents.length === 0 ? (
                <p className="text-xs text-[color:var(--text-muted)]">No active agent tokens found.</p>
              ) : (
                agents.map((agent) => (
                  <div
                    key={agent.id}
                    style={{
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      padding: '10px 12px',
                      background: 'var(--surface-1)',
                      display: 'grid',
                      gap: '8px',
                    }}
                  >
                    {editingAgents[agent.id] ? (
                      <>
                        <div className="form-row">
                          <div className="form-field">
                            <label className="form-label">Label</label>
                            <input
                              className="form-input"
                              value={editingAgents[agent.id].label}
                              onChange={(e) => updateEditField(agent.id, 'label', e.target.value)}
                            />
                          </div>
                          <div className="form-field">
                            <label className="form-label">Agent ID</label>
                            <input
                              className="form-input"
                              value={editingAgents[agent.id].agent_id}
                              onChange={(e) => updateEditField(agent.id, 'agent_id', e.target.value)}
                            />
                          </div>
                        </div>
                        <div className="form-field">
                          <label className="form-label">Subject</label>
                          <input
                            className="form-input"
                            value={editingAgents[agent.id].subject}
                            onChange={(e) => updateEditField(agent.id, 'subject', e.target.value)}
                          />
                        </div>
                        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                          <button
                            className="btn-primary"
                            onClick={() => saveAgent(agent.id)}
                            disabled={savingAgents[agent.id]}
                          >
                            {savingAgents[agent.id] ? 'Saving…' : 'Save'}
                          </button>
                          <button className="btn-secondary" onClick={() => cancelEditAgent(agent.id)}>
                            Cancel
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={() => rotateAgent(agent.id)}
                            disabled={rotatingAgents[agent.id]}
                          >
                            {rotatingAgents[agent.id] ? 'Rotating…' : 'Rotate Key'}
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={() => revokeAgent(agent.id)}
                            disabled={revokingAgents[agent.id]}
                          >
                            {revokingAgents[agent.id] ? 'Revoking…' : 'Revoke'}
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                          {agent.label}
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                          agent_id: {agent.agent_id || '—'} | subject: {agent.subject}
                        </div>
                        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                          <button className="btn-secondary" onClick={() => startEditAgent(agent)}>
                            Edit
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={() => rotateAgent(agent.id)}
                            disabled={rotatingAgents[agent.id]}
                          >
                            {rotatingAgents[agent.id] ? 'Rotating…' : 'Rotate Key'}
                          </button>
                          <button
                            className="btn-secondary"
                            onClick={() => revokeAgent(agent.id)}
                            disabled={revokingAgents[agent.id]}
                          >
                            {revokingAgents[agent.id] ? 'Revoking…' : 'Revoke'}
                          </button>
                        </div>
                      </>
                    )}
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
                placeholder="Agent Token"
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
          <button className="btn-primary" onClick={createAgentToken} disabled={creatingAgent}>
            {creatingAgent ? 'Creating...' : 'Create Agent Token'}
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
