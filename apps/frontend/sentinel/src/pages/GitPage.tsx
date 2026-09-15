import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Check, ExternalLink, GitBranch, KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { notificationPublisher } from '../lib/notifications';

import './git-page.css';

import { SettingsIntegrationFrame } from '../components/SettingsIntegrationFrame';
import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import type { GitAccount, GitAccountListResponse } from '../types/api';

const notify = notificationPublisher('Git');

interface AccountDetails {
  name: string;
  scope_pattern: string;
  author_name: string;
  author_email: string;
}

const EMPTY_DETAILS: AccountDetails = { name: '', scope_pattern: '*', author_name: '', author_email: '' };
const DETAILS_FIELDS: { key: keyof AccountDetails; label: string; placeholder: string }[] = [
  { key: 'name', label: 'Account name', placeholder: 'Your GitHub username by default' },
  { key: 'scope_pattern', label: 'Repository routing', placeholder: 'owner/* or * for any repository' },
  { key: 'author_name', label: 'Commit author', placeholder: 'Your GitHub profile name by default' },
  { key: 'author_email', label: 'Commit email', placeholder: 'Your public or GitHub noreply email by default' },
];

function DetailsFields({ value, onChange }: { value: AccountDetails; onChange: (value: AccountDetails) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {DETAILS_FIELDS.map(({ key, label, placeholder }) => (
        <label key={key} className="space-y-2 text-xs text-(--text-secondary)">
          <span>{label}</span>
          <input className="input-field h-10 text-sm w-full" value={value[key]} placeholder={placeholder}
            onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
        </label>
      ))}
    </div>
  );
}

type TokenKind = 'fine-grained' | 'classic';

function tokenCreationUrl(kind: TokenKind, owner: string) {
  if (kind === 'classic') {
    const params = new URLSearchParams({ description: 'Sentinel', scopes: 'repo,read:org,workflow' });
    return `https://github.com/settings/tokens/new?${params}`;
  }
  const params = new URLSearchParams({
    name: 'Sentinel',
    description: 'Git operations and pull requests from my local Sentinel app',
    expires_in: '30',
    contents: 'write',
    pull_requests: 'write',
    workflows: 'write',
  });
  if (owner.trim()) params.set('target_name', owner.trim());
  return `https://github.com/settings/personal-access-tokens/new?${params}`;
}

function GenerateToken({ disabled = false }: { disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<TokenKind>('fine-grained');
  const [owner, setOwner] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();

  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current?.close();
  }, [open]);

  return <>
    <button type="button" disabled={disabled} onClick={() => setOpen(true)}
      className="btn-secondary h-9 px-4 gap-2 text-[10px] uppercase tracking-widest">
      <ExternalLink size={14} /> Generate token
    </button>
    {createPortal(<dialog ref={dialog} aria-labelledby={`${id}-title`}
      onCancel={() => setOpen(false)} onClose={() => setOpen(false)}
      onClick={(event) => { if (event.target === event.currentTarget) setOpen(false); }}
      className="git-token-dialog m-auto w-[calc(100%-2rem)] max-w-lg max-h-[90dvh] overflow-y-auto rounded-2xl border border-(--border-strong) bg-(--surface-0) p-0 text-(--text-primary) shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-xs">
      <div className="p-6 space-y-5">
        <div className="flex items-center justify-between gap-4">
          <h2 id={`${id}-title`} className="text-sm font-bold uppercase tracking-widest">Create a GitHub token</h2>
          <button type="button" aria-label="Close token options" onClick={() => setOpen(false)}
            className="p-1 text-(--text-secondary) hover:text-(--text-primary)"><X size={18} /></button>
        </div>
        <fieldset className="space-y-3">
          <legend className="text-xs text-(--text-secondary) mb-3">Which repositories should this token cover?</legend>
          {([
            { value: 'fine-grained', title: 'Selected repositories', subtitle: 'Fine-grained · Recommended', description: 'Choose repositories under one personal account or organization.' },
            { value: 'classic', title: 'Personal + organization repositories', subtitle: 'Classic', description: 'Access across your personal account and organizations that allow classic tokens.' },
          ] as const).map((option) => <label key={option.value}
            className={`flex items-start gap-3 p-4 rounded-xl border cursor-pointer transition-colors ${kind === option.value ? 'border-(--border-strong) bg-(--surface-2)' : 'border-(--border-subtle) hover:bg-(--surface-1)'}`}>
            <input type="radio" name={`${id}-kind`} value={option.value} checked={kind === option.value}
              onChange={() => setKind(option.value)} className="mt-1 shrink-0 accent-(--text-primary)" />
            <span className="space-y-1 block">
              <span className="block text-sm font-semibold">{option.title}</span>
              <span className="block text-xs text-(--text-secondary)">{option.subtitle}</span>
              <span className="block text-xs leading-relaxed text-(--text-secondary)">{option.description}</span>
            </span>
          </label>)}
        </fieldset>
        {kind === 'fine-grained' ? <label className="block space-y-2 text-xs text-(--text-secondary)">
          <span>Repository owner · optional</span>
          <input className="input-field h-10 text-sm w-full" placeholder="Your GitHub username or organization" value={owner}
            onChange={(event) => setOwner(event.target.value)} />
          <span className="block leading-relaxed">Leave blank for your personal account. Organization access may require approval.</span>
        </label> : <p className="text-xs leading-relaxed text-(--text-secondary)">Classic tokens cover all repositories you can access where permitted. Organizations using SSO may require you to authorize the token after creation.</p>}
        <p className="text-xs leading-relaxed text-(--text-secondary)">GitHub opens with the permissions prefilled. Review access and expiration, generate your token, then paste it back into Sentinel.</p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary h-9 px-4 text-[10px] uppercase tracking-widest" onClick={() => setOpen(false)}>Cancel</button>
          <a href={tokenCreationUrl(kind, owner)} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)}
            className="btn-primary h-9 px-4 gap-2 text-[10px] uppercase tracking-widest"><ExternalLink size={14} /> Continue to GitHub</a>
        </div>
      </div>
    </dialog>, document.body)}
  </>;
}

