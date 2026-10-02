import { useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown, Download, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { notificationPublisher } from '../lib/notifications';
import { ProviderModelSettings } from './ProviderModelSettings';

const notify = notificationPublisher('Ollama');
type Endpoint = { base_url: string; api_key?: string };
type Model = { name: string; size: number };
type Config = { base_url: string; model: string; configured: boolean; has_api_key: boolean };
type Pull = { phase: string; model: string; detail: string; completed: number; total: number };
const idle: Pull = { phase: 'idle', model: '', detail: '', completed: 0, total: 0 };
const sizeLabel = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;

export function OllamaProviderSettings({ onChanged }: { onChanged: () => Promise<void> }) {
  const [saved, setSaved] = useState<Config>();
  const [modelSettingsRevision, setModelSettingsRevision] = useState(0);
  const [url, setUrl] = useState('http://127.0.0.1:11434');
  const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [model, setModel] = useState('');
  const [models, setModels] = useState<Model[]>([]);
  const [connection, setConnection] = useState<Endpoint | null>(null);
  const [pullModel, setPullModel] = useState('');
  const [pull, setPull] = useState<Pull>(idle);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [removing, setRemoving] = useState<string | null>(null);
  const epoch = useRef(0);
  const formId = useId();
  const configured = saved?.configured ?? false;
  const endpoint: Endpoint = { base_url: url.trim().replace(/\/+$/, ''), api_key: clearKey ? '' : key || undefined };

  useEffect(() => {
    let active = true;
    const version = ++epoch.current;
    void api.get<Config>('/settings/ollama').then(async data => {
      if (!active) return;
      // A malformed response must not take down the page that hosts this card.
      if (typeof data?.base_url !== 'string') throw new Error('Ollama settings are unavailable.');
      setSaved(data); setUrl(data.base_url); setModel(data.model ?? '');
      if (data.configured) {
        const endpoint = { base_url: data.base_url };
        const [discovered, progress] = await Promise.all([
          api.post<{ models: Model[] }>('/settings/ollama/discover', endpoint),
          api.post<Pull>('/settings/ollama/pull/status', endpoint),
        ]);
        if (!active || epoch.current !== version) return;
        setConnection(endpoint); setModels(discovered.models); setPull(progress);
      }
    }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; epoch.current++; };
  }, []);

  // The backend owns the download. Closing this editor/page never cancels it.
  useEffect(() => {
    if (!connection || pull.phase !== 'running') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.post<Pull>('/settings/ollama/pull/status', connection);
        if (!active) return;
        if (next.phase === 'succeeded') {
          try {
            const data = await api.post<{ models: Model[] }>('/settings/ollama/discover', connection);
            if (active) setModels(data.models);
          } catch {
            if (active) setError('Download finished. Refresh to reload the model list.');
          }
          if (!active) return;
          notify.success(`${next.model} downloaded.`);
        }
        if (next.phase === 'failed') notify.error(next.detail);
        setPull(next);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Cannot check download progress.');
      }
      if (active) timer = setTimeout(() => void poll(), 1200);
    };
    timer = setTimeout(() => void poll(), 1200);
    return () => { active = false; clearTimeout(timer); };
  }, [connection, pull.phase]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); } catch (err) { setError(err instanceof Error ? err.message : 'Ollama request failed.'); }
    finally { setBusy(false); }
  };
  const connect = async () => {
    const version = ++epoch.current;
    const [data, progress] = await Promise.all([
      api.post<{ models: Model[] }>('/settings/ollama/discover', endpoint),
      api.post<Pull>('/settings/ollama/pull/status', endpoint),
    ]);
    if (version !== epoch.current) return;
    setConnection(endpoint); setModels(data.models); setPull(progress);
    setModel(current => data.models.some(item => item.name === current) ? current : '');
  };
  const invalidate = () => {
    epoch.current++; setConnection(null); setModels([]); setPull(idle); setModel(''); setRemoving(null); setError('');
  };
  const percent = pull.total ? Math.min(100, Math.floor(100 * pull.completed / pull.total)) : undefined;

  return <section className="provider-connection-editor" aria-label="Ollama provider settings">
    <h3>Connection</h3>
    <div id={formId} className="settings-provider-editor ollama-manager" role="region" aria-label="Ollama settings">
      <label htmlFor={`${formId}-url`}>Server URL</label>
      <div className="ollama-server-row">
        <input id={`${formId}-url`} disabled={busy || !!removing} value={url} onChange={event => { invalidate(); setUrl(event.target.value); }} placeholder="http://localhost:11434" />
        <button className="btn-secondary" disabled={busy || !url.trim() || !!removing} onClick={() => void run(connect)} aria-label={connection ? 'Refresh models' : 'Connect to server'}>
          {connection ? <RefreshCw size={14} /> : 'Connect'}
        </button>
      </div>
      <details className="ollama-auth">
        <summary>Authentication <ChevronDown size={12} /></summary>
        <label>API key (optional)<input type="password" autoComplete="new-password" disabled={busy || !!removing} value={key} onChange={event => { invalidate(); setKey(event.target.value); }} placeholder={saved?.has_api_key && endpoint.base_url === saved.base_url ? 'Using saved key' : 'Bearer token'} /></label>
        {saved?.has_api_key && endpoint.base_url === saved.base_url && <label className="ollama-checkbox"><input type="checkbox" disabled={busy || !!removing} checked={clearKey} onChange={event => { invalidate(); setClearKey(event.target.checked); }} />Clear saved key</label>}
      </details>
      {connection && <>
        <div className="ollama-list-heading"><span>Models <span className="ollama-muted">{models.length}</span></span>
          <button className="btn-secondary" disabled={busy || pull.phase === 'running' || !!removing} onClick={() => setAdding(!adding)}><Plus size={13} />Add model</button>
        </div>
        <div className="ollama-models" role="radiogroup" aria-label="Provider model">
          {models.length === 0 && <p className="ollama-empty">No models yet. Add one to get started.</p>}
          {models.map(item => <div className={`ollama-model ${model === item.name ? 'is-selected' : ''}`} key={item.name}>
            <label><input type="radio" name={`${formId}-model`} checked={model === item.name} disabled={busy || !!removing} onChange={() => setModel(item.name)} />
              <span className="ollama-model-name" title={item.name}>{item.name}<small>{item.size > 0 ? sizeLabel(item.size) : 'Installed'}</small></span>
            </label>
            <button className="ollama-icon-button" aria-label={`Remove ${item.name}`} disabled={busy || pull.phase === 'running' || !!removing} onClick={() => setRemoving(item.name)}><Trash2 size={14} /></button>
          </div>)}
        </div>
        {removing && <div className="ollama-confirm" role="alertdialog" aria-label={`Remove ${removing}?`}>
          <strong>Remove {removing}?</strong>
          <p>This deletes the model from <span>{connection.base_url}</span>.</p>
          {saved?.model === removing && saved.base_url === connection.base_url && <p>You’ll need to select another model for this provider.</p>}
          <div className="ollama-actions"><button className="btn-secondary ollama-danger" disabled={busy} onClick={() => void run(async () => {
            const result = await api.delete<{ cleared_selection: boolean; cleared_tier_models?: boolean }>('/settings/ollama/models', { ...connection, model: removing }, { timeoutMs: 45000 });
            setModels(current => current.filter(item => item.name !== removing));
            if (model === removing) setModel('');
            if (result.cleared_selection) {
              setSaved(current => current ? { ...current, model: '', configured: false } : current);
            }
            if (result.cleared_selection || result.cleared_tier_models) {
              // Refresh Advanced after deleting a tier override, even if the default remains.
              setModelSettingsRevision(value => value + 1);
              await onChanged();
            }
            notify.success(`${removing} removed.`); setRemoving(null);
          })}>Remove model</button><button className="btn-secondary" disabled={busy} onClick={() => setRemoving(null)}>Cancel</button></div>
        </div>}
        {adding && <form className="ollama-add" onSubmit={event => { event.preventDefault(); void run(async () => {
          setPull(await api.post<Pull>('/settings/ollama/pull', { ...connection, model: pullModel.trim() }));
          setAdding(false);
        }); }}>
          <label htmlFor={`${formId}-download`}>Model name</label>
          <div className="ollama-server-row"><input id={`${formId}-download`} value={pullModel} disabled={busy || pull.phase === 'running' || !!removing} onChange={event => setPullModel(event.target.value)} placeholder="e.g. qwen3:4b" />
            <button type="submit" className="btn-secondary" disabled={busy || !pullModel.trim() || pull.phase === 'running' || !!removing}><Download size={13} />Download</button></div>
        </form>}
        {pull.phase !== 'idle' && <div className="ollama-progress" role="status">
          <div><span>{pull.phase === 'succeeded' && <Check size={13} />}{pull.model}</span><span>{pull.phase === 'running' && percent !== undefined ? `${percent}%` : ''}</span></div>
          {pull.phase === 'running' && <div className="ollama-progress-track" role="progressbar" aria-label={`Downloading ${pull.model}`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><div className={percent === undefined ? 'is-indeterminate' : undefined} style={{ width: `${percent ?? 30}%` }} /></div>}
          <small className={pull.phase === 'failed' ? 'ollama-danger' : ''}>{pull.detail}{pull.phase === 'running' && pull.total > 0 ? ` · ${sizeLabel(pull.completed)} / ${sizeLabel(pull.total)}` : ''}</small>
          {pull.phase === 'failed' && <button className="btn-secondary" disabled={busy || !!removing} onClick={() => void run(async () => {
            setPull(await api.post<Pull>('/settings/ollama/pull', { ...connection, model: pull.model }));
          })}>Retry download</button>}
        </div>}
        <div className="ollama-actions ollama-footer"><button className="btn-primary" disabled={busy || !model || !!removing} onClick={() => void run(async () => {
          const result = await api.post<{ has_api_key: boolean }>('/settings/ollama', { ...connection, model });
          setSaved({ base_url: connection.base_url, model, configured: true, has_api_key: result.has_api_key });
          setKey(''); setClearKey(false); setConnection({ base_url: connection.base_url });
          setModelSettingsRevision(value => value + 1);
          await onChanged(); notify.success('Ollama provider saved.');
        })}>Save provider</button><span className="ollama-muted">Used by chats and Voice</span></div>
      </>}
      {busy && <p className="ollama-muted" role="status">Working…</p>}
    </div>
    {error && <p role="alert" className="px-4 pb-3 text-xs text-(--status-error)">{error}</p>}
    {configured && <ProviderModelSettings key={`${saved?.base_url}:${saved?.model}:${modelSettingsRevision}`} provider="ollama" onSaved={() => void onChanged()} />}
  </section>;
}
