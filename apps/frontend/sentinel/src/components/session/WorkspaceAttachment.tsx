import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Monitor, FolderOpen, Check, X, Search, Loader2, Unplug } from 'lucide-react';
import { notificationPublisher } from '../../lib/notifications';
import { api } from '../../lib/api';
import { useSessionWorkspace } from '../../hooks/useSessionWorkspace';
import { useWorkspaceStore } from '../../store/workspace-store';
import type { Workspace, Machine } from '../../types/api';

const notify = notificationPublisher('Workspaces');

export function WorkspaceAttachment({ sessionId, instanceName, busy, compact = false, className }: { sessionId: string | null; instanceName: string | null; busy: boolean; compact?: boolean; className?: string }) {
  const { workspace } = useSessionWorkspace(sessionId, instanceName);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal(); setLoading(true); setQuery('');
    let cancelled = false;
    Promise.all([api.get<Workspace[]>('/workspaces'), api.get<Machine[]>('/machines')]).then(([rows, hosts]) => {
      if (!cancelled) { setSpaces(rows); setMachines(hosts); }
    }).catch(error => notify.error(error instanceof Error ? error.message : 'Could not load workspaces')).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open]);
  useEffect(() => { setOpen(false); }, [sessionId]);
  async function attach(id: string | null) {
    if (!sessionId || busy) return;
    setSaving(true);
    try {
      await api.put(`/sessions/${sessionId}/workspace`, { workspace_id: id });
      window.dispatchEvent(new CustomEvent('sentinel:workspace-changed', { detail: { sessionId } }));
      setOpen(false);
    } catch (error) { notify.error(error instanceof Error ? error.message : 'Could not change workspace'); }
    finally { setSaving(false); }
  }
  const rows = spaces.filter(w => `${w.name} ${w.directory}`.toLowerCase().includes(query.toLowerCase()));
  return <>
    {compact ? <button type="button" disabled={!sessionId || busy} aria-label={workspace ? `Workspace: ${workspace.name}` : 'Attach a workspace'} title={workspace ? `${workspace.name} · ${workspace.directory}` : 'Attach a workspace'} data-attached={workspace ? 'true' : undefined} onClick={() => setOpen(true)} className={className}><Monitor size={16} className={workspace ? 'text-(--accent-solid)' : ''} /></button>
    : <button data-tour="workspace-attachment" disabled={!sessionId || busy} title={workspace ? `${workspace.name} · ${workspace.directory}` : 'Attach a workspace'} onClick={() => setOpen(true)} className="chat-header-pill inline-flex h-7 items-center gap-2 rounded-full border border-(--border-subtle) bg-(--surface-1) px-3 text-[10px] font-bold uppercase tracking-widest text-(--text-secondary) hover:text-(--text-primary) hover:bg-(--surface-2) disabled:opacity-50 transition-colors"><Monitor size={14} className={workspace ? 'text-(--accent-solid)' : ''} /><span className="max-w-36 truncate">{workspace?.name ?? 'Attach'}</span></button>}
    {open && createPortal(<dialog ref={dialog} onCancel={() => setOpen(false)} onClick={e => { if (e.target === e.currentTarget && !saving) setOpen(false); }} className="m-auto w-[min(560px,calc(100vw-48px))] rounded-2xl border border-(--border-subtle) bg-(--surface-0) text-(--text-primary) p-0 shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-xs" aria-label="Attach a workspace"><div className="p-5 space-y-4">
      <div className="flex justify-between items-center"><h2 className="text-sm font-semibold">Attach a workspace</h2><button aria-label="Close" onClick={() => setOpen(false)}><X size={18} /></button></div>
      <div className="relative"><Search size={15} className="absolute left-3 top-3 text-(--text-muted)" /><input autoFocus className="w-full h-10 pl-9 pr-3 rounded-full bg-(--surface-1) border border-(--border-subtle) text-sm outline-hidden focus:border-(--accent-solid)" placeholder="Search workspaces…" value={query} onChange={e => setQuery(e.target.value)} /></div>
      <div className="max-h-[45vh] overflow-y-auto space-y-2">{loading ? <Loader2 size={20} className="animate-spin mx-auto my-8" /> : rows.map(w => <button disabled={saving || busy} key={w.id} onClick={() => void attach(w.id)} className="w-full flex items-center gap-3 rounded-xl p-3 text-left border border-(--border-subtle) hover:bg-(--surface-1) transition-colors"><FolderOpen size={19} className="text-(--accent-solid) shrink-0" /><span className="flex-1 min-w-0"><span className="block text-sm font-semibold truncate">{w.name}</span><span className="block text-xs text-(--text-secondary) truncate mt-1">{machines.find(m => m.id === w.machine_id)?.name} · {w.directory}</span></span>{workspace?.id === w.id && <Check size={16} />}</button>)}{!loading && rows.length === 0 && <p className="text-sm text-(--text-secondary) text-center py-6">{spaces.length ? 'No matching workspaces' : 'Create a workspace to attach it to this chat.'}</p>}</div>
      <div className="flex justify-between gap-4 text-xs"><button className="text-(--text-secondary) hover:text-(--text-primary)" onClick={() => { useWorkspaceStore.getState().openTab('workspaces'); setOpen(false); }}>Manage workspaces</button>{workspace && <button disabled={saving || busy} className="inline-flex items-center gap-1.5 text-(--text-secondary) hover:text-(--text-primary)" onClick={() => void attach(null)}><Unplug size={13} />Detach</button>}</div>
    </div></dialog>, document.body)}
  </>;
}