function detailsPayload(details: AccountDetails) {
  return Object.fromEntries(Object.entries(details).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()]));
}

export function GitPage({ embedded = false }: { embedded?: boolean } = {}) {
  const Shell = embedded ? SettingsIntegrationFrame : AppShell;
  const [accounts, setAccounts] = useState<GitAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [creating, setCreating] = useState(false);
  const [token, setToken] = useState('');
  const [details, setDetails] = useState<AccountDetails>(EMPTY_DETAILS);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDetails, setEditDetails] = useState<AccountDetails>(EMPTY_DETAILS);
  const [replacementToken, setReplacementToken] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const payload = await api.get<GitAccountListResponse>('/git/accounts');
      setAccounts(payload.items);
    } catch {
      setLoadError(true);
      notify.error('Failed to load GitHub accounts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadAll(); }, [loadAll]);

  async function createAccount() {
    if (!token.trim() || creating) return;
    setCreating(true);
    try {
      const account = await api.post<GitAccount>('/git/accounts', { ...detailsPayload(details), token: token.trim() });
      setAccounts((current) => [account, ...current]);
      setToken('');
      setDetails(EMPTY_DETAILS);
      notify.success(`Connected @${account.github_login}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Could not connect GitHub');
    } finally {
      setCreating(false);
    }
  }

  function closeEdit() {
    setEditingId(null);
    setReplacementToken('');
    setEditDetails(EMPTY_DETAILS);
  }

  async function saveAccount(accountId: string) {
    if (Object.values(editDetails).some((value) => !value.trim())) {
      notify.error('Account details cannot be empty');
      return;
    }
    setBusyId(accountId);
    try {
      const account = await api.patch<GitAccount>(`/git/accounts/${accountId}`, {
        ...detailsPayload(editDetails),
        ...(replacementToken.trim() ? { token: replacementToken.trim() } : {}),
      });
      setAccounts((current) => current.map((item) => item.id === account.id ? account : item));
      closeEdit();
      notify.success('Account updated');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Could not update account');
    } finally {
      setBusyId(null);
    }
  }

  async function checkAccount(accountId: string) {
    setBusyId(accountId);
    try {
      const account = await api.post<GitAccount>(`/git/accounts/${accountId}/check`, {});
      setAccounts((current) => current.map((item) => item.id === account.id ? account : item));
      notify.success(`Token verified for @${account.github_login}`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Could not verify token');
    } finally {
      setBusyId(null);
    }
  }

  async function deleteAccount(accountId: string) {
    if (!window.confirm('Remove this account from Sentinel? The token remains on GitHub until you revoke it there.')) return;
    setBusyId(accountId);
    try {
      await api.delete(`/git/accounts/${accountId}`);
      setAccounts((current) => current.filter((item) => item.id !== accountId));
      if (editingId === accountId) closeEdit();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Could not remove account');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Shell title="Git" contentClassName="git-page" actions={
      <div className="chat-header-actions flex items-center gap-2">
        <span className="chat-header-pill inline-flex items-center uppercase"><GitBranch size={14} /> {accounts.length} {accounts.length === 1 ? 'account' : 'accounts'}</span>
        <button className="chat-header-pill inline-flex items-center uppercase" disabled={loading} onClick={() => void loadAll()} aria-label="Refresh GitHub accounts">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>
    }>
      <div className="git-page-content w-full max-w-4xl mx-auto space-y-6">
        <Panel className="git-connect-panel p-6 space-y-6">
          <h2 className="text-xs font-bold uppercase tracking-widest flex items-center gap-2"><GitBranch size={16} /> Connect GitHub</h2>
          <div className="git-connect-steps grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-4">
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">1. Create a dedicated token</h3>
                <p className="text-xs leading-relaxed text-(--text-secondary)">Choose a token for selected repositories or access across your personal account and organizations. GitHub opens with the permissions prefilled.</p>
              </div>
              <GenerateToken disabled={creating} />
            </div>
            <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void createAccount(); }}>
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">2. Connect your account</h3>
                <p className="text-xs leading-relaxed text-(--text-secondary)">Paste the token here. Sentinel checks your GitHub identity and stores the token encrypted for this instance.</p>
              </div>
              <label className="block space-y-2 text-xs text-(--text-secondary)">
                <span>Personal access token</span>
                <input type="password" autoComplete="off" spellCheck={false} className="input-field h-10 text-sm w-full font-mono" placeholder="Paste your GitHub token" value={token}
                  onChange={(event) => setToken(event.target.value)} disabled={creating} />
              </label>
              <button type="submit" className="btn-primary h-9 px-4 gap-2 text-[10px] uppercase tracking-widest" disabled={creating || !token.trim()}>
                {creating ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
                {creating ? 'Checking token…' : 'Connect account'}
              </button>
            </form>
          </div>
          <details className="git-account-options text-xs text-(--text-secondary)">
            <summary className="cursor-pointer py-2">Account name, repository routing & commit identity</summary>
            <fieldset disabled={creating} className="mt-3 space-y-3">
              <DetailsFields value={details} onChange={setDetails} />
              <p className="text-(--text-muted)">Optional overrides. Routing chooses which saved account to use; it does not grant repository access.</p>
            </fieldset>
          </details>
        </Panel>
        <Panel className="git-accounts-panel p-6 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-xs font-bold uppercase tracking-widest">Connected accounts</h2>
            <StatusChip label={`${accounts.length} accounts`} />
          </div>
          {loading ? <p className="text-sm text-(--text-muted)">Loading accounts…</p>
            : loadError ? <button className="btn-secondary h-9 px-4 text-xs" onClick={() => void loadAll()}>Retry loading accounts</button>
            : accounts.length === 0 ? <p className="text-sm text-(--text-muted)">Connect your first GitHub account above.</p>
            : <div className="space-y-3">{accounts.map((account) => (
              <Panel key={account.id} className="git-account p-4 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate">{account.name}</p>
                    <p className="git-account-description text-xs text-(--text-secondary)">{account.github_login ? `@${account.github_login}` : account.host} · {account.scope_pattern}</p>
                  </div>
                  <StatusChip label={!account.has_token ? 'Replace token' : account.verified_at ? 'Identity checked' : 'Token stored'} tone={!account.has_token ? 'warn' : account.verified_at ? 'good' : 'default'} />
                </div>
                {editingId === account.id ? <fieldset disabled={busyId === account.id} className="space-y-4">
                  <DetailsFields value={editDetails} onChange={setEditDetails} />
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <label htmlFor="replacement-git-token" className="text-xs text-(--text-secondary)">Replace token · leave empty to keep the current token</label>
                      <GenerateToken disabled={busyId === account.id} />
                    </div>
                    <input id="replacement-git-token" type="password" autoComplete="off" spellCheck={false} className="input-field h-10 text-sm w-full font-mono" placeholder="Paste a new token for this account" value={replacementToken} onChange={(event) => setReplacementToken(event.target.value)} />
                  </div>
                  <div className="flex justify-end gap-2">
                    <button className="btn-secondary h-9 px-4 text-[10px] uppercase tracking-widest" onClick={closeEdit}>Cancel</button>
                    <button className="btn-primary h-9 px-4 text-[10px] uppercase tracking-widest" onClick={() => void saveAccount(account.id)}>{busyId === account.id ? 'Saving…' : 'Save'}</button>
                  </div>
                </fieldset> : <>
                  <p className="git-account-description text-xs text-(--text-secondary)">Commits as {account.author_name} &lt;{account.author_email}&gt;</p>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span className="text-[11px] text-(--text-muted)">{account.verified_at ? `Identity checked ${formatCompactDate(account.verified_at)}` : 'Identity not checked yet'} · Repository access depends on the token.</span>
                    <div className="flex flex-wrap gap-2">
                      <button className="btn-secondary h-8 px-3 gap-2 text-[10px] uppercase tracking-widest" disabled={busyId !== null || !account.has_token} onClick={() => void checkAccount(account.id)}><Check size={12} /> Check token</button>
                      <button className="btn-secondary h-8 px-3 gap-2 text-[10px] uppercase tracking-widest" disabled={busyId !== null} onClick={() => { setEditingId(account.id); setEditDetails({ name: account.name, scope_pattern: account.scope_pattern, author_name: account.author_name, author_email: account.author_email }); setReplacementToken(''); }}><KeyRound size={12} /> Edit</button>
                      <button className="btn-secondary h-8 px-3 gap-2 text-[10px] uppercase tracking-widest text-rose-500" disabled={busyId !== null} onClick={() => void deleteAccount(account.id)}><Trash2 size={12} /> Remove</button>
                    </div>
                  </div>
                </>}
              </Panel>
            ))}</div>}
        </Panel>
      </div>
    </Shell>
  );
}
