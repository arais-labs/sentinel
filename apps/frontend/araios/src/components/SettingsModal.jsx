import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import Modal from './Modal';

export default function SettingsModal({ open, onClose, notify }) {
  const [baseUrl, setBaseUrl] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    api('/api/settings').then(data => {
      setBaseUrl(data.settings?.manifest_base_url || '');
    }).catch(() => {});
  }, [open]);

  const save = async () => {
    if (!baseUrl.trim()) return;
    try {
      setSaving(true);
      await api('/api/settings/manifest_base_url', {
        method: 'PUT',
        body: JSON.stringify({ value: baseUrl.trim() }),
      });
      notify('Settings saved');
      onClose();
    } catch (err) {
      notify(err.message || 'Could not save', 'warn');
    } finally {
      setSaving(false);
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
        </div>
      </div>
      <div className="modal-footer">
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={save} disabled={saving || !baseUrl.trim()}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  );
}
