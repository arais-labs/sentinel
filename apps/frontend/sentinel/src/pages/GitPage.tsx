import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  CheckCircle2,
  Clock3,
  GitBranch,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  XCircle,
} from 'lucide-react';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import { useAuthStore } from '../store/auth-store';
import type {
  ApprovalListResponse,
  ApprovalRecord,
  GitAccount,
  GitAccountListResponse,
} from '../types/api';

interface GitAccountForm {
  name: string;
  host: string;
  scope_pattern: string;
  author_name: string;
  author_email: string;
  token_read: string;
  token_write: string;
}

const EMPTY_FORM: GitAccountForm = {
  name: '',
  host: 'github.com',
  scope_pattern: '*',
  author_name: '',
  author_email: '',
  token_read: '',
  token_write: '',
};

type ApprovalStatusFilter = 'pending' | 'approved' | 'rejected' | 'timed_out' | 'all';

function approvalTone(status: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (status === 'approved') return 'good';
  if (status === 'pending') return 'warn';
  if (status === 'rejected') return 'danger';
  if (status === 'timed_out') return 'default';
  return 'info';
}

function readMetadataString(approval: ApprovalRecord, key: string): string | null {
  const value = approval.metadata?.[key];
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

export function GitPage() {
  const role = useAuthStore((state) => state.role);
  const [accounts, setAccounts] = useState<GitAccount[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<GitAccountForm>(EMPTY_FORM);
  const [savingAccountId, setSavingAccountId] = useState<string | null>(null);
  const [editingAccountId, setEditingAccountId] = useState<string | null>(null);
  const [editForms, setEditForms] = useState<Record<string, GitAccountForm>>({});
  const [approvalStatusFilter, setApprovalStatusFilter] = useState<ApprovalStatusFilter>('pending');
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string | null>(null);

  const pendingCount = useMemo(
    () => approvals.filter((item) => item.status === 'pending').length,
    [approvals],
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const approvalQuery = new URLSearchParams();
      approvalQuery.set('provider', 'git');
      if (approvalStatusFilter !== 'all') approvalQuery.set('status', approvalStatusFilter);
      const [accountsPayload, approvalsPayload] = await Promise.all([
        api.get<GitAccountListResponse>('/git/accounts'),
        api.get<ApprovalListResponse>(`/approvals?${approvalQuery.toString()}`),
      ]);
      setAccounts(accountsPayload.items || []);
      setApprovals(approvalsPayload.items || []);
    } catch {
      toast.error('Failed to load Git controls');
    } finally {
      setLoading(false);
    }
  }, [approvalStatusFilter]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  if (role !== 'admin') {
    return <Navigate to="/settings" replace />;
  }

  async function refreshAll() {
    setRefreshing(true);
    try {
      await loadAll();
    } finally {
      setRefreshing(false);
    }
  }

  async function createAccount() {
    const required = [
      createForm.name,
      createForm.host,
      createForm.scope_pattern,
      createForm.author_name,
      createForm.author_email,
      createForm.token_read,
      createForm.token_write,
    ];
    if (required.some((value) => !value.trim())) {
      toast.error('All account fields are required');
      return;
    }
    setCreating(true);
    try {
      await api.post('/git/accounts', createForm);
      toast.success('Git account created');
      setCreateForm(EMPTY_FORM);
      await loadAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Create failed';
      toast.error(message);
    } finally {
      setCreating(false);
    }
  }

  function beginEdit(account: GitAccount) {
    setEditingAccountId(account.id);
    setEditForms((current) => ({
      ...current,
      [account.id]: {
        name: account.name,
        host: account.host,
        scope_pattern: account.scope_pattern,
        author_name: account.author_name,
        author_email: account.author_email,
        token_read: '',
        token_write: '',
      },
    }));
  }

  async function saveEdit(accountId: string) {
    const form = editForms[accountId];
    if (!form) return;
    setSavingAccountId(accountId);
    try {
      const payload: Record<string, string> = {
        name: form.name,
        host: form.host,
        scope_pattern: form.scope_pattern,
        author_name: form.author_name,
        author_email: form.author_email,
      };
      if (form.token_read.trim()) {
        payload.token_read = form.token_read.trim();
      }
      if (form.token_write.trim()) {
        payload.token_write = form.token_write.trim();
      }
      await api.patch(`/git/accounts/${accountId}`, payload);
      toast.success('Git account updated');
      setEditingAccountId(null);
      await loadAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Update failed';
      toast.error(message);
    } finally {
      setSavingAccountId(null);
    }
  }

  async function deleteAccount(accountId: string) {
    if (!window.confirm('Delete this Git account? This cannot be undone.')) {
      return;
    }
    setSavingAccountId(accountId);
    try {
      await api.delete(`/git/accounts/${accountId}`);
      toast.success('Git account deleted');
      if (editingAccountId === accountId) {
        setEditingAccountId(null);
      }
      await loadAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Delete failed';
      toast.error(message);
    } finally {
      setSavingAccountId(null);
    }
  }

  async function resolveApproval(approvalId: string, decision: 'approve' | 'reject') {
    setResolvingApprovalId(approvalId);
    try {
      await api.post(`/approvals/git/${approvalId}/${decision}`, {
        note: decision === 'approve' ? 'User approved action.' : 'User rejected action.',
      });
      toast.success(`Push ${decision}d`);
      await loadAll();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Approval decision failed';
      toast.error(message);
    } finally {
      setResolvingApprovalId(null);
    }
  }

  return (
    <AppShell
      title="Git Accounts"
      subtitle="Credential Routing & Push Approval Gate"
      actions={(
        <button
          onClick={() => void refreshAll()}
          className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors"
          aria-label="Refresh"
        >
          <RefreshCw size={18} className={refreshing ? 'animate-spin' : ''} />
        </button>
      )}
    >
      <div className="max-w-7xl mx-auto grid grid-cols-1 xl:grid-cols-[1.2fr_1fr] gap-6 items-start">
        <div className="space-y-5">
          <Panel className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-widest flex items-center gap-2">
                <GitBranch size={14} />
                Create Git Account
              </h2>
              <StatusChip label={`${accounts.length} accounts`} tone="info" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <input
                className="input-field h-10 text-xs"
                placeholder="Account name"
                value={createForm.name}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, name: event.target.value }))}
              />
              <input
                className="input-field h-10 text-xs"
                placeholder="Host (e.g. github.com)"
                value={createForm.host}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, host: event.target.value }))}
              />
              <input
                className="input-field h-10 text-xs"
                placeholder="Scope pattern (e.g. github.com/org/*)"
                value={createForm.scope_pattern}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, scope_pattern: event.target.value }))}
              />
              <input
                className="input-field h-10 text-xs"
                placeholder="Author name"
                value={createForm.author_name}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, author_name: event.target.value }))}
              />
              <input
                className="input-field h-10 text-xs"
                placeholder="Author email"
                value={createForm.author_email}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, author_email: event.target.value }))}
              />
              <div />
              <input
                type="password"
                className="input-field h-10 text-xs font-mono"
                placeholder="Read token"
                value={createForm.token_read}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, token_read: event.target.value }))}
              />
              <input
                type="password"
                className="input-field h-10 text-xs font-mono"
                placeholder="Write token"
                value={createForm.token_write}
                onChange={(event) => setCreateForm((prev) => ({ ...prev, token_write: event.target.value }))}
              />
            </div>

            <div className="flex justify-end">
              <button
                onClick={() => void createAccount()}
                className="btn-primary h-10 px-4 text-[10px] uppercase tracking-widest gap-2"
                disabled={creating}
              >
                {creating ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
                Add Account
              </button>
            </div>
          </Panel>

          <Panel className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-widest">Configured Accounts</h2>
              <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                Tokens are write-only in UI
              </span>
            </div>

            {loading ? (
              <p className="text-xs text-[color:var(--text-muted)]">Loading accounts...</p>
            ) : accounts.length === 0 ? (
              <p className="text-xs text-[color:var(--text-muted)]">No git accounts configured yet.</p>
            ) : (
              <div className="space-y-3">
                {accounts.map((account) => {
                  const isEditing = editingAccountId === account.id;
                  const draft = editForms[account.id];
                  return (
                    <Panel key={account.id} className="p-4 space-y-3 bg-[color:var(--surface-1)]">
                      {!isEditing ? (
                        <>
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold">{account.name}</p>
                              <p className="text-[11px] text-[color:var(--text-muted)]">
                                {account.host} · {account.scope_pattern}
                              </p>
                            </div>
                            <div className="flex items-center gap-2">
                              <StatusChip label={account.has_read_token ? 'read ok' : 'read missing'} tone={account.has_read_token ? 'good' : 'warn'} />
                              <StatusChip label={account.has_write_token ? 'write ok' : 'write missing'} tone={account.has_write_token ? 'good' : 'warn'} />
                            </div>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] text-[color:var(--text-secondary)]">
                            <div>Author: <span className="font-medium">{account.author_name}</span></div>
                            <div>Email: <span className="font-medium">{account.author_email}</span></div>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                              Updated {formatCompactDate(account.updated_at || account.created_at || '')}
                            </span>
                            <div className="flex items-center gap-2">
                              <button
                                className="btn-secondary h-8 px-3 text-[10px] uppercase tracking-widest"
                                onClick={() => beginEdit(account)}
                              >
                                Edit
                              </button>
                              <button
                                className="btn-secondary h-8 px-3 text-[10px] uppercase tracking-widest text-rose-500"
                                onClick={() => void deleteAccount(account.id)}
                                disabled={savingAccountId === account.id}
                              >
                                <Trash2 size={12} className="mr-1" />
                                Delete
                              </button>
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="space-y-3">
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                            <input
                              className="input-field h-9 text-xs"
                              value={draft?.name || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), name: event.target.value },
                              }))}
                            />
                            <input
                              className="input-field h-9 text-xs"
                              value={draft?.host || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), host: event.target.value },
                              }))}
                            />
                            <input
                              className="input-field h-9 text-xs"
                              value={draft?.scope_pattern || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), scope_pattern: event.target.value },
                              }))}
                            />
                            <input
                              className="input-field h-9 text-xs"
                              value={draft?.author_name || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), author_name: event.target.value },
                              }))}
                            />
                            <input
                              className="input-field h-9 text-xs"
                              value={draft?.author_email || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), author_email: event.target.value },
                              }))}
                            />
                            <div />
                            <input
                              type="password"
                              className="input-field h-9 text-xs font-mono"
                              placeholder="Set new read token (optional)"
                              value={draft?.token_read || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), token_read: event.target.value },
                              }))}
                            />
                            <input
                              type="password"
                              className="input-field h-9 text-xs font-mono"
                              placeholder="Set new write token (optional)"
                              value={draft?.token_write || ''}
                              onChange={(event) => setEditForms((current) => ({
                                ...current,
                                [account.id]: { ...(current[account.id] || EMPTY_FORM), token_write: event.target.value },
                              }))}
                            />
                          </div>
                          <div className="flex justify-end gap-2">
                            <button
                              className="btn-secondary h-8 px-3 text-[10px] uppercase tracking-widest"
                              onClick={() => setEditingAccountId(null)}
                            >
                              Cancel
                            </button>
                            <button
                              className="btn-primary h-8 px-3 text-[10px] uppercase tracking-widest"
                              onClick={() => void saveEdit(account.id)}
                              disabled={savingAccountId === account.id}
                            >
                              {savingAccountId === account.id ? <RefreshCw size={12} className="animate-spin mr-1" /> : null}
                              Save
                            </button>
                          </div>
                        </div>
                      )}
                    </Panel>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>

        <Panel className="p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-widest flex items-center gap-2">
              <ShieldAlert size={14} />
              Push Approvals
            </h2>
            <StatusChip label={`${pendingCount} pending`} tone={pendingCount > 0 ? 'warn' : 'good'} />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {(['pending', 'approved', 'rejected', 'timed_out', 'all'] as const).map((status) => (
              <button
                key={status}
                className={`btn-secondary h-8 px-3 text-[10px] uppercase tracking-widest ${approvalStatusFilter === status ? 'border-[color:var(--accent-solid)]' : ''}`}
                onClick={() => setApprovalStatusFilter(status)}
              >
                {status}
              </button>
            ))}
          </div>

          {loading ? (
            <p className="text-xs text-[color:var(--text-muted)]">Loading approvals...</p>
          ) : approvals.length === 0 ? (
            <p className="text-xs text-[color:var(--text-muted)]">No approvals for this filter.</p>
          ) : (
            <div className="space-y-3 max-h-[70vh] overflow-y-auto pr-1">
              {approvals.map((approval) => {
                const decisionBy = readMetadataString(approval, 'decision_by');
                return (
                  <Panel key={approval.approval_id} className="p-4 bg-[color:var(--surface-1)] space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <StatusChip label={approval.status} tone={approvalTone(approval.status)} />
                      <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                        {formatCompactDate(approval.created_at || '')}
                      </span>
                    </div>

                    <div className="space-y-1">
                      <p className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                        Repository
                      </p>
                      <p className="text-xs font-mono break-all">{readMetadataString(approval, 'repo_url') ?? '—'}</p>
                    </div>

                    <div className="space-y-1">
                      <p className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                        Command
                      </p>
                      <p className="text-xs font-mono break-all">{approval.command ?? readMetadataString(approval, 'command') ?? '—'}</p>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-[11px] text-[color:var(--text-secondary)]">
                      <div className="flex items-center gap-1">
                        <Clock3 size={12} />
                        Expires: {formatCompactDate(approval.expires_at || '')}
                      </div>
                      <div className="flex items-center gap-1">
                        <GitBranch size={12} />
                        Remote: {readMetadataString(approval, 'remote_name') ?? '—'}
                      </div>
                    </div>

                    {approval.status === 'pending' ? (
                      <div className="flex items-center justify-end gap-2">
                        <button
                          className="btn-secondary h-8 px-3 text-[10px] uppercase tracking-widest text-rose-500"
                          onClick={() => void resolveApproval(approval.approval_id, 'reject')}
                          disabled={resolvingApprovalId === approval.approval_id}
                        >
                          <XCircle size={12} className="mr-1" />
                          Reject
                        </button>
                        <button
                          className="btn-primary h-8 px-3 text-[10px] uppercase tracking-widest"
                          onClick={() => void resolveApproval(approval.approval_id, 'approve')}
                          disabled={resolvingApprovalId === approval.approval_id}
                        >
                          <CheckCircle2 size={12} className="mr-1" />
                          Approve
                        </button>
                      </div>
                    ) : (
                      <p className="text-[11px] text-[color:var(--text-muted)]">
                        {decisionBy ? `Resolved by ${decisionBy}` : 'Resolved'}
                        {approval.decision_note ? ` · ${approval.decision_note}` : ''}
                      </p>
                    )}
                  </Panel>
                );
              })}
            </div>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}
