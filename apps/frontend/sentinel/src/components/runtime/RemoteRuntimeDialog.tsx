import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Loader2, Server, ShieldCheck, X } from 'lucide-react';
import { api } from '../../lib/api';
import type { Machine } from '../../types/api';
import '../session/chat-header.css';
import './remote-runtime-dialog.css';

type AffectedWorkspace = { id: string; name: string; instance?: string | null };
type Inspection = { host_key: string; worker_id?: string; fingerprint: string; installed: boolean; path: string; identity_verified: boolean; host_key_changed: boolean; installed_version?: string; available_version?: string; update_phase?: string; update_warning?: string; progress?: string; workspaces?: AffectedWorkspace[]; restart_check_failed?: boolean };
type Verification = { checks: { name: string; status: 'passed' | 'warning' | 'failed'; detail: string }[] };

export function RemoteRuntimeDialog({ machine, onClose, onInstalled }: { machine: Machine; onClose: () => void; onInstalled: () => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const mounted = useRef(false);
  const updating = useRef(false);
  const backdropPress = useRef(false);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [trusted, setTrusted] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [complete, setComplete] = useState(false);
  const [progress, setProgress] = useState('');
  const [verification, setVerification] = useState<Verification | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState('');
  const endpoint = `/machines/${machine.id}/runtime`;
  async function loadPlan() {
    setLoading(true); setError('');
    try {
      const value = await api.get<Inspection>(`${endpoint}/plan`, { timeoutMs: 60_000 });
      if (mounted.current) { setInspection(value); setTrusted(false); }
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (mounted.current) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    dialog.current?.showModal();
    void loadPlan();
    return () => { mounted.current = false; dialog.current?.close(); };
    // A dialog belongs to one machine and is unmounted when it closes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [machine.id]);
  useEffect(() => {
    if (!installing) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api.get<Inspection>(endpoint);
        if (active && updating.current && value.progress) setProgress(value.progress);
      } catch { /* The install request reports failures. */ }
      if (active) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [installing, endpoint]);
  async function verify() {
    setVerifying(true); setVerification(null); setVerifyError('');
    try {
      const value = await api.post<Verification>(`${endpoint}/verify`, {}, { timeoutMs: 80_000 });
      if (mounted.current) setVerification(value);
    } catch (reason) { if (mounted.current) setVerifyError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (mounted.current) setVerifying(false); }
  }
  const affected = inspection?.workspaces ?? [];
  const connecting = inspection?.installed && inspection.worker_id && !inspection.identity_verified;
  const recovery = inspection?.update_phase && !['complete', 'rolled_back'].includes(inspection.update_phase);
  const action = connecting ? 'Connect' : !inspection?.installed ? 'Install' : recovery ? 'Recover' : inspection.installed_version && inspection.installed_version === inspection.available_version ? 'Repair' : 'Update';
  const blocked = loading || !inspection || inspection.host_key_changed || (!inspection.identity_verified && !trusted) || installing || verifying || Boolean(inspection.progress);
  async function install() {
    if (blocked || updating.current || !inspection) return;
    updating.current = true;
    setInstalling(true); setError(''); setNotice(''); setProgress('Preparing runtime…');
    try {
      if (connecting) {
        await api.post(`${endpoint}/connect`, { host_key: inspection.host_key });
        await onInstalled();
        onClose();
        return;
      }
      const result = await api.post<{ approval_required?: boolean; workspaces?: string[]; workspace_details?: AffectedWorkspace[]; warning?: string }>(endpoint, {
        approved: true, reinstall: inspection.installed, host_key: inspection.host_key,
        approved_workspaces: affected.map(workspace => workspace.id),
      }, { timeoutMs: 35 * 60_000 });
      if (!mounted.current) return;
      if (result.approval_required) {
        setInspection(value => value ? { ...value, workspaces: result.workspace_details ?? (result.workspaces ?? []).map(id => ({ id, name: 'Unregistered workspace' })), restart_check_failed: false } : value);
        setNotice('The running workspace list changed. Review it below and confirm again. No workspaces were stopped.');
        return;
      }
      setComplete(true); setNotice(result.warning ?? '');
      try { await onInstalled(); }
      catch { if (mounted.current) setNotice('Runtime installed. Close this dialog and refresh the machine list.'); }
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally {
      updating.current = false;
      if (mounted.current) { setInstalling(false); setProgress(''); }
    }
  }
  return createPortal(<dialog ref={dialog} aria-labelledby="remote-runtime-title" onCancel={event => { event.preventDefault(); if (!updating.current) onClose(); }}
    onPointerDown={event => { backdropPress.current = event.target === event.currentTarget; }}
    onClick={event => {
      if (!backdropPress.current || event.target !== event.currentTarget || updating.current) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}
    className="remote-runtime-dialog m-auto max-h-[85vh] w-[calc(100%-2rem)] max-w-lg overflow-hidden rounded-2xl border border-(--border-subtle) bg-(--surface-1) p-0 text-(--text-primary) backdrop:bg-black/50 backdrop:backdrop-blur-xs">
    <div className="flex max-h-[85vh] flex-col">
      <header className="flex shrink-0 items-start gap-3 p-5"><Server size={20} className="mt-0.5 shrink-0 text-(--accent-solid)" /><div className="min-w-0 flex-1"><h2 id="remote-runtime-title" className="font-semibold">{complete ? 'Runtime ready' : loading ? 'Checking runtime' : `${action} runtime`}</h2><p className="mt-1 break-words text-sm text-(--text-secondary)">{machine.name}</p></div><button type="button" aria-label="Close" disabled={installing} onClick={() => { if (!updating.current) onClose(); }} className="runtime-dialog-close"><X size={19} aria-hidden="true" /></button></header>
      <div className="min-h-0 space-y-4 overflow-y-auto px-5 pb-5 text-sm">
        {loading && <p className="flex items-center gap-2 text-(--text-secondary)" role="status"><Loader2 size={14} className="animate-spin" />Checking runtime and running workspaces…</p>}
        {complete ? <p className="flex items-center gap-2" role="status"><Check size={16} className="text-(--accent-solid)" />Runtime installed successfully.</p> : inspection && !loading && <>
          <p className="text-(--text-secondary)">{connecting ? 'Connect to this worker and discover its workspaces. Nothing will restart.' : action === 'Repair' ? 'Replace the installed runtime with a fresh copy.' : action === 'Recover' ? 'Finish the interrupted runtime update.' : action === 'Install' ? 'Set up Sentinel to run workspaces on this machine.' : 'Install the runtime included with this app.'}</p>
          {notice && <p role="status" className="text-amber-400">{notice}</p>}
          {inspection.host_key_changed ? <p role="alert" className="text-rose-400">This machine’s SSH identity changed. Verify its identity before updating.</p> : !inspection.identity_verified && <div className="space-y-2 rounded-xl bg-(--surface-0) p-3"><p className="text-(--text-secondary)">First connection · SSH fingerprint</p><p className="break-all font-mono text-xs select-text">{inspection.fingerprint}</p><label className="flex items-start gap-2"><input type="checkbox" checked={trusted} disabled={installing} onChange={event => setTrusted(event.target.checked)} className="mt-1 accent-(--accent-solid)" />I trust this machine’s SSH identity.</label></div>}
          {!inspection.host_key_changed && !connecting && <div className="space-y-2 rounded-xl bg-(--surface-0) p-3">
            {inspection.restart_check_failed && <p role="status" className="text-amber-400">Could not check which workspaces are running. Any running workspace listed here may need to restart.</p>}
            {affected.length > 0 ? <><p className="font-medium">{inspection.restart_check_failed ? 'Workspaces that may restart' : `Will restart ${affected.length === 1 ? '1 workspace' : `${affected.length} workspaces`}`}</p><ul className="space-y-1">{affected.map(workspace => <li key={workspace.id} className="break-words"><span>{workspace.name}</span>{workspace.instance && <span className="text-xs text-(--text-muted)"> · {workspace.instance}</span>}{workspace.name === 'Unregistered workspace' && <span className="block font-mono text-xs text-(--text-muted)">{workspace.id}</span>}</li>)}</ul><p className="text-(--text-secondary)">Files and tools stay. Running commands and terminals will stop.</p></> : <p className="text-(--text-secondary)">{inspection.restart_check_failed ? 'Restart approval will be requested if a running workspace is found.' : 'No running workspaces need to restart.'} Files and tools stay.</p>}
          </div>}
          <section className="runtime-details" aria-label="Runtime details">
            <div className="runtime-details-heading chat-header-actions">
              <h3>Runtime details</h3>
              {inspection.installed && !inspection.host_key_changed && <button
                type="button" disabled={verifying || installing || Boolean(inspection.progress)}
                onClick={() => void verify()} className="chat-header-pill runtime-verify"
                title="Check runtime files and service without changing workspaces"
              >
                {verifying ? <Loader2 size={12} className="animate-spin" aria-hidden="true" /> : <ShieldCheck size={12} aria-hidden="true" />}
                <span>{verifying ? 'Verifying…' : 'Verify runtime'}</span>
              </button>}
            </div>
            <dl className="runtime-metadata">
              <div><dt>Release</dt><dd>{inspection.installed_version ?? 'Not installed'} → {inspection.available_version ?? 'Prepared during installation'}</dd></div>
              <div><dt>Location</dt><dd>{inspection.path}</dd></div>
              <div><dt>SSH identity{inspection.identity_verified && <span className="runtime-identity-verified"><Check size={11} aria-hidden="true" />Verified</span>}</dt><dd>{inspection.fingerprint}</dd></div>
            </dl>
            {verification && <ul className="runtime-verification-results" aria-live="polite">{verification.checks.map(check => <li key={check.name}>
              <p className={check.status === 'failed' ? 'text-rose-600 dark:text-rose-400' : check.status === 'warning' ? 'text-amber-700 dark:text-amber-400' : ''}>{check.name} · {check.status === 'passed' ? 'Passed' : check.status === 'failed' ? 'Failed' : 'Needs attention'}</p>
              <p className="text-(--text-muted)">{check.detail}</p>
            </li>)}</ul>}
            {verifyError && <p role="alert" className="mt-3 text-xs text-rose-600 dark:text-rose-400">Verification could not finish: {verifyError}</p>}
          </section>
        </>}
        {complete && notice && <p role="status" className="text-(--text-secondary)">{notice}</p>}
        {installing && <p className="flex items-center gap-2 text-(--text-secondary)" role="status"><Loader2 size={14} className="shrink-0 animate-spin" />{progress}</p>}
        {!installing && inspection?.progress && <p role="status">Another runtime update is in progress. {inspection.progress}</p>}
        {error && <p role="alert" className="break-words text-rose-400">{error}</p>}
      </div>
      <footer className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-(--border-subtle) p-4">
        <button autoFocus disabled={installing} onClick={onClose} className="btn-secondary h-9 px-4 text-xs">{complete ? 'Done' : 'Cancel'}</button>
        {!complete && (!inspection || inspection.progress) && !loading ? <button onClick={() => void loadPlan()} className="btn-primary h-9 px-4 text-xs">Check again</button> : !complete && <button disabled={blocked} onClick={() => void install()} className="btn-primary h-9 px-4 text-xs">{installing ? `${connecting ? 'Connecting' : action === 'Repair' ? 'Repairing' : action === 'Recover' ? 'Recovering' : action === 'Install' ? 'Installing' : 'Updating'}…` : !connecting && affected.length ? `${action} & restart` : `${action} runtime`}</button>}
      </footer>
    </div>
  </dialog>, document.body);
}
