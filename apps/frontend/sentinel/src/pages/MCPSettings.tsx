import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ChevronRight, Info, Loader2, Pencil, Plug, Plus, Server, Trash2, Unplug, X } from 'lucide-react';
import { Panel } from '../components/ui/Panel';
import { requestJson } from '../lib/api';
import './mcp-settings.css';

type Connection = { transport: 'auto' | 'stdio'; url: string; command: string; args: string[]; headers: Record<string, null>; env: Record<string, null>; timeout_seconds: number };
type Server = { id: string; name: string; enabled: boolean; always_load: boolean; authenticated: boolean; connection: Connection; tools: { name: string; description: string }[] };
type Progress = { status: 'connecting' | 'sign_in' | 'connected' | 'disconnected' | 'cancelled' | 'error'; authorization_url?: string | null; message?: string | null };
type Credential = { id: number; key: string; value: string; saved: boolean };
type Editor = { id?: string; name: string; local: boolean; address: string; args: string; credentials: Credential[]; timeout: number };

export function MCPSettings() {
  const [servers, setServers] = useState<Server[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [removeId, setRemoveId] = useState<string | null>(null);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const nextKey = useRef(0);
  const mounted = useRef(true);
  const polling = useRef(new Set<string>());

  async function refresh() {
    const rows = await requestJson<Server[]>('/mcp');
    if (mounted.current) setServers(rows);
  }
  useEffect(() => {
    mounted.current = true;
    void refresh().catch(() => setError('Could not load MCP servers.'));
    return () => { mounted.current = false; };
  }, []);

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); await refresh(); }
    catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : 'Could not update the connection.'); }
    finally { if (mounted.current) setBusy(false); }
  }

  async function poll(id: string) {
    if (polling.current.has(id)) return;
    polling.current.add(id);
    try {
      while (mounted.current) {
        const state = await requestJson<Progress>(`/mcp/${id}/connection`);
        if (!mounted.current) return;
        setProgress(value => ({ ...value, [id]: state }));
        if (!['connecting', 'sign_in'].includes(state.status)) { await refresh(); return; }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } catch (e) {
      if (mounted.current) setProgress(value => ({ ...value, [id]: { status: 'error', message: e instanceof Error ? e.message : 'Connection failed.' } }));
    } finally { polling.current.delete(id); }
  }

  async function connect(id: string) {
    const state = await requestJson<Progress>(`/mcp/${id}/connect`, { method: 'POST' });
    setProgress(value => ({ ...value, [id]: state }));
    void poll(id);
  }

  function edit(server?: Server) {
    setError(''); setRemoveId(null);
    const config = server?.connection;
    const local = config?.transport === 'stdio';
    setEditor({ id: server?.id, name: server?.name ?? '', local,
      address: config ? local ? config.command : config.url : '', args: config?.args.join('\n') ?? '',
      timeout: config?.timeout_seconds ?? 60,
      credentials: Object.keys((local ? config?.env : config?.headers) ?? {}).map(key => ({ id: nextKey.current++, key, value: '', saved: true })) });
  }

  async function save() {
    if (!editor) return;
    const credentials: Record<string, string | null> = {};
    for (const row of editor.credentials) {
      const key = row.key.trim();
      if (!key) throw new Error('Enter a name for each authentication field, or remove the empty row.');
      if (key in credentials) throw new Error(`“${key}” is entered more than once.`);
      credentials[key] = row.saved && !row.value ? null : row.value;
    }
    const connection = { transport: editor.local ? 'stdio' : 'auto',
      url: editor.local ? '' : editor.address.trim(), command: editor.local ? editor.address.trim() : '',
      args: editor.local ? editor.args.split('\n').filter(value => value.length > 0) : [],
      env: editor.local ? credentials : {}, headers: editor.local ? {} : credentials,
      timeout_seconds: editor.timeout };
    const saved = await requestJson<Server>(editor.id ? `/mcp/${editor.id}` : '/mcp', {
      method: editor.id ? 'PUT' : 'POST', body: { name: editor.name.trim(), connection } });
    setEditor(null);
    if (!editor.id) await connect(saved.id);
  }

  function updateCredential(id: number, changes: Partial<Credential>) {
    setEditor(value => value && ({ ...value, credentials: value.credentials.map(row => row.id === id ? { ...row, ...changes } : row) }));
  }

  const inputClass = 'h-10 w-full rounded-lg border border-(--border-subtle) bg-(--surface-1) px-3 text-xs font-medium text-(--text-primary) placeholder:text-(--text-muted) outline-hidden transition-colors focus:border-(--accent-solid)';

  return <Panel className="settings-section mcp-settings p-6 space-y-6">
    <div className="settings-section-heading flex items-center gap-3 pb-4">
      <div className="p-2 rounded-lg bg-(--surface-2) text-(--accent-solid)"><Plug size={20} /></div>
      <div className="flex-1">
        <h2 className="text-sm font-bold uppercase tracking-widest">MCP servers</h2>
        <p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-tighter">Connect external tools to your agents</p>
      </div>
      {!editor && <button type="button" className="btn-secondary h-9 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest" disabled={busy} onClick={() => edit()}><Plus size={14} />Add server</button>}
    </div>
    {error && <p role="alert" className="mcp-error">{error}</p>}

    {editor ? <form className="space-y-5" onSubmit={event => { event.preventDefault(); void run(save); }}>
      <div className="flex items-center gap-2">
        <Server size={14} className="text-(--accent-solid)" />
        <h3 className="text-xs font-bold uppercase tracking-widest">{editor.id ? 'Edit server' : 'Add server'}</h3>
      </div>
      <p className="text-[10px] text-(--text-muted) leading-relaxed">{editor.local ? 'Runs a command on the computer hosting Sentinel.' : 'Sentinel detects the connection type and asks you to sign in when the server needs it.'}</p>
      <div className="mcp-fields">
        <label className="mcp-field"><span>Name</span><input required maxLength={120} className={inputClass} value={editor.name} onChange={e => setEditor({ ...editor, name: e.target.value })} placeholder="e.g. Mercury" autoFocus /></label>
        <label className="mcp-field"><span>{editor.local ? 'Command' : 'Server URL'}</span><input required type={editor.local ? 'text' : 'url'} className={inputClass} value={editor.address} onChange={e => setEditor({ ...editor, address: e.target.value })} placeholder={editor.local ? 'npx' : 'https://mcp.example.com/mcp'} spellCheck={false} /></label>
      </div>
      <button type="button" className="mcp-text-action" onClick={() => setEditor({ ...editor, local: !editor.local, address: '', args: '', credentials: [] })}>{editor.local ? 'Use a server URL instead' : 'Use a local command instead'}</button>
      {editor.local && <label className="mcp-field"><span>Arguments <small>One per line</small></span><textarea rows={3} className={`${inputClass} h-auto py-2`} value={editor.args} onChange={e => setEditor({ ...editor, args: e.target.value })} spellCheck={false} /></label>}
      <details className="mcp-advanced"><summary><ChevronRight size={14} />Advanced{editor.credentials.length > 0 && <span>{editor.credentials.length} saved {editor.local ? 'variables' : 'headers'}</span>}</summary>
        <div className="mcp-advanced-body">
          <div className="mcp-field-heading"><span>{editor.local ? 'Environment variables' : 'Custom headers'}</span><small>{editor.credentials.some(row => row.saved) ? 'Saved values stay unchanged unless replaced.' : 'Only needed if your server requires them.'}</small></div>
          {editor.credentials.map(row => <div className="mcp-credential" key={row.id}>
            <input aria-label={editor.local ? 'Variable name' : 'Header name'} className={inputClass} placeholder={editor.local ? 'API_KEY' : 'Authorization'} value={row.key} onChange={e => updateCredential(row.id, { key: e.target.value, saved: e.target.value === row.key && row.saved })} spellCheck={false} />
            <input aria-label={`Value for ${row.key || 'new field'}`} className={inputClass} type="password" autoComplete="new-password" placeholder={row.saved ? 'Saved — leave unchanged' : 'Value'} value={row.value} onChange={e => updateCredential(row.id, { value: e.target.value })} />
            <button type="button" className="mcp-icon-action" aria-label={`Remove ${row.key || 'field'}`} onClick={() => setEditor({ ...editor, credentials: editor.credentials.filter(item => item.id !== row.id) })}><X size={15} /></button>
          </div>)}
          <button type="button" className="mcp-text-action" onClick={() => setEditor({ ...editor, credentials: [...editor.credentials, { id: nextKey.current++, key: '', value: '', saved: false }] })}><Plus size={14} />{editor.local ? 'Add variable' : 'Add header'}</button>
        </div>
      </details>
      <div className="flex items-center justify-between gap-3 pt-2">
        <button type="button" className="mcp-text-action" disabled={busy} onClick={() => setEditor(null)}><ArrowLeft size={14} />Cancel</button>
        <button type="submit" className="btn-primary h-10 px-6 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed" disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}{editor.id ? 'Save changes' : 'Add server'}</button>
      </div>
    </form> : <>
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Server size={14} className="text-(--accent-solid)" />
          <h3 className="text-xs font-bold uppercase tracking-widest">Connected servers</h3>
        </div>
        <p className="text-[10px] text-(--text-muted) leading-relaxed">Give your agents access to external tools. Your existing approval settings still apply.</p>
        {servers.length === 0 && <div className="text-[10px] text-(--text-muted) uppercase tracking-widest py-2">No servers connected yet</div>}
        <div className="mcp-server-list">{servers.map(server => {
          const state = progress[server.id];
          const waiting = state?.status === 'connecting' || state?.status === 'sign_in';
          return <article className="mcp-server" key={server.id}>
            <div className="mcp-server-row">
              <div className="mcp-server-identity"><div className="mcp-server-title"><h4>{server.name}</h4><span className={`mcp-status ${server.enabled ? 'is-connected' : ''}`}>{server.enabled ? <><Check size={12} />Connected</> : waiting ? 'Connecting' : 'Not connected'}</span></div><p title={server.connection.url || server.connection.command}>{server.connection.url || server.connection.command}</p></div>
              <div className="mcp-server-actions">
                {!server.enabled && !waiting && <button className="btn-secondary h-8 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest" disabled={busy} onClick={() => void run(() => connect(server.id))}><Plug size={13} />Connect</button>}
                {server.enabled && <button className="mcp-icon-action" title="Disconnect" aria-label={`Disconnect ${server.name}`} disabled={busy} onClick={() => void run(async () => { await requestJson(`/mcp/${server.id}`, { method: 'PATCH', body: { enabled: false } }); setProgress(value => ({ ...value, [server.id]: { status: 'disconnected' } })); })}><Unplug size={16} /></button>}
                <button className="mcp-icon-action" title="Edit" aria-label={`Edit ${server.name}`} disabled={busy || waiting} onClick={() => edit(server)}><Pencil size={15} /></button>
                <button className="mcp-icon-action" title="Remove" aria-label={`Remove ${server.name}`} disabled={busy} onClick={() => setRemoveId(server.id)}><Trash2 size={15} /></button>
              </div>
            </div>
            {waiting && <div className="mcp-progress" role="status">{state?.status === 'sign_in' && state.authorization_url ? <><span>Sign in to finish connecting.</span><a className="mcp-text-action" href={state.authorization_url} target="_blank" rel="noopener noreferrer">Continue to sign in<ArrowUpRight size={14} /></a></> : <><Loader2 size={14} className="animate-spin" /><span>Connecting…</span></>}<button type="button" className="mcp-text-action" onClick={() => void run(async () => { await requestJson(`/mcp/${server.id}/connection`, { method: 'DELETE' }); setProgress(value => ({ ...value, [server.id]: { status: 'cancelled' } })); })}>Cancel</button></div>}
            {state?.status === 'error' && <p role="alert" className="mcp-error">{state.message}</p>}
            {server.enabled && <label className="settings-item-option mcp-pin">
              <input type="checkbox" className="settings-item-checkbox" checked={server.always_load} disabled={busy} onChange={event => void run(async () => { await requestJson(`/mcp/${server.id}`, { method: 'PATCH', body: { always_load: event.target.checked } }); })} />
              <span className="text-xs font-medium">Always load tools</span>
              <small className="text-[10px] text-(--text-muted)">Keep every tool in the prompt. Off means the agent loads them when a task needs them.</small>
            </label>}
            {server.tools.length > 0 && <details className="mcp-tools"><summary><ChevronRight size={13} />{server.tools.length} {server.tools.length === 1 ? 'tool' : 'tools'}</summary><ul>{server.tools.map(tool => <li key={tool.name}><span>{tool.name}</span><p>{tool.description}</p></li>)}</ul></details>}
            {removeId === server.id && <div className="mcp-remove"><p>Remove {server.name} and its saved credentials?</p><button className="mcp-text-action" disabled={busy} onClick={() => setRemoveId(null)}>Cancel</button><button className="mcp-text-action mcp-destructive" disabled={busy} onClick={() => void run(async () => { await requestJson(`/mcp/${server.id}`, { method: 'DELETE' }); setRemoveId(null); })}>Remove</button></div>}
          </article>;
        })}</div>
      </div>
      <div className="bg-(--surface-1) p-3 rounded-xl border border-(--border-subtle) flex items-start gap-2.5">
        <Info size={14} className="text-(--accent-solid) shrink-0 mt-0.5" />
        <p className="text-[10px] text-(--text-secondary) leading-relaxed">Tools from connected servers are loaded into a conversation when a task needs them, and unloaded again after a few idle turns. Every call still goes through your approval settings.</p>
      </div>
    </>}
  </Panel>;
}
