import { MCPSettings } from './MCPSettings';
import { GitPage } from './GitPage';
import { TelegramPage } from './TelegramPage';
import { AppearanceSettings } from '../components/AppearanceControls';
import { VoiceSettings } from '../components/VoiceSettings';
import { useLocation } from 'react-router-dom';
import { useInstanceName } from '../lib/workspace-context';
import { providersChanged } from '../hooks/useModelCatalog';
import { useState, useEffect, useCallback } from 'react';
import { notificationPublisher } from '../lib/notifications';
import {
  ShieldAlert, Info, KeyRound, GitBranch, Send,
  Bot, Eye, EyeOff, Check, Loader2, HelpCircle, X, KeySquare,
  Trash2, AudioLines,
  Archive, Download, Upload, Lock, Server, Type, Plug,
} from 'lucide-react';

import { DesktopManagement } from '../components/DesktopManagement';
import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { PillSwitch } from '../components/ui/PillSwitch';
import { api, requestBlob } from '../lib/api';
import './settings-page.css';
import { OllamaProviderSettings } from '../components/OllamaProviderSettings';
import { ProvidersSettings, type Provider, type ProviderStatus, type ProvidersStatusResponse } from '../components/ProvidersSettings';

const notify = notificationPublisher('Settings');


// ── types ───────────────────────────────────────────────────────────────────

interface DesktopCodexOauthStatus {
  enabled: boolean;
  auth_file_found: boolean;
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
}

interface BackupImportResponse {
  imported: number;
  skipped: number;
  by_table: Record<string, Record<string, number>>;
}

const runtimeInputClass = 'h-10 rounded-lg border border-(--border-subtle) bg-(--surface-1) px-3 text-xs font-medium text-(--text-primary) placeholder:text-(--text-muted) outline-hidden transition-colors focus:border-(--accent-solid)';

// ── OAuth help content ──────────────────────────────────────────────────────

const OAUTH_HELP: Record<string, { title: string; steps: string[]; command: string }> = {
  anthropic: {
    title: 'How to get an Anthropic OAuth token',
    steps: [
      'Install the Claude CLI: npm install -g @anthropic-ai/claude-code',
      'Run: claude auth login',
      'A browser window opens — log in with your Anthropic account and authorize.',
      'Choose Auto-sync CLI so Sentinel reads the current login for every request. You can also enter a token manually.',
    ],
    command: 'claude auth login',
  },
  openai: {
    title: 'How to get an OpenAI Codex OAuth token',
    steps: [
      'Install or update the Codex CLI: npm i -g @openai/codex',
      'Run: codex login',
      'Click Sign in with ChatGPT and complete the browser flow.',
      'After authorization, credentials are stored locally in ~/.codex/auth.json.',
      'Choose Auto-sync CLI, or enter an access_token manually.',
    ],
    command: 'codex login',
  },
  gemini: {
    title: 'How to connect Gemini with Antigravity',
    steps: [
      'Install Antigravity CLI from antigravity.google and run agy.',
      'Sign in with your personal Google account.',
      'On macOS, choose Auto-sync CLI.',
      'You can also paste an exported Antigravity OAuth credential bundle containing a refresh_token.',
    ],
    command: 'agy',
  },
};

// ── provider editor ─────────────────────────────────────────────────────────

