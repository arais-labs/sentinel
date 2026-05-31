import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  LogOut, ShieldAlert, User, Cpu, Hash, Info, ChevronRight,
  Bot, Eye, EyeOff, Check, Loader2, RefreshCw, HelpCircle, X, KeyRound, Pencil, Plus,
  Trash2, Server, Wifi, Container, Power, Square, RotateCcw, Copy, Unlink,
  Archive, Download, Upload, Lock,
} from 'lucide-react';
import { Link as RouterLink, useLocation } from 'react-router-dom';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { APP_VERSION } from '../lib/env';
import { api, requestBlob } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { instanceRoute, instanceRouteFromPath } from '../lib/routes';
import { useAuthStore } from '../store/auth-store';
import type {
  Runtime,
  RuntimeLifecycleResponse,
  RuntimeCapabilitiesResponse,
  RuntimeJob,
  RuntimeTestResponse,
} from '../types/api';

// ── types ───────────────────────────────────────────────────────────────────

interface ProviderStatus {
  configured: boolean;
  auth_method: 'oauth' | 'api_key' | null;
  masked_key: string | null;
}

interface ProvidersStatusResponse {
  primary_provider: string;
  providers: {
    anthropic: ProviderStatus;
    openai: ProviderStatus;
    gemini: ProviderStatus;
  };
}

interface DesktopCodexOauthStatus {
  enabled: boolean;
  auth_file_found: boolean;
}

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
  runtime_id: string | null;
}

interface BackupItemInfo {
  key: string;
  label: string;
}

interface BackupItemsResponse {
  items: BackupItemInfo[];
}

interface BackupInfoResponse {
  source_instance: string | null;
  created_at: string | null;
  created_by_version: string | null;
  items: string[];
  restorable: boolean;
  compatibility: string | null;
}

interface BackupImportResponse {
  imported: number;
  skipped: number;
  by_table: Record<string, Record<string, number>>;
}

const emptyRuntimeForm = {
  name: '',
  host: '',
  port: '22',
  username: '',
  workspaces_dir: '',
  auth_type: 'private_key' as 'private_key' | 'password',
  private_key: '',
  password: '',
};

const RUNTIME_VERIFICATION_FIELDS = new Set<keyof typeof emptyRuntimeForm>([
  'host', 'port', 'username', 'workspaces_dir', 'auth_type', 'private_key', 'password',
]);

const RUNTIME_INSTALL_HINTS: Record<string, string | null> = {
  Lima: 'brew install lima',
  Docker: 'brew install --cask docker',
  'Provisioning engine': 'brew install ansible',
  'Runtime provisioning profile': null,
  'Lima runtime profile': null,
};

const runtimeInputClass = 'h-10 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-xs font-medium text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] outline-none transition-colors focus:border-[color:var(--accent-solid)]';
const runtimeTextAreaClass = 'min-h-28 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 py-2 font-mono text-[10px] font-medium leading-relaxed text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] outline-none transition-colors focus:border-[color:var(--accent-solid)]';

function runtimeProviderLabel(runtime: Runtime): string {
  if (runtime.provider !== 'ssh') return runtime.provider;
  if (runtime.auth_type === 'private_key') return 'Key';
  if (runtime.auth_type === 'password') return 'Password';
  return 'SSH';
}

// ── OAuth help content ──────────────────────────────────────────────────────

const OAUTH_HELP: Record<string, { title: string; steps: string[]; command: string }> = {
  anthropic: {
    title: 'How to get an Anthropic OAuth token',
    steps: [
      'Install the Claude CLI: npm install -g @anthropic-ai/claude-code',
      'Run: claude setup-token',
      'A browser window opens — log in with your Anthropic account and authorize.',
      'The terminal prints your token (starts with sk-ant-oat01-). Copy it.',
    ],
    command: 'claude setup-token',
  },
  openai: {
    title: 'How to get an OpenAI Codex OAuth token',
    steps: [
      'Install or update the Codex CLI: npm i -g @openai/codex',
      'Run: codex login',
      'Click Sign in with ChatGPT and complete the browser flow.',
      'After authorization, credentials are stored locally in ~/.codex/auth.json.',
      'Copy the access_token value and paste it here.',
    ],
    command: 'codex login',
  },
  gemini: {
    title: 'How to get Gemini OAuth credentials',
    steps: [
      'Install and run the Gemini CLI: npm install -g @google/gemini-cli or run gemini directly if already installed.',
      'Start gemini and choose Sign in with Google.',
      'After authorization, open ~/.gemini/oauth_creds.json.',
      'Copy the full JSON file contents and paste them here. Sentinel uses the same refreshable Code Assist credentials, so the JSON must include refresh_token.',
    ],
    command: 'gemini',
  },
};

// ── provider editor ─────────────────────────────────────────────────────────

