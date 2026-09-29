import { useEffect, useState } from 'react';
import { FolderOpen, Check, Loader2, Unplug } from 'lucide-react';
import { notificationPublisher } from '../../lib/notifications';
import { api } from '../../lib/api';
import { useSessionWorkspace } from '../../hooks/useSessionWorkspace';
import { useWorkspaceStore } from '../../store/workspace-store';
import { WorkspacePerformanceChip } from './WorkspaceRuntimeStats';
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
  useEffect(() => {
    if (!open) return;
    setLoading(true); setQuery('');
    let cancelled = false;
    api.get<Workspace[]>('/workspaces?include_runtime=false').then(rows => {
      if (!cancelled) setSpaces(rows);
    }).catch(error => { if (!cancelled) notify.error(error instanceof Error ? error.message : 'Could not load workspaces'); }).finally(() => { if (!cancelled) setLoading(false); });
    api.get<Machine[]>('/machines').then(hosts => {
      if (!cancelled) setMachines(hosts);
    }).catch(error => { if (!cancelled) notify.error(error instanceof Error ? error.message : 'Could not load machines'); });
    return () => { cancelled = true; };
  }, [open]);
  useEffect(() => { setOpen(false); }, [sessionId]);
  async function attach(id: string | null) {
    if (!sessionId || busy || saving) return;
    setSaving(true);
    try {
      await api.put(`/sessions/${sessionId}/workspace`, { workspace_id: id });
      window.dispatchEvent(new CustomEvent('sentinel:workspace-changed', { detail: { sessionId } }));
      setOpen(false);
    } catch (error) { notify.error(error instanceof Error ? error.message : 'Could not change workspace'); }
    finally { setSaving(false); }
  }
  const rows = spaces.filter(w => `${w.name} ${w.directory}`.toLowerCase().includes(query.toLowerCase()));
  return <WorkspacePerformanceChip key={sessionId} workspace={workspace} instanceName={instanceName} compact={compact} className={className}
    disabled={!sessionId} onOpen={() => { if (!workspace) setOpen(true); }} onClose={() => setOpen(false)}
    onChangeWorkspace={() => setOpen(value => !value)} workspaceMenu={open ? <div className="workspace-performance-options">
      <input aria-label="Search workspaces" placeholder="Search workspaces…" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="workspace-performance-choices">{loading && !spaces.length ? <Loader2 size={16} className="animate-spin" /> : rows.map(row => <button type="button" key={row.id} disabled={saving || busy} aria-pressed={workspace?.id === row.id}
        title={`${machines.find(machine => machine.id === row.machine_id)?.name ?? ''} · ${row.directory}`}
        onClick={() => void attach(row.id)}><FolderOpen size={14} /><span>{row.name}</span>{workspace?.id === row.id && <Check size={14} />}</button>)}
        {!loading && !rows.length && <p>{spaces.length ? 'No matching workspaces' : 'Create a workspace to attach it to this chat.'}</p>}</div>
      {workspace && <button type="button" disabled={saving || busy} onClick={() => void attach(null)}><Unplug size={14} />Detach workspace</button>}
      <button type="button" onClick={() => { useWorkspaceStore.getState().openTab('workspaces'); setOpen(false); }}>Manage workspaces</button>
    </div> : null} />;
}