function ProviderConnectionSettings({
  status, onSave, saving, providerId,
  canSyncOauth = false, syncingOauth = false, onSyncOauth,
}: {
  status: ProviderStatus | null;
  onSave: (data: { apiKey?: string; oauthToken?: string }) => void;
  saving: boolean;
  providerId: 'anthropic' | 'openai' | 'gemini';
  canSyncOauth?: boolean;
  syncingOauth?: boolean;
  onSyncOauth?: () => void;
}) {
  const help = OAUTH_HELP[providerId];
  const [mode, setMode] = useState<'oauth' | 'api'>(status?.auth_method === 'oauth' ? 'oauth' : 'api');
  const [oauthSource, setOauthSource] = useState<'cli' | 'manual'>(status?.auth_source === 'manual' ? 'manual' : 'cli');
  const [value, setValue] = useState('');
  const [showValue, setShowValue] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const isGeminiOauth = providerId === 'gemini' && mode === 'oauth' && oauthSource === 'manual';
  const oauthLabel = 'OAuth';
  const cliName = providerId === 'anthropic' ? 'Claude' : providerId === 'gemini' ? 'Antigravity' : 'Codex';

  function handleSave() {
    if (!value.trim()) return;
    onSave(mode === 'oauth' ? { oauthToken: value.trim() } : { apiKey: value.trim() });
    setValue('');
  }

  return (
    <section className="provider-connection-editor" aria-label="Connection settings">
      <h3>Connection</h3>
      <div className="settings-provider-editor space-y-3">
          {help ? (
            <div className="provider-auth-mode">
              <PillSwitch label="Connection method" className="provider-connection-switch" value={mode}
                onChange={next => { setMode(next); setShowHelp(false); }}
                options={[{ value: 'oauth', label: oauthLabel, icon: <KeyRound size={14} /> }, { value: 'api', label: 'API Key', icon: <KeySquare size={14} /> }]} />
              {mode === 'oauth' && (
                <button onClick={() => setShowHelp(v => !v)}
                  className={`p-1 rounded-md transition-colors ${showHelp ? 'text-(--accent-solid)' : 'text-(--text-muted) hover:text-(--text-primary)'}`}
                  aria-label="OAuth connection help" title="How to get an OAuth token">
                  <HelpCircle size={14} />
                </button>
              )}
            </div>
          ) : null}

          {/* OAuth help popup */}
          {showHelp && mode === 'oauth' && help && (
            <div className="rounded-lg border border-(--border) bg-(--surface-1) p-3 space-y-2 animate-in fade-in duration-200">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-primary)">{help.title}</span>
                <button onClick={() => setShowHelp(false)} className="text-(--text-muted) hover:text-(--text-primary)">
                  <X size={12} />
                </button>
              </div>
              <ol className="space-y-1.5 list-decimal list-inside">
                {help.steps.map((step, i) => (
                  <li key={i} className="text-[10px] text-(--text-muted) leading-relaxed">
                    {step}
                  </li>
                ))}
              </ol>
              <div className="flex items-center gap-2 rounded-md bg-(--app-bg) px-2 py-1.5 font-mono text-[11px] text-(--text-primary) border border-(--border)">
                <span className="flex-1">{help.command}</span>
                <button onClick={() => { navigator.clipboard.writeText(help.command); notify.success('Copied'); }}
                  className="text-[9px] font-bold uppercase tracking-widest text-(--accent-solid) hover:opacity-70 transition-opacity shrink-0">
                  Copy
                </button>
              </div>
            </div>
          )}

          {mode === 'oauth' && canSyncOauth && (
            <div className="flex rounded-lg bg-(--surface-2) p-0.5 w-fit">
              <button type="button" onClick={() => setOauthSource('cli')}
                className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${oauthSource === 'cli' ? 'bg-(--accent-solid) text-(--app-bg)' : 'text-(--text-muted) hover:text-(--text-primary)'}`}>
                Auto-sync CLI
              </button>
              <button type="button" onClick={() => setOauthSource('manual')}
                className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${oauthSource === 'manual' ? 'bg-(--accent-solid) text-(--app-bg)' : 'text-(--text-muted) hover:text-(--text-primary)'}`}>
                Enter manually
              </button>
            </div>
          )}

          {(mode === 'api' || oauthSource === 'manual' || !canSyncOauth) && <div className="flex gap-2">
            <div className="relative flex-1 min-w-0">
              {isGeminiOauth ? (
                <textarea
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  placeholder='Paste Gemini OAuth credentials JSON...'
                  className="input-field min-h-[128px] py-3 font-mono text-xs w-full resize-y"
                />
              ) : (
                <>
                  <input aria-label={mode === 'oauth' ? oauthLabel : 'API key'} type={showValue ? 'text' : 'password'} value={value} onChange={e => setValue(e.target.value)}
                    placeholder={mode === 'oauth' ? 'Paste OAuth token...' : 'Paste API key...'}
                    className="input-field h-10 pr-10 font-mono text-xs w-full"
                    onKeyDown={e => e.key === 'Enter' && handleSave()}
                  />
                  <button type="button" aria-label={showValue ? 'Hide credential' : 'Show credential'} onClick={() => setShowValue(v => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary)">
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
          </div>}
          {canSyncOauth && mode === 'oauth' && oauthSource === 'cli' && onSyncOauth && (
            <button
              type="button"
              onClick={onSyncOauth}
              disabled={syncingOauth}
              className="btn-secondary h-9 gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              {syncingOauth ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
              {status?.auth_source === 'cli' ? `Reconnect ${cliName} auto-sync` : `Use ${cliName} CLI automatically`}
            </button>
          )}
          {mode === 'oauth' && oauthSource === 'cli' && (
            <p className="text-[10px] text-(--text-muted)">
              Uses your current {cliName} CLI login.
            </p>
          )}
          {isGeminiOauth && (
            <p className="text-[10px] text-(--text-muted)">
              Import your Antigravity login from macOS Keychain, or paste an exported Antigravity OAuth credential bundle. Sign in with <span className="font-mono text-(--text-primary)">agy</span> first.
            </p>
          )}
      </div>
    </section>
  );
}

// ── main page ───────────────────────────────────────────────────────────────

export function SettingsPage({ initialSection = 'providers' }: { initialSection?: 'providers' | 'git' | 'telegram' } = {}) {
  const instance = useInstanceName();
  const location = useLocation();
  const destination = location.state as { settingsInstance?: string; settingsSection?: string } | null;
  const voiceSettingsRequested = destination?.settingsInstance === instance && destination?.settingsSection === 'voice';
  const [section, setSection] = useState<'providers' | 'voice' | 'backup' | 'services' | 'updates' | 'appearance' | 'git' | 'telegram' | 'mcp'>(() => voiceSettingsRequested ? 'voice' : initialSection);
  useEffect(() => {
    if (voiceSettingsRequested) setSection('voice');
  }, [location.key, voiceSettingsRequested]);


  const [providerStatus, setProviderStatus] = useState<ProvidersStatusResponse | null>(null);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [codexOauthImportAvailable, setCodexOauthImportAvailable] = useState(false);
  const [importingCodexOauth, setImportingCodexOauth] = useState(false);
  const [importingClaudeOauth, setImportingClaudeOauth] = useState(false);
  const [importingGeminiOauth, setImportingGeminiOauth] = useState(false);
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


  const fetchStatus = useCallback(async () => {
    try {
      const providers = await api.get<ProvidersStatusResponse>('/settings/api-keys/status');
      setProviderStatus(providers);
      return true;
    } catch {
      return false;
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const providerUpdated = async () => {
    providersChanged(instance);
    await fetchStatus();
  };

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
      notify.error('Could not read backup file');
      setRestoreData(null);
    }
  }

  async function handleCreateBackup() {
    if (backupSelection.size === 0) {
      notify.error('Select at least one item to back up');
      return;
    }
    if (!backupPassphrase) {
      notify.error('Enter a passphrase to encrypt the backup');
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
      notify.success('Backup created');
      setBackupPassphrase('');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to create backup');
    } finally {
      setCreatingBackup(false);
    }
  }

  async function handleInspectBackup() {
    if (!restoreData) {
      notify.error('Select a backup file');
      return;
    }
    if (!restorePassphrase) {
      notify.error('Enter the backup passphrase');
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
      notify.error(error instanceof Error ? error.message : 'Failed to read backup');
    } finally {
      setInspectingBackup(false);
    }
  }

  async function handleRestoreBackup() {
    if (!restoreData || !restoreInfo) return;
    if (restoreSelection.size === 0) {
      notify.error('Select at least one item to restore');
      return;
    }
    setRestoringBackup(true);
    try {
      const result = await api.post<BackupImportResponse>('/backup/import', {
        data: restoreData,
        passphrase: restorePassphrase,
        items: Array.from(restoreSelection),
      });
      notify.success(`Restored ${result.imported} record(s), skipped ${result.skipped}`);
      resetRestore();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to restore backup');
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
      notify.success(`${labels[provider]} provider updated`);
      await providerUpdated();
    } catch {
      notify.error('Failed to update provider');
    } finally {
      setSavingProvider(null);
    }
  }

  async function handleRemoveProvider(provider: Provider) {
    try {
      await api.delete('/settings/api-keys', { provider });
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini', ollama: 'Ollama' };
      notify.success(`${labels[provider]} provider removed`);
      await providerUpdated();
    } catch (error) {
      notify.error('Failed to remove provider');
      throw error;
    }
  }

  async function handleSyncClaudeOauth() {
    setImportingClaudeOauth(true);
    try {
      await api.post('/settings/desktop-claude-oauth/connect');
      notify.success('Claude CLI auto-sync enabled');
      await providerUpdated();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Claude CLI auto-sync');
    } finally {
      setImportingClaudeOauth(false);
    }
  }

  async function handleSyncCodexOauth() {
    setImportingCodexOauth(true);
    try {
      await api.post('/settings/desktop-codex-oauth/connect');
      notify.success('Codex CLI auto-sync enabled');
      await providerUpdated();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Codex CLI auto-sync');
    } finally {
      setImportingCodexOauth(false);
    }
  }

  async function handleSyncGeminiOauth() {
    setImportingGeminiOauth(true);
    try {
      await api.post('/settings/desktop-gemini-oauth/connect');
      notify.success('Antigravity auto-sync enabled');
      await providerUpdated();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Antigravity auto-sync');
    } finally {
      setImportingGeminiOauth(false);
    }
  }

  return (
    <AppShell
      title="Settings"
      subtitle="Models and instance preferences"
      contentClassName="settings-pane p-0! overflow-hidden"
    >
      <div className="settings-workspace">
        <nav className="settings-navigation" aria-label="Settings sections">
          <span>SETTINGS</span>
          <button className="menu-selection-item" type="button" aria-current={section === 'providers' ? 'page' : undefined} onClick={() => setSection('providers')}><Bot size={16} />LLM Providers</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'voice' ? 'page' : undefined} onClick={() => setSection('voice')}><AudioLines size={16} />Voice</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'backup' ? 'page' : undefined} onClick={() => setSection('backup')}><Archive size={16} />Backup & Restore</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'appearance' ? 'page' : undefined} onClick={() => setSection('appearance')}><Type size={16} />Appearance</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'git' ? 'page' : undefined} onClick={() => setSection('git')}><GitBranch size={16} />Git & GitHub</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'telegram' ? 'page' : undefined} onClick={() => setSection('telegram')}><Send size={16} />Telegram</button>
          <button className="menu-selection-item" type="button" aria-current={section === 'mcp' ? 'page' : undefined} onClick={() => setSection('mcp')}><Plug size={16} />MCP servers</button>
          {window.sentinelDesktop && <><button className="menu-selection-item" type="button" aria-current={section === 'services' ? 'page' : undefined} onClick={() => setSection('services')}><Server size={16} />Services</button><button className="menu-selection-item" type="button" aria-current={section === 'updates' ? 'page' : undefined} onClick={() => setSection('updates')}><Download size={16} />Updates</button></>}
        </nav>
        <main className="settings-content"><div className="settings-layout">
        {section === 'mcp' && <MCPSettings />}
        {section === 'git' && <GitPage embedded />}
        {section === 'voice' && <VoiceSettings />}
        {section === 'telegram' && <TelegramPage embedded />}
        <div hidden={section !== 'appearance'}><AppearanceSettings /></div>
        <ProvidersSettings key={instance} status={providerStatus} loading={loadingProviders} active={section === 'providers'}
          onChanged={providerUpdated} onRoutingChanged={fetchStatus} onRetry={() => void fetchStatus()} onRemove={handleRemoveProvider}
          renderConnection={provider => provider === 'ollama'
            ? <OllamaProviderSettings onChanged={providerUpdated} />
            : <ProviderConnectionSettings providerId={provider}
                status={providerStatus?.providers[provider] ?? null}
                onSave={data => void handleSaveProvider(provider, data)}
                saving={savingProvider === provider}
                canSyncOauth={provider !== 'openai' || codexOauthImportAvailable}
                syncingOauth={provider === 'anthropic' ? importingClaudeOauth : provider === 'openai' ? importingCodexOauth : importingGeminiOauth}
                onSyncOauth={provider === 'anthropic' ? handleSyncClaudeOauth : provider === 'openai' ? handleSyncCodexOauth : handleSyncGeminiOauth}
              />}
        />

        {/* Backup & Restore Panel — full width */}
        <Panel hidden={section !== 'backup'} className="settings-section p-6 space-y-6">
          <div className="settings-section-heading flex items-center gap-3 pb-4">
            <div className="p-2 rounded-lg bg-(--surface-2) text-(--accent-solid)">
              <Archive size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Backup &amp; Restore</h2>
              <p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-tighter">Export &amp; import this instance</p>
            </div>
          </div>

          <div className="settings-backup-grid">
            {/* Backup */}
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Download size={14} className="text-(--accent-solid)" />
                <h3 className="text-xs font-bold uppercase tracking-widest">Create Backup</h3>
              </div>
              <p className="text-[10px] text-(--text-muted) leading-relaxed">
                Select what to include, set a passphrase, and download an encrypted archive.
              </p>

              <div className="settings-item-selection" role="group" aria-label="Backup contents">
                {backupItems.length === 0 ? (
                  <div className="text-[10px] text-(--text-muted) uppercase tracking-widest py-2">No items available</div>
                ) : (
                  backupItems.map((item) => (
                    <label
                      key={item.key}
                      className="settings-item-option"
                    >
                      <input
                        type="checkbox"
                        className="settings-item-checkbox"
                        checked={backupSelection.has(item.key)}
                        onChange={() => toggleBackupItem(item.key)}
                      />
                      <span className="text-xs font-medium">{item.label}</span>
                    </label>
                  ))
                )}
              </div>

              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-(--text-muted)" />
                <input
                  type="password"
                  aria-label="Encryption passphrase"
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
            <div className="space-y-4 settings-restore">
              <div className="flex items-center gap-2">
                <Upload size={14} className="text-(--accent-solid)" />
                <h3 className="text-xs font-bold uppercase tracking-widest">Restore Backup</h3>
              </div>
              <p className="text-[10px] text-(--text-muted) leading-relaxed">
                Upload an archive, unlock it, then choose what to restore. Existing records are never overwritten.
              </p>

              <label className="flex items-center gap-3 p-2.5 rounded-lg border border-dashed border-(--border-subtle) bg-(--surface-0) hover:border-(--accent-solid) transition-colors cursor-pointer">
                <Upload size={14} className="text-(--text-muted)" />
                <span className="text-xs font-medium truncate">{restoreFile ? restoreFile.name : 'Choose backup file…'}</span>
                <input
                  type="file"
                  accept=".sntl,application/octet-stream"
                  className="hidden"
                  onChange={(e) => void handleRestoreFileSelected(e.target.files?.[0] ?? null)}
                />
              </label>

              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-(--text-muted)" />
                <input
                  type="password"
                  aria-label="Backup passphrase"
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
                  <div className="text-[10px] text-(--text-muted) leading-relaxed">
                    {restoreInfo.source_instance && <div>Source: <span className="font-mono text-(--text-secondary)">{restoreInfo.source_instance}</span></div>}
                    {restoreInfo.created_at && <div>Created: <span className="font-mono text-(--text-secondary)">{restoreInfo.created_at}</span></div>}
                    {restoreInfo.created_by_version && <div>Made by Sentinel <span className="font-mono text-(--text-secondary)">{restoreInfo.created_by_version}</span></div>}
                  </div>

                  <div className="settings-item-selection" role="group" aria-label="Restore contents">
                    {restoreInfo.items.length === 0 ? (
                      <div className="text-[10px] text-(--text-muted) uppercase tracking-widest py-2">Backup contains no items</div>
                    ) : (
                      restoreInfo.items.map((key) => (
                        <label
                          key={key}
                          className="settings-item-option"
                        >
                          <input
                            type="checkbox"
                            className="settings-item-checkbox"
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
                      disabled={restoringBackup || restoreSelection.size === 0}
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

          <div className="bg-(--surface-1) p-3 rounded-xl border border-(--border-subtle) flex items-start gap-2.5">
            <Info size={14} className="text-(--accent-solid) shrink-0 mt-0.5" />
            <p className="text-[10px] text-(--text-secondary) leading-relaxed">
              Backups are encrypted with your passphrase and contain decrypted secrets — store them securely. The passphrase cannot be recovered if lost.
            </p>
          </div>
        </Panel>
        {window.sentinelDesktop && <div hidden={section !== 'services' && section !== 'updates'}><p className="settings-global-note">These controls apply to the whole app and all instances.</p><DesktopManagement sectionId={section === 'updates' ? 'updates' : 'services'} /></div>}
      </div></main>
      </div>
    </AppShell>
  );
}
