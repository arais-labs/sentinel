import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';
import { IconCopy } from './Icons';

function buildToolPrompt(config) {
  const BASE_URL = window.location.origin;
  const lines = [
    `Module: ${config.label} (${config.name}) — ${config.description || ''}`,
    `Type: tool — call actions via POST, no records stored.`,
    '',
    `Auth: Use your araiOS token in every request: Authorization: Bearer <your_araios_token>`,
    `If you do not have an araiOS token, ask the user to provide one before proceeding.`,
    '',
    'Actions:',
  ];
  for (const action of (config.actions || [])) {
    lines.push(`  ${action.id} — ${action.description || action.label}`);
    lines.push(`    POST ${BASE_URL}/api/modules/${config.name}/action/${action.id}`);
    lines.push(`    Body: { ${(action.params || []).map(p => `"${p.key}"${p.required ? '*' : ''}: "${p.placeholder || p.type}"`).join(', ')} }`);
  }
  const required = (config.secrets || []).filter(s => s.required);
  if (required.length) {
    lines.push('');
    lines.push(`Note: This tool requires credentials to be configured by an operator before use. Ask them to set the required secrets in the ${config.label} tool panel.`);
  }
  return lines.join('\n');
}

export default function ApiModule({ config, notify, setRefresh }) {
  const [secretsStatus, setSecretsStatus] = useState({});
  const [search, setSearch] = useState('');
  const [copied, setCopied] = useState(false);

  const copyPrompt = () => {
    navigator.clipboard.writeText(buildToolPrompt(config));
    setCopied(true);
    notify('Prompt copied to clipboard');
    setTimeout(() => setCopied(false), 2000);
  };

  const loadSecrets = useCallback(async () => {
    if (!(config.secrets || []).length) return;
    try {
      const res = await api(`/api/modules/${config.name}/secrets-status`);
      setSecretsStatus(res.secrets || {});
    } catch { /* non-fatal */ }
  }, [config.name, config.secrets]);

  useEffect(() => {
    setRefresh(loadSecrets);
    loadSecrets();
  }, [loadSecrets, setRefresh]);

  const resetSecret = async (key, label) => {
    try {
      await api(`/api/modules/${config.name}/secrets/${key}`, { method: 'DELETE' });
      notify(`${label} cleared`);
      loadSecrets();
    } catch (err) {
      notify(err.message || 'Could not clear secret', 'warn');
    }
  };

  const allRequired = (config.secrets || []).filter(s => s.required);
  const missingRequired = allRequired.filter(s => !secretsStatus[s.key]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="sub-header" style={{ position: 'relative', padding: '14px 16px' }}>
        <div className="sub-header-left">
          <span className="text-sm font-medium text-[color:var(--text-secondary)]">
            {config.label}
          </span>
          {/* Secret status chips */}
          {(config.secrets || []).map(s => (
            secretsStatus[s.key] ? (
              <span key={s.key} className="flex items-center gap-1">
                <span className="badge badge-success">✓ {s.label}</span>
                <button
                  className="text-xs text-[color:var(--text-muted)] hover:text-rose-400 transition-colors leading-none"
                  onClick={() => resetSecret(s.key, s.label)}
                  title="Clear secret"
                >×</button>
              </span>
            ) : (
              <span key={s.key} className="badge badge-warn">✗ {s.label}</span>
            )
          ))}
        </div>
        <div className="sub-header-right">
          <button
            className="p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded hover:bg-[color:var(--surface-2)] flex items-center gap-1"
            onClick={copyPrompt}
            title="Copy agent prompt"
          >
            <IconCopy />
            <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
          </button>
        </div>
        {(config.actions || []).length > 2 && (
          <div style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}>
            <input
              className="form-input"
              style={{ width: 240, padding: '4px 10px', fontSize: 12 }}
              placeholder="Search actions…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div style={{ maxWidth: 720, margin: '0 auto' }} className="space-y-4">
          {/* Secret config cards (shown when missing) */}
          {(config.secrets || []).filter(s => !secretsStatus[s.key]).map(s => (
            <SecretCard
              key={s.key}
              moduleName={config.name}
              secret={s}
              onSaved={loadSecrets}
              notify={notify}
            />
          ))}

          {missingRequired.length > 0 && (config.actions || []).length > 0 && (
            <div className="text-xs text-[color:var(--text-muted)] py-2 border-t border-[color:var(--border-subtle)]">
              Configure required secrets above to run actions
            </div>
          )}

          {/* Action cards */}
          {(config.actions || []).filter(a =>
            !search.trim() || a.label.toLowerCase().includes(search.toLowerCase()) || (a.description || '').toLowerCase().includes(search.toLowerCase())
          ).map(action => (
            <ActionCard
              key={action.id}
              action={action}
              moduleName={config.name}
              secretsStatus={secretsStatus}
              requiredSecrets={config.secrets || []}
              notify={notify}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function SecretCard({ moduleName, secret, onSaved, notify }) {
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!value.trim()) return;
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/secrets/${secret.key}`, {
        method: 'PUT',
        body: JSON.stringify({ value }),
      });
      setValue('');
      notify(`${secret.label} saved`);
      onSaved();
    } catch (err) {
      notify(err.message || 'Could not save secret', 'warn');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 flex items-end gap-3">
      <div className="flex-1 form-field" style={{ marginBottom: 0 }}>
        <label className="form-label">
          <span className="text-rose-400 mr-1">✗</span>
          {secret.label}
          {secret.hint && <span className="text-[color:var(--text-muted)] ml-2 font-normal">{secret.hint}</span>}
        </label>
        <input
          className="form-input"
          type="password"
          placeholder="Paste value…"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && save()}
        />
      </div>
      <button className="btn-primary shrink-0" onClick={save} disabled={saving || !value.trim()}>
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}

function ActionCard({ action, moduleName, secretsStatus, requiredSecrets, notify }) {
  const params = action.params || [];
  const [form, setForm] = useState(() => Object.fromEntries(params.map(p => [p.key, ''])));
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const missingSecrets = requiredSecrets.filter(s => s.required && !secretsStatus[s.key]);
  const disabled = missingSecrets.length > 0;

  const set = (key, val) => setForm(prev => ({ ...prev, [key]: val }));

  const run = useCallback(async () => {
    const missing = params.filter(p => p.required && !String(form[p.key] ?? '').trim());
    if (missing.length) { notify(`Required: ${missing.map(p => p.label).join(', ')}`, 'warn'); return; }
    try {
      setRunning(true);
      setResult(null);
      const res = await api(`/api/modules/${moduleName}/action/${action.id}`, {
        method: 'POST',
        body: JSON.stringify(form),
      });
      const ok = res?.ok !== false;
      setResult({ ok, data: res });
      if (!ok) notify(res?.error || 'Action returned an error', 'warn');
    } catch (err) {
      setResult({ ok: false, error: err.message });
      notify(err.message || 'Action failed', 'warn');
    } finally {
      setRunning(false);
      setExpanded(true);
    }
  }, [form, action.id, moduleName, params, notify]);

  return (
    <div className={`rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-start justify-between p-4 gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-[color:var(--text-primary)]">{action.label}</h3>
          {action.description && (
            <p className="text-xs text-[color:var(--text-muted)] mt-0.5">{action.description}</p>
          )}
        </div>
        <button className="btn-primary shrink-0" onClick={run} disabled={running || disabled}>
          {running ? 'Running…' : 'Run'}
        </button>
      </div>

      {params.length > 0 && (
        <div className="px-4 pb-4 space-y-3 border-t border-[color:var(--border-subtle)] pt-3">
          {params.map(param => (
            <div key={param.key} className="form-field">
              <label className="form-label">
                {param.label}
                {param.required && <span className="text-rose-400 ml-1">*</span>}
              </label>
              {param.type === 'textarea' ? (
                <textarea className="editor-textarea" rows={3}
                  value={form[param.key]} onChange={e => set(param.key, e.target.value)}
                  placeholder={param.placeholder || ''} />
              ) : (
                <input className="form-input"
                  type={param.type === 'number' ? 'number' : 'text'}
                  value={form[param.key]} onChange={e => set(param.key, e.target.value)}
                  placeholder={param.placeholder || ''} />
              )}
            </div>
          ))}
        </div>
      )}

      {result && (
        <div className="border-t border-[color:var(--border-subtle)]">
          <button
            className="flex w-full items-center justify-between px-4 py-2 text-xs hover:bg-[color:var(--surface-2)] transition-colors"
            onClick={() => setExpanded(e => !e)}
          >
            <span className={result.ok ? 'text-emerald-400' : 'text-rose-400'}>
              {result.ok ? '✓ Success' : '✗ Error'}
            </span>
            <span className="text-[color:var(--text-muted)]">{expanded ? '▲ hide' : '▼ show'}</span>
          </button>
          {expanded && (
            <pre className="px-4 pb-4 text-xs text-[color:var(--text-secondary)] overflow-x-auto whitespace-pre-wrap break-all">
              {JSON.stringify(result.ok ? result.data : { error: result.error }, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
