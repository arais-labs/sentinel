import { Check, Loader2, ShieldCheck, X } from 'lucide-react';
import type { ApprovalScope } from '../../lib/approvals';
import './approval-actions.css';

export function ApprovalActions({ busy = false, sessionId, action, onResolve }: {
  busy?: boolean;
  sessionId?: string | null;
  action?: string | null;
  onResolve: (decision: 'approve' | 'reject', scope?: ApprovalScope) => void;
}) {
  return <div className="session-approval-actions" aria-busy={busy}>
    <button type="button" disabled={busy} onClick={() => onResolve('reject', 'once')}>
      <X size={13} /> Deny
    </button>
    <button type="button" disabled={busy} onClick={() => onResolve('approve', 'once')}>
      {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Approve once
    </button>
    {sessionId && action && <button type="button" className="session-approval-allow" disabled={busy}
      title={`Allow all ${action} calls in this conversation, including different arguments. Revoke in Run settings → Session permissions.`}
      onClick={() => onResolve('approve', 'session')}>
      <ShieldCheck size={13} /> Allow for this session
    </button>}
  </div>;
}
