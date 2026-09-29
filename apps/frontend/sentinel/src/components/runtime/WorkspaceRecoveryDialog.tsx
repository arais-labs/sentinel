import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, Wrench, X } from 'lucide-react';
import { api } from '../../lib/api';
import type { Workspace } from '../../types/api';
import './remote-runtime-dialog.css';

export function WorkspaceRecoveryDialog({ workspace, onClose, onStarted, onReinstall, onRemove }: {
  workspace: Workspace; onClose: () => void; onStarted: () => Promise<void>;
  onReinstall: () => void; onRemove: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const backdrop = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close(); }, []);
  async function recover() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await api.post(`/workspaces/${workspace.id}/recover`, { confirmed: true }, { timeoutMs: 20_000 });
      onClose();
      await onStarted();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start recovery'); setBusy(false); }
  }
  return createPortal(<dialog ref={dialog} className="remote-runtime-dialog m-auto w-[calc(100%-2rem)] max-w-md rounded-2xl border border-(--border-subtle) bg-(--surface-1) p-0 text-sm text-(--text-primary) backdrop:bg-black/50 backdrop:backdrop-blur-xs" aria-labelledby="workspace-recovery-title"
    onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}
    onPointerDown={e => { backdrop.current = e.target === e.currentTarget; }}
    onClick={e => { if (backdrop.current && e.target === e.currentTarget && !busy) onClose(); backdrop.current = false; }}>
    <div className="flex items-start gap-3 p-5">
      <Wrench size={22} /><div><h2 id="workspace-recovery-title" className="text-base font-semibold">Recover workspace</h2><p className="mt-1 text-(--text-muted)">{workspace.name}</p></div>
      <button type="button" className="runtime-dialog-close ml-auto" aria-label="Close" disabled={busy} onClick={onClose}><X size={18} /></button>
    </div>
    <div className="space-y-4 px-5 pb-5">
      <p>Stop the VM, save a disk backup, repair its filesystem, and restart this workspace.</p>
      <p className="text-(--text-secondary)">Running commands and unsaved work will be lost. Your project folder is kept. If repair fails, the workspace stays stopped and keeps its backup.</p>
      {workspace.container_error && <details className="text-xs text-(--text-secondary)"><summary className="cursor-pointer">Recovery details</summary><p className="mt-2 break-words [overflow-wrap:anywhere]">{workspace.container_error}</p></details>}
      <div className="space-y-3 rounded-xl border border-(--border-subtle) p-4">
        <p className="font-medium">Can’t recover, or don’t need the Linux files?</p>
        <p className="text-(--text-secondary)">Reinstall for a fresh Linux environment, or delete the workspace. Both erase VM-only data and recovery backups, but keep your project folder and conversations. You’ll confirm before anything is erased.</p>
        <div className="flex flex-wrap gap-2">
          <button disabled={busy} className="btn-secondary h-9 px-3 text-xs" onClick={onReinstall}>Reinstall Linux…</button>
          <button disabled={busy} className="btn-secondary h-9 px-3 text-xs" onClick={onRemove}>Delete workspace…</button>
        </div>
      </div>
      {error && <p role="alert" className="workspace-container-error">{error}</p>}
    </div>
    <footer className="flex flex-wrap justify-end gap-2 border-t border-(--border-subtle) p-4">
      <button className="btn-secondary h-9 px-4 text-xs" disabled={busy} onClick={onClose}>Cancel</button>
      <button className="btn-primary h-9 px-4 gap-2 text-xs" disabled={busy} onClick={() => void recover()}>{busy ? <><Loader2 size={14} className="animate-spin" />Starting recovery…</> : 'Recover and restart'}</button>
    </footer>
  </dialog>, document.body);
}
