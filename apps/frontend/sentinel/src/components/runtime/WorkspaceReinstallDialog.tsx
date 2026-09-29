import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader2 } from 'lucide-react';
import { api } from '../../lib/api';
import type { Workspace } from '../../types/api';
import type { WorkspaceDraft } from './WorkspaceEditor';

export function WorkspaceReinstallDialog({ workspace, settings, onClose, onStarted }: {
  workspace: Workspace; settings?: WorkspaceDraft | null; onClose: () => void; onStarted: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { const node = dialog.current; node?.showModal(); return () => node?.close(); }, []);
  async function reinstall() {
    if (busy || !confirmed) return;
    setBusy(true); setError('');
    try {
      await api.post(`/workspaces/${workspace.id}/reinstall`, { confirmed: true, ...(settings ? { settings: {
        name: settings.name, directory: settings.directory, distribution: settings.distribution,
        desktop: settings.desktop, browser: settings.browser, development_tools: settings.development_tools,
        resources: settings.resources, revision: workspace.revision,
      } } : {}) });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start reinstall');
      setBusy(false); return;
    }
    onClose();
    await onStarted();
  }
  return createPortal(<dialog ref={dialog} aria-labelledby="workspace-reinstall-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    className="m-auto w-[calc(100%-3rem)] max-w-md rounded-2xl border border-(--border-subtle) bg-(--surface-1) p-6 text-(--text-primary) backdrop:bg-black/50 backdrop:backdrop-blur-xs">
    <div className="space-y-5">
      <h2 id="workspace-reinstall-title" className="font-semibold">{settings ? `Change OS and reinstall ${workspace.name}?` : `Reinstall Linux for ${workspace.name}?`}</h2>
      {settings && <p className="text-sm text-(--text-secondary)">The workspace OS will change from {workspace.distribution ?? 'alpine'} to {settings.distribution}. Your other edits will be applied to the rebuilt workspace.</p>}
      <p className="text-sm text-(--text-secondary)">This stops the workspace and permanently erases its private Linux disk and all recovery backups, including VM-only files, installed packages, browser profiles, and desktop settings. There is no automatic backup. This cannot be undone.</p>
      {workspace.recovery_available && <p className="text-sm text-(--text-secondary)">You can rebuild Linux without completing recovery. The damaged environment will be replaced with a fresh installation.</p>}
      <p className="text-sm text-(--text-secondary)">Your mounted project folder is kept: <span className="break-all">{workspace.directory}</span>{settings?.directory !== workspace.directory && settings?.directory ? <>. The newly selected folder is also kept: <span className="break-all">{settings.directory}</span></> : null}. Linked conversations are kept. Linux is rebuilt from the selected OS image, then your selected tools and desktop are installed again.</p>
      <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-0.5" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>I understand that VM-only files and all recovery backups will be permanently deleted.</span></label>
      {error && <p role="alert" className="workspace-container-error">{error}</p>}
      <div className="flex justify-end gap-3">
        <button autoFocus disabled={busy} className="btn-secondary h-9 px-4 text-xs" onClick={onClose}>Cancel</button>
        <button disabled={busy || !confirmed} className="btn-primary h-9 px-4 gap-2 text-xs" onClick={() => void reinstall()}>{busy ? <><Loader2 size={14} className="animate-spin" />Starting reinstall…</> : 'Erase Linux disk and reinstall'}</button>
      </div>
    </div>
  </dialog>, document.body);
}
