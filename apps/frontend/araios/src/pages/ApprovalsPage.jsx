import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { APPROVAL_STATUS } from '../lib/constants';
import { fmtDate } from '../lib/utils';
import { IconCheckCircle } from '../components/Icons';

export default function ApprovalsPage({ notify, setRefresh, onApprovalResolved }) {
  const [allApprovals, setAllApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('pending');
  const [processingId, setProcessingId] = useState('');

  const load = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      setError('');
      const data = await api('/api/approvals');
      setAllApprovals(data.approvals || []);
    } catch (err) {
      setError(err.message || 'Failed to load approvals');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { setRefresh(load); }, []);

  useEffect(() => { load(); }, []);

  const approvals = allApprovals.filter(a => a.status === statusFilter);
  const counts = { pending: 0, approved: 0, rejected: 0 };
  allApprovals.forEach(a => { if (counts[a.status] !== undefined) counts[a.status]++; });

  const handleApprove = async (approvalId) => {
    try {
      setProcessingId(approvalId);
      await api(`/api/approvals/${approvalId}/approve`, { method: 'POST' });
      notify('Approval executed');
      await load(true);
      onApprovalResolved?.();
    } catch (err) {
      notify(err.message || 'Failed to approve', 'warn');
    } finally {
      setProcessingId('');
    }
  };

  const handleReject = async (approvalId) => {
    try {
      setProcessingId(approvalId);
      await api(`/api/approvals/${approvalId}/reject`, { method: 'POST' });
      notify('Request rejected');
      await load(true);
    } catch (err) {
      notify(err.message || 'Failed to reject', 'warn');
    } finally {
      setProcessingId('');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="sub-header">
        <div className="sub-header-left">
          {['pending', 'approved', 'rejected'].map(s => (
            <div
              key={s}
              className={`stat-chip ${statusFilter === s ? 'active' : ''}`}
              onClick={() => setStatusFilter(s)}
            >
              <span className="stat-label">{s}</span>
              <strong className="stat-value">{counts[s]}</strong>
            </div>
          ))}
        </div>
      </div>

      <div className="detail-content" style={{ padding: '24px' }}>
        {loading ? (
          <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Awaiting telemetry...</div>
        ) : approvals.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full opacity-50 gap-4">
             <IconCheckCircle size={48} strokeWidth={1} />
             <p className="text-sm font-medium uppercase tracking-widest">Protocol Clear</p>
          </div>
        ) : (
          <div style={{ maxWidth: '900px', margin: '0 auto', width: '100%', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {approvals.map((approval) => {
              const statusInfo = APPROVAL_STATUS[approval.status] || { label: approval.status, tone: 'neutral' };
              const isProcessing = processingId === approval.id;

              return (
                <article key={approval.id} className="panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyBetween: 'space-between', width: '100%' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1 }}>
                      <span className={`badge badge-${statusInfo.tone}`}>{statusInfo.label}</span>
                      <span className="text-xs font-mono font-bold text-[color:var(--text-primary)]">{approval.action}</span>
                      {approval.resource && (
                        <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest bg-[color:var(--surface-2)] px-2 py-0.5 rounded">
                          {approval.resource}{approval.resourceId ? `/${approval.resourceId}` : ''}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono text-[color:var(--text-muted)]">{fmtDate(approval.createdAt)}</span>
                  </div>

                  {approval.description && (
                    <p className="text-sm leading-relaxed text-[color:var(--text-secondary)] font-medium">
                      {approval.description}
                    </p>
                  )}

                  {approval.payload && (
                    <div style={{ backgroundColor: 'var(--surface-1)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                       <pre style={{ margin: 0, fontSize: '11px', fontFamily: 'monospace', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                          {JSON.stringify(approval.payload, null, 2)}
                       </pre>
                    </div>
                  )}

                  {approval.status === 'pending' && (
                    <div className="flex items-center gap-3 pt-2">
                      <button
                        className="btn-primary"
                        style={{ flex: 1 }}
                        disabled={isProcessing}
                        onClick={() => handleApprove(approval.id)}
                      >
                        {isProcessing ? 'Executing...' : 'Authorize Action'}
                      </button>
                      <button
                        className="btn-secondary"
                        style={{ flex: 1 }}
                        disabled={isProcessing}
                        onClick={() => handleReject(approval.id)}
                      >
                        Decline
                      </button>
                    </div>
                  )}

                  {approval.resolvedAt && (
                    <div className="pt-4 border-t border-[color:var(--border-subtle)] flex items-center justify-between text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
                      <span>Resolution protocol complete</span>
                      <span>{fmtDate(approval.resolvedAt)} • BY {approval.resolvedBy || 'SYSTEM'}</span>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
