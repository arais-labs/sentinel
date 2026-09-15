import { ApprovalActions } from './ApprovalActions';
import { SESSION_PERMISSIONS_CHANGED, type ApprovalScope } from '../../lib/approvals';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';

type PendingApproval = {
  provider: string;
  approval_id: string;
  session_id: string | null;
  pending: boolean;
  can_resolve: boolean;
  command?: string | null;
  action?: string | null;
  description?: string | null;
};

/** Instance- and session-scoped controls; opening a preview never resolves anything. */
export function SessionPreviewApprovals({ instanceName, sessionId }: { instanceName: string; sessionId: string }) {
  const [items, setItems] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const resolving = useRef(false);
  const mounted = useRef(false);
  const revision = useRef(0);
  const base = `/instances/${encodeURIComponent(instanceName)}/approvals`;
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    const refresh = async () => {
      if (resolving.current) return;
      const startedAt = revision.current;
      try {
        const result = await api.get<{ items: PendingApproval[] }>(`${base}?status=pending&session_id=${encodeURIComponent(sessionId)}`);
        if (!stopped && !resolving.current && startedAt === revision.current) {
          setItems(result.items.filter(item => item.session_id === sessionId && item.pending));
          setError('');
        }
      } catch { if (!stopped) setError('Could not load approvals. Open the session to retry.'); }
      finally { if (!stopped) setLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { stopped = true; mounted.current = false; window.clearInterval(timer); };
  }, [base, sessionId]);

  async function resolve(item: PendingApproval, decision: 'approve' | 'reject', scope: ApprovalScope = 'once') {
    if (resolving.current || !item.can_resolve) return;
    resolving.current = true;
    revision.current++;
    setBusy(`${item.provider}:${item.approval_id}`);
    setError('');
    try {
      await api.post(`${base}/${encodeURIComponent(item.provider)}/${encodeURIComponent(item.approval_id)}/${decision}`, { scope });
      if (mounted.current) {
        setItems(current => current.filter(other => scope === 'session' ? other.action !== item.action : other.provider !== item.provider || other.approval_id !== item.approval_id));
        if (scope === 'session') window.dispatchEvent(new Event(SESSION_PERMISSIONS_CHANGED));
        setNotice(scope === 'session' ? 'Action allowed for this session.' : decision === 'approve' ? 'Approved once.' : 'Denied.');
      }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not resolve approval.');
    } finally {
      resolving.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  return <div className="session-preview-approvals">
    {loading && <p role="status">Loading approvals…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!loading && !error && !notice && !items.length && <p>No pending tool approvals.</p>}
    {items.map(item => <section key={`${item.provider}:${item.approval_id}`} className="session-preview-approval">
      <strong>{item.action || item.provider}</strong>
      {item.description && <p>{item.description}</p>}
      {item.command && <pre>{item.command}</pre>}
      {item.can_resolve && <ApprovalActions busy={busy !== null} sessionId={item.session_id} action={item.action}
        onResolve={(decision, scope) => void resolve(item, decision, scope)} />}
    </section>)}
  </div>;
}
