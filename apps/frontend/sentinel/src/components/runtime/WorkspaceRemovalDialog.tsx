import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2 } from 'lucide-react';
import { api } from '../../lib/api';
import type { Workspace } from '../../types/api';

type Removal = { sessions: { id: string; title: string; running: boolean }[] };

export function WorkspaceRemovalDialog({ workspace, onClose, onRemoved }: {
  workspace: Workspace; onClose: () => void; onRemoved: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [details, setDetails] = useState<Removal | null>(null);
  const [detach, setDetach] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [checkError, setCheckError] = useState('');
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => {
    if (saving) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const value = await api.get<Removal>(`/workspaces/${workspace.id}/removal`);
        if (active) { setDetails(value); setCheckError(''); }
      } catch (reason) {
        if (active) setCheckError(reason instanceof Error ? reason.message : 'Could not check linked sessions');
      } finally {
        if (active) timer = setTimeout(() => void check(), 2000);
      }
    }
    void check();
    return () => { active = false; clearTimeout(timer); };
  }, [workspace.id, saving]);
  const linked = !!details?.sessions.length;
  const running = details?.sessions.some(session => session.running);
  async function remove() {
    setSaving(true); setError('');
    try {
      await api.delete(`/workspaces/${workspace.id}?detach_sessions=${detach}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove workspace');
      setSaving(false);
      return;
    }
    onClose();
    await onRemoved();
  }
  return createPortal(<dialog ref={dialog} aria-labelledby="workspace-removal-title"
    onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}
    className="m-auto w-[calc(100%-3rem)] max-w-md rounded-2xl border border-(--border-subtle) bg-(--surface-1) p-6 text-(--text-primary) backdrop:bg-black/50 backdrop:backdrop-blur-xs">
    <div className="space-y-5">
      <h2 id="workspace-removal-title" className="font-semibold">Remove {workspace.name}?</h2>
      <p className="text-sm text-(--text-secondary)">This deletes its environment, installed tools, and private data, and stops its terminals. Your project folder and conversations are kept.</p>
      {!details && !checkError && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 size={14} className="animate-spin" />Checking linked sessions…</p>}
      {linked && <div className="space-y-3">
        <p className="text-sm text-(--text-secondary)">Linked to {details.sessions.length} {details.sessions.length === 1 ? 'session' : 'sessions'}:</p>
        <ul className="max-h-36 overflow-y-auto space-y-2 text-sm">{details.sessions.map(session => <li key={session.id} className="flex items-center justify-between gap-3"><span className="truncate" title={session.title}>{session.title}</span>{session.running && <span className="shrink-0 text-(--text-muted)">Agent active</span>}</li>)}</ul>
        <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-0.5" checked={detach} disabled={saving} onChange={event => setDetach(event.target.checked)} /><span>Detach this workspace from these sessions</span></label>
      </div>}
      {running && <p role="status" className="text-sm text-(--text-secondary)">Wait for all agents in these sessions to finish, or stop them first. This updates automatically.</p>}
      {(error || checkError) && <p role="alert" className="text-sm text-red-400">{error || checkError}</p>}
      <div className="flex justify-end gap-3">
        <button autoFocus disabled={saving} className="btn-secondary h-9 px-4 text-xs" onClick={onClose}>Cancel</button>
        <button disabled={saving || !details || !!checkError || running || (linked && !detach)} className="btn-primary h-9 px-4 gap-2 text-xs" onClick={() => void remove()}>{saving ? <><Loader2 size={14} className="animate-spin" />Removing…</> : linked ? 'Detach and remove' : 'Remove workspace'}</button>
      </div>
    </div>
  </dialog>, document.body);
}