function ProviderRow({
  name, status, onSave, saving, providerId, isPrimary, onSetPrimary, onRemove,
  canImportOauth = false, importingOauth = false, onImportOauth,
}: {
  name: string;
  status: ProviderStatus | null;
  onSave: (data: { apiKey?: string; oauthToken?: string }) => void;
  saving: boolean;
  providerId: 'anthropic' | 'openai' | 'gemini';
  isPrimary: boolean;
  onSetPrimary: () => void;
  onRemove: () => void;
  canImportOauth?: boolean;
  importingOauth?: boolean;
  onImportOauth?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const help = OAUTH_HELP[providerId];
  const [mode, setMode] = useState<'oauth' | 'api'>('api');
  const [value, setValue] = useState('');
  const [showValue, setShowValue] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const configured = status?.configured ?? false;
  const isGeminiOauth = providerId === 'gemini' && mode === 'oauth';
  const oauthLabel = providerId === 'gemini' ? 'OAuth Credentials' : 'OAuth Token';

  function handleSave() {
    if (!value.trim()) return;
    onSave(mode === 'oauth' ? { oauthToken: value.trim() } : { apiKey: value.trim() });
    setValue('');
    setEditing(false);
  }

  return (
    <div className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] overflow-hidden relative">
      <div className="px-4 py-3 space-y-2">
        {/* Row 1: name + status badge */}
        <div className="flex items-center gap-2">
          <div className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: configured ? '#10B981' : '#F59E0B' }} />
          <span className="text-xs font-bold uppercase tracking-widest">{name}</span>
          <div className="flex-1" />
          {configured && isPrimary && (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Primary</span>
          )}
          {configured && !isPrimary && (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">Fallback</span>
          )}
          {configured && (
            <StatusChip label={status?.auth_method === 'oauth' ? 'OAuth' : 'API Key'} tone="info" className="scale-90" />
          )}
          {!configured && (
            <StatusChip label="Not configured" tone="warn" className="scale-90" />
          )}
        </div>

        {/* Row 2: masked key */}
        {configured && status?.masked_key && (
          <div className="text-[10px] font-mono text-[color:var(--text-muted)] truncate">{status.masked_key}</div>
        )}

        {/* Row 3: action buttons */}
        <div className="flex items-center gap-3 pt-1">
          {configured && !isPrimary && (
            <button onClick={onSetPrimary}
              className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--accent-solid)] transition-colors">
              Set primary
            </button>
          )}
          <button onClick={() => {
              if (!editing) setMode(status?.auth_method === 'oauth' ? 'oauth' : 'api');
              setEditing(v => !v);
              setConfirmRemove(false);
            }}
            className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] hover:opacity-70 transition-opacity">
            {editing ? 'Cancel' : configured ? 'Update' : 'Configure'}
          </button>
          {configured && (
            !confirmRemove ? (
              <button onClick={() => setConfirmRemove(true)}
                className="text-[10px] font-bold uppercase tracking-widest text-rose-500/60 hover:text-rose-500 transition-colors">
                Remove
              </button>
            ) : (
              <div className="flex items-center gap-1">
                <button onClick={() => { onRemove(); setConfirmRemove(false); }}
                  className="text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:opacity-70 transition-opacity">
                  Confirm
                </button>
                <button onClick={() => setConfirmRemove(false)}
                  className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
                  No
                </button>
              </div>
            )
          )}
        </div>
      </div>

      {editing && (
        <div className="px-4 pb-4 pt-1 border-t border-[color:var(--border-subtle)] space-y-3 animate-in fade-in duration-200">
          {help ? (
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg bg-[color:var(--surface-2)] p-0.5 w-fit">
                {([
                  { id: 'oauth', label: oauthLabel },
                  { id: 'api',   label: 'API Key' },
                ] as const).map(m => (
                  <button key={m.id} onClick={() => { setMode(m.id); setShowHelp(false); }}
                    className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${mode === m.id ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'}`}>
                    {m.label}
                  </button>
                ))}
              </div>
              {mode === 'oauth' && (
                <button onClick={() => setShowHelp(v => !v)}
                  className={`p-1 rounded-md transition-colors ${showHelp ? 'text-[color:var(--accent-solid)]' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'}`}
                  title="How to get an OAuth token">
                  <HelpCircle size={14} />
                </button>
              )}
            </div>
          ) : null}

          {/* OAuth help popup */}
          {showHelp && mode === 'oauth' && help && (
            <div className="rounded-lg border border-[color:var(--border)] bg-[color:var(--surface-1)] p-3 space-y-2 animate-in fade-in duration-200">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)]">{help.title}</span>
                <button onClick={() => setShowHelp(false)} className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                  <X size={12} />
                </button>
              </div>
              <ol className="space-y-1.5 list-decimal list-inside">
                {help.steps.map((step, i) => (
                  <li key={i} className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                    {step}
                  </li>
                ))}
              </ol>
              <div className="flex items-center gap-2 rounded-md bg-[color:var(--app-bg)] px-2 py-1.5 font-mono text-[11px] text-[color:var(--text-primary)] border border-[color:var(--border)]">
                <span className="flex-1">{help.command}</span>
                <button onClick={() => { navigator.clipboard.writeText(help.command); toast.success('Copied'); }}
                  className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] hover:opacity-70 transition-opacity shrink-0">
                  Copy
                </button>
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <div className="relative flex-1">
              {isGeminiOauth ? (
                <textarea
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  placeholder='Paste Gemini OAuth credentials JSON...'
                  className="input-field min-h-[128px] py-3 font-mono text-xs w-full resize-y"
                />
              ) : (
                <>
                  <input type={showValue ? 'text' : 'password'} value={value} onChange={e => setValue(e.target.value)}
                    placeholder={mode === 'oauth' ? 'Paste OAuth token...' : 'Paste API key...'}
                    className="input-field h-10 pr-10 font-mono text-xs w-full"
                    onKeyDown={e => e.key === 'Enter' && handleSave()}
                  />
                  <button type="button" onClick={() => setShowValue(v => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                    {showValue ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </>
              )}
            </div>
            <button onClick={handleSave} disabled={!value.trim() || saving}
              className="btn-primary h-10 px-4 text-[10px] font-bold uppercase tracking-widest shrink-0">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Save
            </button>
          </div>
          {canImportOauth && mode === 'oauth' && onImportOauth && (
            <button
              type="button"
              onClick={onImportOauth}
              disabled={importingOauth}
              className="btn-secondary h-10 w-full justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              {importingOauth ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
              Import from Codex CLI
            </button>
          )}
          {isGeminiOauth && (
            <p className="text-[10px] text-[color:var(--text-muted)]">
              Paste the full contents of <span className="font-mono text-[color:var(--text-primary)]">~/.gemini/oauth_creds.json</span>. Sentinel mirrors Gemini CLI and stores the refreshable Code Assist credential bundle, not a short-lived token.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── main page ───────────────────────────────────────────────────────────────

export function SettingsPage() {
  const location = useLocation();
  const userId = useAuthStore((s) => s.userId);
  const role = useAuthStore((s) => s.role);
  const logout = useAuthStore((s) => s.logout);

  const isAdmin = role === 'admin';

  const [providerStatus, setProviderStatus] = useState<ProvidersStatusResponse | null>(null);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [codexOauthImportAvailable, setCodexOauthImportAvailable] = useState(false);
  const [importingCodexOauth, setImportingCodexOauth] = useState(false);
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [currentInstance, setCurrentInstance] = useState<SentinelInstance | null>(null);
  const [loadingRuntimes, setLoadingRuntimes] = useState(true);
  const [savingRuntime, setSavingRuntime] = useState(false);
  const [testingRuntime, setTestingRuntime] = useState(false);
  const [runtimeForm, setRuntimeForm] = useState(emptyRuntimeForm);
  const [editingRuntimeId, setEditingRuntimeId] = useState<string | null>(null);
  const [addingNewTarget, setAddingNewTarget] = useState(false);
  const [confirmDeleteTargetId, setConfirmDeleteTargetId] = useState<string | null>(null);
  const [deletingRuntime, setDeletingRuntime] = useState(false);
  const [runtimeVerified, setRuntimeVerified] = useState(false);
  const [runtimeResolvedHome, setRuntimeResolvedHome] = useState<string | null>(null);
  const [runtimeCapabilities, setRuntimeCapabilities] = useState<RuntimeCapabilitiesResponse | null>(null);
  const [creatingProvider, setCreatingProvider] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<'lima' | 'docker' | 'ssh' | null>(null);
  const [creatingJobMessage, setCreatingJobMessage] = useState<string | null>(null);

  // ── backup & restore ──
  const [backupItems, setBackupItems] = useState<BackupItemInfo[]>([]);
  const [backupSelection, setBackupSelection] = useState<Set<string>>(new Set());
  const [backupPassphrase, setBackupPassphrase] = useState('');
  const [creatingBackup, setCreatingBackup] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreData, setRestoreData] = useState<string | null>(null);
  const [restorePassphrase, setRestorePassphrase] = useState('');
  const [restoreInfo, setRestoreInfo] = useState<BackupInfoResponse | null>(null);
  const [restoreSelection, setRestoreSelection] = useState<Set<string>>(new Set());
  const [inspectingBackup, setInspectingBackup] = useState(false);
  const [restoringBackup, setRestoringBackup] = useState(false);

  function updateRuntimeForm(updates: Partial<typeof emptyRuntimeForm>) {
    setRuntimeForm((f) => ({ ...f, ...updates }));
    const touchesVerification = (Object.keys(updates) as Array<keyof typeof emptyRuntimeForm>)
      .some((k) => RUNTIME_VERIFICATION_FIELDS.has(k));
    if (touchesVerification) {
      setRuntimeVerified(false);
      setRuntimeResolvedHome(null);
    }
  }

  // Context-first so the page works inside a tiling pane (not the active route).
  const instanceName = useInstanceName() ?? null;

  const fetchStatus = useCallback(async () => {
    try {
      const providers = await api.get<ProvidersStatusResponse>('/settings/api-keys/status');
      setProviderStatus(providers);
    } catch {
      // silent — panel will show loading state
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const fetchRuntimes = useCallback(async () => {
    if (!instanceName) return;
    setLoadingRuntimes(true);
    try {
      const [capabilities, rows, instance] = await Promise.all([
        api.get<RuntimeCapabilitiesResponse>('/runtimes/capabilities'),
        api.get<Runtime[]>('/runtimes'),
        api.get<SentinelInstance>(`/instances/${encodeURIComponent(instanceName)}`),
      ]);
      setRuntimeCapabilities(capabilities);
      setRuntimes(rows);
      setCurrentInstance(instance);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load runtimes');
    } finally {
      setLoadingRuntimes(false);
    }
  }, [instanceName]);

  useEffect(() => { void fetchRuntimes(); }, [fetchRuntimes]);

  useEffect(() => {
    let cancelled = false;
    api.get<DesktopCodexOauthStatus>('/settings/desktop-codex-oauth/status')
      .then((status) => {
        if (!cancelled) setCodexOauthImportAvailable(status.enabled);
      })
      .catch(() => {
        if (!cancelled) setCodexOauthImportAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.get<BackupItemsResponse>('/backup/items')
      .then((res) => {
        if (cancelled) return;
        setBackupItems(res.items);
        setBackupSelection(new Set(res.items.map((i) => i.key)));
      })
      .catch(() => {
        if (!cancelled) setBackupItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const backupItemLabel = useCallback(
    (key: string) => backupItems.find((i) => i.key === key)?.label ?? key,
    [backupItems],
  );

  function toggleBackupItem(key: string) {
    setBackupSelection((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleRestoreItem(key: string) {
    setRestoreSelection((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function resetRestore() {
    setRestoreFile(null);
    setRestoreData(null);
    setRestoreInfo(null);
    setRestoreSelection(new Set());
    setRestorePassphrase('');
  }

  async function handleRestoreFileSelected(file: File | null) {
    setRestoreInfo(null);
    setRestoreSelection(new Set());
    setRestoreFile(file);
    if (!file) {
      setRestoreData(null);
      return;
    }
    try {
      const buffer = await file.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      let binary = '';
      for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
      setRestoreData(btoa(binary));
    } catch {
      toast.error('Could not read backup file');
      setRestoreData(null);
    }
  }

  async function handleCreateBackup() {
    if (backupSelection.size === 0) {
      toast.error('Select at least one item to back up');
      return;
    }
    if (!backupPassphrase) {
      toast.error('Enter a passphrase to encrypt the backup');
      return;
    }
    setCreatingBackup(true);
    try {
      const { blob, filename } = await requestBlob('/backup/export', {
        method: 'POST',
        body: { items: Array.from(backupSelection), passphrase: backupPassphrase },
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename ?? 'sentinel-backup.sntl';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      toast.success('Backup created');
      setBackupPassphrase('');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create backup');
    } finally {
      setCreatingBackup(false);
    }
  }

  async function handleInspectBackup() {
    if (!restoreData) {
      toast.error('Select a backup file');
      return;
    }
    if (!restorePassphrase) {
      toast.error('Enter the backup passphrase');
      return;
    }
    setInspectingBackup(true);
    try {
      const info = await api.post<BackupInfoResponse>('/backup/inspect', {
        data: restoreData,
        passphrase: restorePassphrase,
      });
      setRestoreInfo(info);
      setRestoreSelection(new Set(info.items));
    } catch (error) {
      setRestoreInfo(null);
      setRestoreSelection(new Set());
      toast.error(error instanceof Error ? error.message : 'Failed to read backup');
    } finally {
      setInspectingBackup(false);
    }
  }

  async function handleRestoreBackup() {
    if (!restoreData || !restoreInfo) return;
    if (restoreSelection.size === 0) {
      toast.error('Select at least one item to restore');
      return;
    }
    setRestoringBackup(true);
    try {
      const result = await api.post<BackupImportResponse>('/backup/import', {
        data: restoreData,
        passphrase: restorePassphrase,
        items: Array.from(restoreSelection),
      });
      toast.success(`Restored ${result.imported} record(s), skipped ${result.skipped}`);
      resetRestore();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to restore backup');
    } finally {
      setRestoringBackup(false);
    }
  }

  async function handleSaveProvider(provider: 'anthropic' | 'openai' | 'gemini', data: { apiKey?: string; oauthToken?: string }) {
    setSavingProvider(provider);
    try {
      const body: Record<string, string | undefined> = {};
      if (provider === 'anthropic') {
        body.anthropic_api_key = data.apiKey;
        body.anthropic_oauth_token = data.oauthToken;
      } else if (provider === 'openai') {
        body.openai_api_key = data.apiKey;
        body.openai_oauth_token = data.oauthToken;
      } else {
        body.gemini_api_key = data.apiKey;
        body.gemini_oauth_credentials = data.oauthToken;
      }
      await api.post('/settings/api-keys', body);
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini' };
      toast.success(`${labels[provider]} provider updated`);
      await fetchStatus();
    } catch {
      toast.error('Failed to update provider');
    } finally {
      setSavingProvider(null);
    }
  }

  async function handleRemoveProvider(provider: 'anthropic' | 'openai' | 'gemini') {
    try {
      await api.delete('/settings/api-keys', { provider });
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini' };
      toast.success(`${labels[provider]} provider removed`);
      await fetchStatus();
    } catch {
      toast.error('Failed to remove provider');
    }
  }

  async function handleSetPrimary(provider: 'anthropic' | 'openai' | 'gemini') {
    try {
      await api.post('/settings/primary-provider', { provider });
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini' };
      toast.success(`${labels[provider]} set as primary`);
      await fetchStatus();
    } catch {
      toast.error('Failed to set primary provider');
    }
  }

  async function handleImportCodexOauth() {
    setImportingCodexOauth(true);
    try {
      await api.post('/settings/desktop-codex-oauth/import');
      toast.success('Codex OAuth token imported');
      await fetchStatus();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to import Codex OAuth token');
    } finally {
      setImportingCodexOauth(false);
    }
  }

  function startEditingRuntime(target: Runtime) {
    setAddingNewTarget(false);
    setSelectedProvider('ssh');
    setConfirmDeleteTargetId(null);
    setEditingRuntimeId(target.id);
    setRuntimeForm({
      name: target.name,
      host: target.host ?? '',
      port: String(target.port ?? 22),
      username: target.username ?? '',
      workspaces_dir: target.workspaces_dir ?? '',
      auth_type: target.auth_type ?? 'private_key',
      private_key: '',
      password: '',
    });
    setRuntimeVerified(true);
    setRuntimeResolvedHome(null);
  }

  function startAddingRuntime() {
    setEditingRuntimeId(null);
    setConfirmDeleteTargetId(null);
    setRuntimeForm(emptyRuntimeForm);
    setAddingNewTarget(true);
    setSelectedProvider(null);
    setRuntimeVerified(false);
    setRuntimeResolvedHome(null);
  }

  function autoSuggestName(provider: 'lima' | 'docker' | 'ssh'): string {
    const prefix = provider;
    const taken = new Set(runtimes.map((r) => r.name));
    for (let n = 1; n < 999; n++) {
      const candidate = `${prefix}-${n}`;
      if (!taken.has(candidate)) return candidate;
    }
    return `${prefix}-new`;
  }

  function pickProvider(provider: 'lima' | 'docker' | 'ssh') {
    setSelectedProvider(provider);
    setRuntimeForm((f) => ({ ...f, name: f.name || autoSuggestName(provider) }));
    if (provider !== 'ssh') {
      setRuntimeVerified(false);
      setRuntimeResolvedHome(null);
    }
  }

  function resetRuntimeForm() {
    setEditingRuntimeId(null);
    setAddingNewTarget(false);
    setSelectedProvider(null);
    setRuntimeForm(emptyRuntimeForm);
    setRuntimeVerified(false);
    setRuntimeResolvedHome(null);
    setCreatingJobMessage(null);
  }

  async function handleDeleteRuntime(targetId: string) {
    setDeletingRuntime(true);
    try {
      await api.delete(`/runtimes/${targetId}`);
      toast.success('Runtime removed');
      setConfirmDeleteTargetId(null);
      if (editingRuntimeId === targetId) resetRuntimeForm();
      await fetchRuntimes();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to remove runtime');
    } finally {
      setDeletingRuntime(false);
    }
  }

  async function handleSaveRuntime(testOnly = false) {
    const isEditing = editingRuntimeId !== null;
    const secretValue = runtimeForm.auth_type === 'private_key'
      ? runtimeForm.private_key.trim()
      : runtimeForm.password;
    const body = {
      name: runtimeForm.name.trim(),
      provider: 'ssh',
      host: runtimeForm.host.trim(),
      port: Number(runtimeForm.port || 22),
      username: runtimeForm.username.trim(),
      workspaces_dir: runtimeForm.workspaces_dir.trim(),
      ...(isEditing && !secretValue ? {} : { auth_type: runtimeForm.auth_type }),
      private_key: runtimeForm.auth_type === 'private_key' && secretValue ? runtimeForm.private_key : undefined,
      password: runtimeForm.auth_type === 'password' && secretValue ? runtimeForm.password : undefined,
    };
    if (testOnly) {
      if (!body.host || !body.username) {
        toast.error('Host and username are required to test');
        return;
      }
      if (!isEditing && !secretValue) {
        toast.error(runtimeForm.auth_type === 'private_key' ? 'Private key is required' : 'SSH password is required');
        return;
      }
      if (!secretValue) {
        toast.error('Enter the SSH secret to test this runtime');
        return;
      }
      setTestingRuntime(true);
      try {
        const { provider: _provider, ...testBody } = body;
        const result = await api.post<RuntimeTestResponse>(
          '/runtimes/test',
          { ...testBody, auth_type: runtimeForm.auth_type },
          { timeoutMs: 20_000 },
        );
        if (result.ok) {
          if (result.resolved_workspaces_dir) {
            setRuntimeForm((f) => ({ ...f, workspaces_dir: result.resolved_workspaces_dir as string }));
          }
          setRuntimeVerified(true);
          setRuntimeResolvedHome(result.resolved_home ?? null);
          toast.success(result.detail);
        } else {
          setRuntimeVerified(false);
          setRuntimeResolvedHome(null);
          toast.error(result.detail);
        }
      } catch (error) {
        setRuntimeVerified(false);
        setRuntimeResolvedHome(null);
        toast.error(error instanceof Error ? error.message : 'Runtime test failed');
      } finally {
        setTestingRuntime(false);
      }
      return;
    }
    if (!body.name || !body.host || !body.username || !body.workspaces_dir) {
      toast.error('Runtime name, host, username, and workspace root are required');
      return;
    }
    if (!isEditing && !secretValue) {
      toast.error(runtimeForm.auth_type === 'private_key' ? 'Private key is required' : 'SSH password is required');
      return;
    }
    setSavingRuntime(true);
    try {
      const target = isEditing
        ? await api.patch<Runtime>(`/runtimes/${editingRuntimeId}`, body)
        : await api.post<Runtime>('/runtimes', body);
      resetRuntimeForm();
      await fetchRuntimes();
      if (!isEditing && instanceName) {
        await assignRuntime(target.id);
      }
      toast.success(isEditing ? 'Runtime updated' : 'Runtime saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save runtime');
    } finally {
      setSavingRuntime(false);
    }
  }

  async function assignRuntime(targetId: string | null) {
    if (!instanceName) return;
    try {
      const instance = await api.patch<SentinelInstance>(
        `/instances/${encodeURIComponent(instanceName)}/runtime`,
        { runtime_id: targetId },
      );
      setCurrentInstance(instance);
      toast.success(targetId ? 'Runtime selected' : 'Runtime cleared');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to assign runtime');
    }
  }

  async function createRuntime(provider: 'lima' | 'docker') {
    const name = runtimeForm.name.trim();
    if (!name) {
      toast.error('Runtime name is required');
      return;
    }
    setCreatingProvider(provider);
    setCreatingJobMessage('Submitting…');
    try {
      const response = await api.post<RuntimeLifecycleResponse>(
        '/runtimes',
        {
          provider,
          name,
          profile: provider === 'lima' ? 'sentinel-linux-xfce' : 'sentinel-docker-linux',
          provider_config: { desktop: 'xfce' },
        },
        { timeoutMs: 30_000 },
      );
      await fetchRuntimes();
      // Poll the job until terminal status, showing the latest event message.
      let jobId: string | null = response.job.id;
      let lastStatus: 'queued' | 'running' | 'succeeded' | 'failed' = response.job.status;
      while (jobId && (lastStatus === 'queued' || lastStatus === 'running')) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const job = await api.get<Pick<RuntimeJob, 'id' | 'status' | 'events' | 'error'>>(`/runtimes/jobs/${jobId}`);
          lastStatus = job.status;
          const latest = job.events.length > 0 ? job.events[job.events.length - 1].message : null;
          setCreatingJobMessage(latest ?? (lastStatus === 'queued' ? 'Queued…' : 'Working…'));
          if (lastStatus === 'failed') {
            toast.error(job.error ?? 'Runtime creation failed');
          } else if (lastStatus === 'succeeded') {
            toast.success(`Runtime ${name} ready`);
          }
        } catch {
          // Polling glitch — keep trying for the next interval, but don't spam toasts.
        }
      }
      await fetchRuntimes();
      if (lastStatus === 'succeeded') {
        resetRuntimeForm();
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create runtime');
    } finally {
      setCreatingProvider(null);
      setCreatingJobMessage(null);
    }
  }

  async function runRuntimeAction(runtime: Runtime, action: 'start' | 'stop' | 'rebuild' | 'delete') {
    try {
      const response = await api.post<RuntimeLifecycleResponse>(
        `/runtimes/${runtime.id}/${action}`,
        undefined,
        { timeoutMs: 30_000 },
      );
      toast.success(`Runtime job started: ${response.job.action}`);
      await fetchRuntimes();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `Failed to ${action} runtime`);
    }
  }

  const primaryProvider = providerStatus?.primary_provider ?? 'anthropic';
  const activeRuntimeId = currentInstance?.runtime_id ?? null;

  function renderRuntimeForm() {
    const isEditing = editingRuntimeId !== null;
    const suggestedWorkspaceRoot = runtimeResolvedHome
      ? `${runtimeResolvedHome.replace(/\/+$/, '')}/sentinel/workspaces`
      : null;
    const canShowSuggestion = !!(
      suggestedWorkspaceRoot && runtimeForm.workspaces_dir.trim() !== suggestedWorkspaceRoot
    );
    return (
      <div className="rounded-xl border border-[color:var(--accent-solid)]/40 bg-[color:var(--surface-0)] p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">
            {isEditing ? 'Edit runtime' : 'New runtime'}
          </span>
          {runtimeVerified ? (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
              <Check size={10} /> Verified
            </span>
          ) : (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">
              Not verified
            </span>
          )}
          <div className="flex-1" />
          <button
            type="button"
            onClick={resetRuntimeForm}
            className="p-1 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors"
            title="Cancel"
          >
            <X size={14} />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Name</span>
            <input
              value={runtimeForm.name}
              onChange={(e) => updateRuntimeForm({ name: e.target.value })}
              className={`${runtimeInputClass} w-full`}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Username</span>
            <input
              value={runtimeForm.username}
              onChange={(e) => updateRuntimeForm({ username: e.target.value })}
              className={`${runtimeInputClass} w-full`}
            />
          </label>
          <label className="space-y-1 sm:col-span-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Host</span>
            <input
              value={runtimeForm.host}
              onChange={(e) => updateRuntimeForm({ host: e.target.value })}
              placeholder="hostname or IP"
              className={`${runtimeInputClass} w-full font-mono`}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Port</span>
            <input
              value={runtimeForm.port}
              onChange={(e) => updateRuntimeForm({ port: e.target.value })}
              className={`${runtimeInputClass} w-full font-mono`}
            />
          </label>
        </div>

        <label className="block space-y-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Workspace root</span>
          <input
            value={runtimeForm.workspaces_dir}
            onChange={(e) => updateRuntimeForm({ workspaces_dir: e.target.value })}
            className={`${runtimeInputClass} w-full font-mono`}
          />
          {canShowSuggestion && (
            <button
              type="button"
              onClick={() => updateRuntimeForm({ workspaces_dir: suggestedWorkspaceRoot! })}
              className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] hover:opacity-70 transition-opacity"
            >
              <Check size={11} />
              Use <span className="font-mono normal-case tracking-normal">{suggestedWorkspaceRoot}</span>
            </button>
          )}
          <p className="text-[10px] text-[color:var(--text-muted)]">
            Leave blank to use <span className="font-mono">$HOME/sentinel/workspaces</span>.
          </p>
        </label>

        <div className="space-y-2">
          <span className="block text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Authentication</span>
          <div className="flex rounded-lg bg-[color:var(--surface-2)] p-0.5 w-fit">
            {(['private_key', 'password'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => updateRuntimeForm({ auth_type: mode })}
                className={`px-3 py-1.5 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${
                  runtimeForm.auth_type === mode
                    ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                    : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'
                }`}
              >
                {mode === 'private_key' ? 'Private key' : 'Password'}
              </button>
            ))}
          </div>
          {runtimeForm.auth_type === 'private_key' ? (
            <textarea
              value={runtimeForm.private_key}
              onChange={(e) => updateRuntimeForm({ private_key: e.target.value })}
              placeholder={isEditing ? '' : '-----BEGIN OPENSSH PRIVATE KEY-----'}
              className={`${runtimeTextAreaClass} w-full`}
            />
          ) : (
            <input
              type="password"
              value={runtimeForm.password}
              onChange={(e) => updateRuntimeForm({ password: e.target.value })}
              className={`${runtimeInputClass} w-full`}
            />
          )}
          {isEditing && (
            <p className="text-[10px] leading-relaxed text-[color:var(--text-muted)]">
              Leave the SSH secret empty to keep the existing credential.
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
          <button
            type="button"
            onClick={resetRuntimeForm}
            className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest"
          >
            Cancel
          </button>
          <div className="flex-1" />
          {!runtimeVerified && (
            <span className="text-[10px] text-amber-400 font-medium">
              Test connection to enable save
            </span>
          )}
          <button
            type="button"
            onClick={() => void handleSaveRuntime(true)}
            disabled={testingRuntime || savingRuntime}
            className="btn-secondary h-10 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest"
          >
            {testingRuntime ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
            Test connection
          </button>
          <button
            type="button"
            onClick={() => void handleSaveRuntime(false)}
            disabled={savingRuntime || testingRuntime || !runtimeVerified}
            title={!runtimeVerified ? 'Run Test connection successfully before saving' : undefined}
            className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {savingRuntime ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {isEditing ? 'Save changes' : 'Save runtime'}
          </button>
        </div>
      </div>
    );
  }

  function renderTargetCard(target: Runtime) {
    const isActive = activeRuntimeId === target.id;
    const isConfirmingDelete = confirmDeleteTargetId === target.id;

    return (
      <div
        key={target.id}
        className={`group relative rounded-xl border transition-all ${
          isActive
            ? 'border-[color:var(--accent-solid)] bg-[color:var(--surface-1)] ring-1 ring-[color:var(--accent-solid)]/30'
            : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-1)]'
        }`}
      >
        <div className="flex items-stretch">
          <button
            type="button"
            onClick={() => { if (!isActive) void assignRuntime(target.id); }}
            disabled={isActive}
            className={`flex-1 min-w-0 text-left px-4 py-3 flex items-start gap-3 ${isActive ? 'cursor-default' : 'cursor-pointer'}`}
            title={isActive ? 'This target is currently active for this instance' : 'Click to set as active runtime'}
          >
            <div className={`mt-1 h-2.5 w-2.5 rounded-full shrink-0 ${isActive ? 'bg-emerald-500' : 'bg-[color:var(--text-muted)]/40 group-hover:bg-[color:var(--text-muted)]'}`} />
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-bold truncate">{target.name}</span>
                {isActive && (
                  <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
                    Active
                  </span>
                )}
                <StatusChip
                  label={runtimeProviderLabel(target)}
                  tone="info"
                  className="scale-90"
                />
                <StatusChip
                  label={target.status}
                  tone={target.status === 'ready' || target.status === 'running' ? 'good' : target.status === 'error' ? 'danger' : 'default'}
                  className="scale-90"
                />
              </div>
              <div className="font-mono text-[10px] text-[color:var(--text-muted)] truncate">
                {target.username && target.host ? `${target.username}@${target.host}:${target.port ?? 22}` : 'SSH details pending'}
              </div>
              <div className="font-mono text-[10px] text-[color:var(--text-muted)] truncate">
                {target.workspaces_dir ?? 'Workspace root pending'}
              </div>
              {target.status_detail && (
                <div className="mt-2 flex items-start gap-2 rounded-md border border-rose-500/20 bg-rose-500/10 px-2.5 py-2 text-[10px] leading-relaxed text-rose-300">
                  <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                  <span className="min-w-0">{target.status_detail}</span>
                </div>
              )}
            </div>
          </button>
          <div className="flex items-center gap-1 pr-3 shrink-0">
            {isConfirmingDelete ? (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => void handleDeleteRuntime(target.id)}
                  disabled={deletingRuntime}
                  className="text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:opacity-70 transition-opacity px-2 py-1"
                >
                  {deletingRuntime ? <Loader2 size={12} className="animate-spin" /> : 'Confirm'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDeleteTargetId(null)}
                  className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors px-2 py-1"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <>
                {isActive && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); void assignRuntime(null); }}
                    className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-rose-500 hover:bg-rose-500/10 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                    title="Detach from this instance"
                  >
                    <Unlink size={13} />
                  </button>
                )}
                {target.provider === 'ssh' && (
                  <button
                    type="button"
                    onClick={() => startEditingRuntime(target)}
                    className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                    title="Edit runtime"
                  >
                    <Pencil size={13} />
                  </button>
                )}
                {target.provider !== 'ssh' && (
                  <>
                    <button type="button" onClick={() => void runRuntimeAction(target, 'start')} className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-emerald-400 hover:bg-[color:var(--surface-2)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Start runtime">
                      <Power size={13} />
                    </button>
                    <button type="button" onClick={() => void runRuntimeAction(target, 'stop')} className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Stop runtime">
                      <Square size={13} />
                    </button>
                    <button type="button" onClick={() => void runRuntimeAction(target, 'rebuild')} className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Rebuild runtime">
                      <RotateCcw size={13} />
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setConfirmDeleteTargetId(target.id)}
                  className="p-1.5 rounded-md text-[color:var(--text-muted)] hover:text-rose-500 hover:bg-rose-500/10 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                  title="Delete runtime"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  const isFormOpen = editingRuntimeId !== null || addingNewTarget;

  return (
    <AppShell
      title="Operator Settings"
      subtitle="Identity Management & Console Controls"
    >
      <div className="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
        {/* Identity Panel */}
        <Panel className="p-6 space-y-6">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <User size={20} />
            </div>
            <div>
              <h2 className="text-sm font-bold uppercase tracking-widest">Operator Identity</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Verified Credentials</p>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between group">
              <div className="flex items-center gap-3">
                <Hash size={14} className="text-[color:var(--text-muted)]" />
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">User ID</span>
              </div>
              <span className="text-xs font-mono font-bold">{userId ?? 'N/A'}</span>
            </div>

            <div className="flex items-center justify-between group">
              <div className="flex items-center gap-3">
                <ShieldAlert size={14} className="text-[color:var(--text-muted)]" />
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Assigned Role</span>
              </div>
              <StatusChip label={role ?? 'unknown'} tone={isAdmin ? 'warn' : 'info'} className="scale-90" />
            </div>

            <div className="flex items-center justify-between group border-t border-[color:var(--border-subtle)] pt-4">
              <div className="flex items-center gap-3">
                <Cpu size={14} className="text-[color:var(--text-muted)]" />
                <span className="text-[11px] font-bold text-[color:var(--text-muted)] uppercase tracking-wider">Console Version</span>
              </div>
              <span className="text-xs font-mono font-bold opacity-60">v{APP_VERSION}</span>
            </div>
          </div>

          <div className="bg-[color:var(--surface-1)] p-4 rounded-xl border border-[color:var(--border-subtle)] flex items-start gap-3">
            <Info size={16} className="text-[color:var(--accent-solid)] shrink-0 mt-0.5" />
            <p className="text-[11px] text-[color:var(--text-secondary)] leading-relaxed font-medium uppercase tracking-tight">
              Identity and role come from your active Sentinel session.
            </p>
          </div>
        </Panel>

        {/* Access Panel */}
        <Panel className="p-6 flex flex-col justify-between space-y-6">
          <div className="space-y-6">
            <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
              <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--text-primary)]">
                <ShieldAlert size={20} />
              </div>
              <div>
                <h2 className="text-sm font-bold uppercase tracking-widest">System Access</h2>
                <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Critical Operations</p>
              </div>
            </div>

            <div className="space-y-3">
              {isAdmin ? (
                <RouterLink
                  to={instanceName ? instanceRoute(instanceName, 'settings/admin') : instanceRouteFromPath(location.pathname, 'settings/admin')}
                  className="flex items-center justify-between p-4 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)] transition-all group"
                >
                  <div className="flex items-center gap-3">
                    <ShieldAlert size={18} className="text-amber-500" />
                    <div>
                      <span className="text-xs font-bold block">Admin Console</span>
                      <span className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tight">Access restricted system overrides</span>
                    </div>
                  </div>
                  <ChevronRight size={16} className="text-[color:var(--text-muted)] group-hover:text-[color:var(--text-primary)] transition-colors" />
                </RouterLink>
              ) : (
                <div className="p-4 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] opacity-60 grayscale flex items-center gap-3">
                  <ShieldAlert size={18} className="text-[color:var(--text-muted)]" />
                  <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Administrative tools locked</span>
                </div>
              )}
            </div>
          </div>

          <button
            onClick={() => void logout()}
            className="btn-secondary w-full h-12 gap-3 text-rose-500 hover:bg-rose-500/10 hover:border-rose-500/20"
          >
            <LogOut size={18} />
            <span className="text-xs font-bold uppercase tracking-widest">De-authenticate Session</span>
          </button>
        </Panel>

        {/* Providers Panel — full width */}
        <Panel className="p-6 space-y-6 md:col-span-2">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <Bot size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">LLM Providers</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Configure &amp; manage LLM providers</p>
            </div>
            <button onClick={() => { setLoadingProviders(true); fetchStatus(); }}
              className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors p-1">
              <RefreshCw size={14} className={loadingProviders ? 'animate-spin' : ''} />
            </button>
          </div>

          {loadingProviders && !providerStatus ? (
            <div className="flex items-center justify-center py-8 text-[color:var(--text-muted)]">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <ProviderRow
                name="Anthropic"
                providerId="anthropic"
                status={providerStatus?.providers.anthropic ?? null}
                onSave={(data) => handleSaveProvider('anthropic', data)}
                saving={savingProvider === 'anthropic'}
                isPrimary={primaryProvider === 'anthropic'}
                onSetPrimary={() => handleSetPrimary('anthropic')}
                onRemove={() => handleRemoveProvider('anthropic')}
              />
              <ProviderRow
                name="OpenAI"
                providerId="openai"
                status={providerStatus?.providers.openai ?? null}
                onSave={(data) => handleSaveProvider('openai', data)}
                saving={savingProvider === 'openai'}
                isPrimary={primaryProvider === 'openai'}
                onSetPrimary={() => handleSetPrimary('openai')}
                onRemove={() => handleRemoveProvider('openai')}
                canImportOauth={codexOauthImportAvailable}
                importingOauth={importingCodexOauth}
                onImportOauth={handleImportCodexOauth}
              />
              <ProviderRow
                name="Google Gemini"
                providerId="gemini"
                status={providerStatus?.providers.gemini ?? null}
                onSave={(data) => handleSaveProvider('gemini', data)}
                saving={savingProvider === 'gemini'}
                isPrimary={primaryProvider === 'gemini'}
                onSetPrimary={() => handleSetPrimary('gemini')}
                onRemove={() => handleRemoveProvider('gemini')}
              />
            </div>
          )}

          <div className="bg-[color:var(--surface-1)] p-4 rounded-xl border border-[color:var(--border-subtle)] flex items-start gap-3">
            <Info size={16} className="text-[color:var(--accent-solid)] shrink-0 mt-0.5" />
            <p className="text-[11px] text-[color:var(--text-secondary)] leading-relaxed font-medium">
              Changes take effect immediately. Each effort level (Fast / Normal / Deep Think) routes to the appropriate model per provider. The primary provider handles requests first; the other is used as fallback.
            </p>
          </div>
        </Panel>

        <Panel className="p-6 space-y-4 md:col-span-2">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <KeyRound size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Runtimes</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Where Sentinel executes sessions for this instance</p>
            </div>
            <button onClick={() => { void fetchRuntimes(); }}
              className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors p-1"
              title="Refresh runtimes">
              <RefreshCw size={14} className={loadingRuntimes ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* Existing runtimes list */}
          {loadingRuntimes && runtimes.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-[color:var(--text-muted)]">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : runtimes.length === 0 && !addingNewTarget ? (
            <div className="flex flex-col items-center justify-center py-12 text-center gap-4 rounded-xl border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]">
              <div className="p-3 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)]">
                <Server size={24} className="text-[color:var(--text-muted)]" />
              </div>
              <div className="space-y-1 max-w-sm">
                <p className="text-xs font-bold uppercase tracking-widest">No runtimes yet</p>
                <p className="text-[11px] text-[color:var(--text-muted)] leading-relaxed">
                  Provision a Lima VM, Docker container, or connect an SSH host you already manage.
                </p>
              </div>
              <button onClick={startAddingRuntime} className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest">
                <Plus size={14} /> Add your first runtime
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {runtimes.map((target) =>
                editingRuntimeId === target.id
                  ? <div key={target.id}>{renderRuntimeForm()}</div>
                  : renderTargetCard(target),
              )}
            </div>
          )}

          {/* Add-runtime affordance — collapses chooser/form */}
          {addingNewTarget && !selectedProvider && (() => {
            const capByProvider = new Map((runtimeCapabilities?.providers ?? []).map((p) => [p.provider, p]));
            const tiles: Array<{ id: 'lima' | 'docker' | 'ssh'; label: string; description: string; icon: typeof Cpu }> = [
              { id: 'lima', label: 'Lima VM', description: 'Provisioned Linux VM via Lima.', icon: Cpu },
              { id: 'docker', label: 'Docker', description: 'Provisioned Linux container via Docker.', icon: Container },
              { id: 'ssh', label: 'Custom SSH', description: 'Bring your own SSH host.', icon: KeyRound },
            ];
            return (
              <div className="rounded-xl border border-[color:var(--accent-solid)]/40 bg-[color:var(--surface-0)] p-4 space-y-3 animate-in fade-in slide-in-from-top-1 duration-200">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">Choose how to add a runtime</span>
                  <button type="button" onClick={resetRuntimeForm} className="p-1 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors" title="Cancel">
                    <X size={14} />
                  </button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {tiles.map((tile) => {
                    const cap = capByProvider.get(tile.id);
                    const isSsh = tile.id === 'ssh';
                    const available = isSsh ? true : (cap?.available ?? false);
                    const Icon = tile.icon;
                    return (
                      <button
                        key={tile.id}
                        type="button"
                        onClick={() => pickProvider(tile.id)}
                        className="group text-left rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] hover:border-[color:var(--accent-solid)] hover:bg-[color:var(--surface-2)] transition-all p-3 space-y-1.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <Icon size={16} className="text-[color:var(--text-secondary)] group-hover:text-[color:var(--accent-solid)] transition-colors" />
                          {isSsh ? null : available ? (
                            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Ready</span>
                          ) : (
                            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">Setup needed</span>
                          )}
                        </div>
                        <div className="text-[11px] font-bold text-[color:var(--text-primary)]">{tile.label}</div>
                        <div className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">{tile.description}</div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* SSH form — selected from chooser */}
          {addingNewTarget && selectedProvider === 'ssh' && renderRuntimeForm()}

          {/* Lima/Docker contextual form — selected from chooser */}
          {addingNewTarget && (selectedProvider === 'lima' || selectedProvider === 'docker') && (() => {
            const cap = runtimeCapabilities?.providers.find((p) => p.provider === selectedProvider);
            const available = cap?.available ?? false;
            const isCreating = creatingProvider === selectedProvider;
            const providerLabel = selectedProvider === 'lima' ? 'Lima VM' : 'Docker';
            const installSteps = (cap?.missing ?? [])
              .filter((m) => RUNTIME_INSTALL_HINTS[m] !== undefined)
              .map((m) => ({ key: m, command: RUNTIME_INSTALL_HINTS[m] }));
            return (
              <div className="rounded-xl border border-[color:var(--accent-solid)]/40 bg-[color:var(--surface-0)] p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">New {providerLabel} runtime</span>
                  <button type="button" onClick={resetRuntimeForm} disabled={isCreating} className="p-1 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors disabled:opacity-40" title="Cancel">
                    <X size={14} />
                  </button>
                </div>

                {!available ? (
                  <div className="space-y-3">
                    <div className="text-[11px] text-amber-400 leading-relaxed">
                      {providerLabel} setup is incomplete on this machine. Install the missing tools below, then refresh.
                    </div>
                    {installSteps.length > 0 ? (
                      <div className="space-y-1.5">
                        {installSteps.map((step) => (
                          <div key={step.key} className="flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2.5 py-1.5">
                            <div className="flex-1 min-w-0">
                              <div className="text-[10px] text-[color:var(--text-muted)] uppercase tracking-wider">{step.key}</div>
                              {step.command ? (
                                <code className="font-mono text-[11px] text-[color:var(--text-primary)] break-all">{step.command}</code>
                              ) : (
                                <span className="text-[10px] text-[color:var(--text-muted)] italic">bundled with Sentinel — reinstall if missing</span>
                              )}
                            </div>
                            {step.command && (
                              <button
                                type="button"
                                onClick={() => { navigator.clipboard.writeText(step.command!); toast.success('Copied'); }}
                                className="shrink-0 p-1 rounded text-[color:var(--text-muted)] hover:text-[color:var(--accent-solid)] transition-colors"
                                title="Copy to clipboard"
                              >
                                <Copy size={12} />
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-[10px] text-[color:var(--text-muted)] italic">
                        Missing: {cap?.missing.join(', ') ?? 'unknown'}
                      </div>
                    )}
                    <div className="flex items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
                      <button type="button" onClick={resetRuntimeForm} className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest">Close</button>
                      <div className="flex-1" />
                      <button type="button" onClick={() => { void fetchRuntimes(); }} className="btn-secondary h-10 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest">
                        <RefreshCw size={14} /> Recheck
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="text-[11px] text-[color:var(--text-muted)] leading-relaxed">
                      {selectedProvider === 'lima'
                        ? 'Sentinel will provision a Linux VM via Lima. First run takes a few minutes.'
                        : 'Sentinel will provision a Linux container via Docker. First run takes ~1 minute.'}
                    </p>

                    <label className="block space-y-1">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Name</span>
                      <input
                        value={runtimeForm.name}
                        onChange={(e) => updateRuntimeForm({ name: e.target.value })}
                        disabled={isCreating}
                        className={`${runtimeInputClass} w-full disabled:opacity-50`}
                      />
                    </label>

                    {isCreating && (
                      <div className="rounded-md border-l-2 border-amber-500/50 bg-[color:var(--surface-1)]/40 pl-2 pr-1.5 py-1.5">
                        <div className="flex items-center gap-2">
                          <Loader2 size={12} className="animate-spin text-amber-400 shrink-0" />
                          <span className="text-[10px] text-[color:var(--text-secondary)] leading-snug">{creatingJobMessage ?? 'Working…'}</span>
                        </div>
                      </div>
                    )}

                    <div className="flex items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
                      <button type="button" onClick={resetRuntimeForm} disabled={isCreating} className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40">Cancel</button>
                      <div className="flex-1" />
                      <button
                        type="button"
                        onClick={() => void createRuntime(selectedProvider)}
                        disabled={isCreating || !runtimeForm.name.trim()}
                        className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {isCreating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                        Create runtime
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })()}

          {/* Add button — visible only when no flow is active */}
          {!addingNewTarget && editingRuntimeId === null && runtimes.length > 0 && (
            <button
              type="button"
              onClick={startAddingRuntime}
              className="w-full h-12 rounded-xl border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--accent-solid)] hover:bg-[color:var(--surface-1)] text-[color:var(--text-muted)] hover:text-[color:var(--accent-solid)] transition-colors flex items-center justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              <Plus size={14} /> Add runtime
            </button>
          )}

          <div className="bg-[color:var(--surface-1)] p-3 rounded-xl border border-[color:var(--border-subtle)] flex items-start gap-2.5">
            <Info size={14} className="text-[color:var(--accent-solid)] shrink-0 mt-0.5" />
            <p className="text-[10px] text-[color:var(--text-secondary)] leading-relaxed">
              Click a runtime to make it active for this instance. Runtimes are shared across instances; assigning one here only affects this instance.
            </p>
          </div>
        </Panel>

        {/* Backup & Restore Panel — full width */}
        <Panel className="p-6 space-y-6 md:col-span-2">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <Archive size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Backup &amp; Restore</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">Export &amp; import this instance</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Backup */}
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Download size={14} className="text-[color:var(--accent-solid)]" />
                <h3 className="text-xs font-bold uppercase tracking-widest">Create Backup</h3>
              </div>
              <p className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                Select what to include, set a passphrase, and download an encrypted archive.
              </p>

              <div className="space-y-1.5">
                {backupItems.length === 0 ? (
                  <div className="text-[10px] text-[color:var(--text-muted)] uppercase tracking-widest py-2">No items available</div>
                ) : (
                  backupItems.map((item) => (
                    <label
                      key={item.key}
                      className="w-full flex items-center gap-3 p-2.5 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] transition-colors cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        className="w-4 h-4 shrink-0 accent-[color:var(--accent-solid)]"
                        checked={backupSelection.has(item.key)}
                        onChange={() => toggleBackupItem(item.key)}
                      />
                      <span className="text-xs font-medium">{item.label}</span>
                    </label>
                  ))
                )}
              </div>

              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                <input
                  type="password"
                  value={backupPassphrase}
                  onChange={(e) => setBackupPassphrase(e.target.value)}
                  placeholder="Encryption passphrase"
                  className={`${runtimeInputClass} w-full pl-9`}
                />
              </div>

              <button
                type="button"
                onClick={() => void handleCreateBackup()}
                disabled={creatingBackup || backupSelection.size === 0 || !backupPassphrase}
                className="btn-primary w-full h-10 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {creatingBackup ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                Create backup
              </button>
            </div>

            {/* Restore */}
            <div className="space-y-4 md:border-l md:border-[color:var(--border-subtle)] md:pl-6">
              <div className="flex items-center gap-2">
                <Upload size={14} className="text-[color:var(--accent-solid)]" />
                <h3 className="text-xs font-bold uppercase tracking-widest">Restore Backup</h3>
              </div>
              <p className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                Upload an archive, unlock it, then choose what to restore. Existing records are never overwritten.
              </p>

              <label className="flex items-center gap-3 p-2.5 rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--accent-solid)] transition-colors cursor-pointer">
                <Upload size={14} className="text-[color:var(--text-muted)]" />
                <span className="text-xs font-medium truncate">{restoreFile ? restoreFile.name : 'Choose backup file…'}</span>
                <input
                  type="file"
                  accept=".sntl,application/octet-stream"
                  className="hidden"
                  onChange={(e) => void handleRestoreFileSelected(e.target.files?.[0] ?? null)}
                />
              </label>

              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                <input
                  type="password"
                  value={restorePassphrase}
                  onChange={(e) => setRestorePassphrase(e.target.value)}
                  placeholder="Backup passphrase"
                  className={`${runtimeInputClass} w-full pl-9`}
                />
              </div>

              {!restoreInfo ? (
                <button
                  type="button"
                  onClick={() => void handleInspectBackup()}
                  disabled={inspectingBackup || !restoreData || !restorePassphrase}
                  className="btn-secondary w-full h-10 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {inspectingBackup ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
                  Unlock backup
                </button>
              ) : (
                <div className="space-y-4">
                  <div className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                    {restoreInfo.source_instance && <div>Source: <span className="font-mono text-[color:var(--text-secondary)]">{restoreInfo.source_instance}</span></div>}
                    {restoreInfo.created_at && <div>Created: <span className="font-mono text-[color:var(--text-secondary)]">{restoreInfo.created_at}</span></div>}
                    {restoreInfo.created_by_version && <div>Made by Sentinel <span className="font-mono text-[color:var(--text-secondary)]">{restoreInfo.created_by_version}</span></div>}
                  </div>

                  {!restoreInfo.restorable && restoreInfo.compatibility && (
                    <div className="text-[11px] leading-relaxed p-3 rounded-lg border border-[color:var(--danger-border,#7f1d1d)] bg-[color:var(--danger-surface,rgba(127,29,29,0.12))] text-[color:var(--danger-text,#fca5a5)]">
                      {restoreInfo.compatibility}
                    </div>
                  )}

                  <div className="space-y-1.5">
                    {restoreInfo.items.length === 0 ? (
                      <div className="text-[10px] text-[color:var(--text-muted)] uppercase tracking-widest py-2">Backup contains no items</div>
                    ) : (
                      restoreInfo.items.map((key) => (
                        <label
                          key={key}
                          className="w-full flex items-center gap-3 p-2.5 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] transition-colors cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            className="w-4 h-4 shrink-0 accent-[color:var(--accent-solid)]"
                            checked={restoreSelection.has(key)}
                            onChange={() => toggleRestoreItem(key)}
                          />
                          <span className="text-xs font-medium">{backupItemLabel(key)}</span>
                        </label>
                      ))
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={resetRestore}
                      className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleRestoreBackup()}
                      disabled={restoringBackup || restoreSelection.size === 0 || !restoreInfo.restorable}
                      className="btn-primary flex-1 h-10 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {restoringBackup ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                      Restore selected
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="bg-[color:var(--surface-1)] p-3 rounded-xl border border-[color:var(--border-subtle)] flex items-start gap-2.5">
            <Info size={14} className="text-[color:var(--accent-solid)] shrink-0 mt-0.5" />
            <p className="text-[10px] text-[color:var(--text-secondary)] leading-relaxed">
              Backups are encrypted with your passphrase and contain decrypted secrets — store them securely. The passphrase cannot be recovered if lost.
            </p>
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
