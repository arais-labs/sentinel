import { ChevronDown, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { SESSION_PERMISSIONS_CHANGED } from '../../lib/approvals';
import './approval-actions.css';

export type Grant = { id: string; action: string; session_id: string };

/** Grants that let actions run without asking in one conversation, kept in sync across surfaces. */
export function useSessionGrants(instanceName: string, sessionId: string) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const requestVersion = useRef(0);
  const base = `/instances/${encodeURIComponent(instanceName)}/approvals/sessions/${encodeURIComponent(sessionId)}/grants`;
  useEffect(() => {
    let stopped = false;
    const refresh = () => {
      const version = ++requestVersion.current;
      void api.get<Grant[]>(base).then(items => {
        if (!stopped && version === requestVersion.current) { setGrants(items); setError(''); }
      }).catch(() => {
        if (!stopped && version === requestVersion.current) setError('Could not load permissions.');
      }).finally(() => { if (!stopped && version === requestVersion.current) setLoading(false); });
    };
    refresh();
    window.addEventListener(SESSION_PERMISSIONS_CHANGED, refresh);
    return () => { stopped = true; window.removeEventListener(SESSION_PERMISSIONS_CHANGED, refresh); };
  }, [base, revision]);

  async function revoke(grant: Grant) {
    if (busy) return;
    setBusy(grant.id);
    requestVersion.current++;
    try {
      await api.delete(`${base}/${encodeURIComponent(grant.id)}`);
      requestVersion.current++;
      setGrants(items => items.filter(item => item.id !== grant.id));
      setError('');
      window.dispatchEvent(new Event(SESSION_PERMISSIONS_CHANGED));
    } catch { setError('Could not revoke permission. Try again.'); }
    finally { setBusy(null); }
  }

  return { grants, loading, error, busy, revoke, retry: () => setRevision(value => value + 1) };
}

export function SessionPermissions({ instanceName, sessionId }: { instanceName: string; sessionId: string }) {
  const [expanded, setExpanded] = useState(false);
  const { grants, loading, error, busy, revoke, retry } = useSessionGrants(instanceName, sessionId);

  return <section className="run-settings-section session-permissions">
    <button type="button" className="run-settings-section-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      <span className="text-(--text-secondary)"><ShieldCheck size={11} /></span>
      <span className="run-settings-section-label">Session permissions</span>
      <span className="run-settings-section-value">{loading ? '…' : grants.length}</span>
      <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${expanded ? 'rotate-180' : ''}`} />
    </button>
    <div className="session-permissions-body" hidden={!expanded}>
    {loading ? <p role="status">Loading…</p> : <>
      <p>{grants.length ? 'These actions can run without asking in this conversation.' : 'No actions automatically approved for this conversation.'}</p>
      <ul>{grants.map(grant => <li key={grant.id}>
        <code>{grant.action}</code>
        <button type="button" disabled={busy !== null} aria-label={`Revoke ${grant.action}`}
          onClick={() => void revoke(grant)}>{busy === grant.id ? 'Revoking…' : 'Revoke'}</button>
      </li>)}</ul>
    </>}
    {error && <p role="alert">{error} <button type="button" onClick={retry}>Retry</button></p>}
    </div>
  </section>;
}
