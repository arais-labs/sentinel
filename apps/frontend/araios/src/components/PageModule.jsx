import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';
import { IconCopy } from './Icons';

/**
 * PageModule — renders a single editable document page (no list).
 * Used for modules like Positioning where there's one canonical record.
 */
export default function PageModule({ config, notify, setRefresh }) {
  const [record, setRecord] = useState(null);
  const [form, setForm] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyPrompt = () => {
    const BASE_URL = window.location.origin;
    const base = `${BASE_URL}/api/modules/${config.name}`;
    const fields = (config.fields || []).map(f => `${f.key}${f.required ? '*' : ''} (${f.type})`).join(', ');
    const text = [
      `Module: ${config.label} (${config.name}) — ${config.description || ''}`,
      `Type: page — single editable document, no list.`,
      '',
      `Auth: Use your araiOS token in every request: Authorization: Bearer <your_araios_token>`,
      `If you do not have an araiOS token, ask the user to provide one before proceeding.`,
      '',
      'Endpoints:',
      `  GET   ${base}/records   — get current document`,
      `  PATCH ${base}/records/:id — update fields`,
      '',
      `Fields: ${fields || 'none'}`,
    ].join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    notify('Prompt copied to clipboard');
    setTimeout(() => setCopied(false), 2000);
  };

  const loadRecord = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const res = await api(`/api/modules/${config.name}/records`);
      const recs = res.records || [];
      const rec = recs[0] || null;
      setRecord(rec);
      if (rec) {
        const data = {};
        (config.fields || []).forEach(f => { data[f.key] = rec[f.key] ?? ''; });
        setForm(data);
      } else {
        const empty = {};
        (config.fields || []).forEach(f => { empty[f.key] = ''; });
        setForm(empty);
      }
      setDirty(false);
    } catch (err) {
      notify(err.message || 'Failed to load', 'warn');
    } finally {
      setLoading(false);
    }
  }, [config, notify]);

  useEffect(() => { setRefresh(loadRecord); }, [loadRecord, setRefresh]);
  useEffect(() => { loadRecord(); }, [config.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key, val) => {
    setForm(prev => ({ ...prev, [key]: val }));
    setDirty(true);
  };

  const save = useCallback(async () => {
    try {
      setSaving(true);
      if (record) {
        await api(`/api/modules/${config.name}/records/${record.id}`, {
          method: 'PATCH',
          body: JSON.stringify(form),
        });
      } else {
        await api(`/api/modules/${config.name}/records`, {
          method: 'POST',
          body: JSON.stringify(form),
        });
      }
      notify('Saved');
      setDirty(false);
      await loadRecord(true);
    } catch (err) {
      notify(err.message || 'Could not save', 'warn');
    } finally {
      setSaving(false);
    }
  }, [config.name, record, form, notify, loadRecord]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">
        Loading {config.label}...
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header bar */}
      <div className="sub-header">
        <div className="sub-header-left">
          <span className="text-sm font-medium text-[color:var(--text-secondary)]">{config.label}</span>
        </div>
        <div className="sub-header-right" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            className="p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded hover:bg-[color:var(--surface-2)] flex items-center gap-1"
            onClick={copyPrompt}
            title="Copy agent prompt"
          >
            <IconCopy />
            <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
          </button>
          <button
            className="btn-primary"
            onClick={save}
            disabled={saving || !dirty}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* Full-width editable form */}
      <div className="flex-1 overflow-y-auto p-6">
        <div style={{ maxWidth: 720, margin: '0 auto' }} className="space-y-6">
          {(config.fields || []).map(field => (
            <div key={field.key} className="form-field">
              <label className="form-label">{field.label}</label>
              <PageFieldInput
                field={field}
                value={form[field.key] ?? ''}
                onChange={val => set(field.key, val)}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PageFieldInput({ field, value, onChange }) {
  if (field.type === 'textarea') {
    return (
      <textarea
        className="editor-textarea"
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={6}
        style={{ minHeight: 120 }}
      />
    );
  }
  if (field.type === 'select') {
    return (
      <select className="form-input" value={value} onChange={e => onChange(e.target.value)}>
        <option value="">— select —</option>
        {(field.options || []).map(opt => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    );
  }
  const typeMap = { email: 'email', url: 'url', number: 'number', date: 'date' };
  return (
    <input
      className="form-input"
      type={typeMap[field.type] || 'text'}
      value={value}
      onChange={e => onChange(e.target.value)}
    />
  );
}
