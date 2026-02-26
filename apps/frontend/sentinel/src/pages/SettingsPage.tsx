import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import {
  LogOut, ShieldAlert, User, Cpu, Hash, Info, ChevronRight,
  Bot, Eye, EyeOff, Check, Loader2, RefreshCw, HelpCircle, X, Plug,
} from 'lucide-react';
import { Link as RouterLink } from 'react-router-dom';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { APP_VERSION } from '../lib/env';
import { api } from '../lib/api';
import { persistAraiosSwitchUrl } from '../lib/araios-switch';
import { useAuthStore } from '../store/auth-store';

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

interface AraiOSIntegrationResponse {
  configured: boolean;
  base_url: string | null;
  masked_agent_api_key: string | null;
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
      'Install and run the Codex CLI: npx codex --full-setup',
      'A browser opens — sign in with your OpenAI / ChatGPT account via auth.openai.com.',
      'After authorization, the token is stored in ~/.codex/auth.json.',
      'Copy the access_token value and paste it here.',
    ],
    command: 'npx codex --full-setup',
  },
};

// ── provider editor ─────────────────────────────────────────────────────────

function ProviderRow({
  name, status, onSave, saving, providerId, isPrimary, onSetPrimary, onRemove,
}: {
  name: string;
  status: ProviderStatus | null;
  onSave: (data: { apiKey?: string; oauthToken?: string }) => void;
  saving: boolean;
  providerId: 'anthropic' | 'openai' | 'gemini';
  isPrimary: boolean;
  onSetPrimary: () => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const help = OAUTH_HELP[providerId];
  const [mode, setMode] = useState<'oauth' | 'api'>(help ? 'api' : 'api');
  const [value, setValue] = useState('');
  const [showValue, setShowValue] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const configured = status?.configured ?? false;

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
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-[color:var(--accent-solid)]/15 text-[color:var(--accent-solid)]">Primary</span>
          )}
          {configured && !isPrimary && (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">Fallback</span>
          )}
          {configured && (
            <StatusChip label={status?.auth_method === 'oauth' ? 'OAuth' : 'API Key'} tone="good" className="scale-90" />
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
          <button onClick={() => { setEditing(v => !v); setConfirmRemove(false); }}
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
                  { id: 'oauth', label: 'OAuth Token' },
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
              <input type={showValue ? 'text' : 'password'} value={value} onChange={e => setValue(e.target.value)}
                placeholder={mode === 'oauth' ? 'Paste OAuth token...' : 'Paste API key...'}
                className="input-field h-10 pr-10 font-mono text-xs w-full"
                onKeyDown={e => e.key === 'Enter' && handleSave()}
              />
              <button type="button" onClick={() => setShowValue(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                {showValue ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <button onClick={handleSave} disabled={!value.trim() || saving}
              className="btn-primary h-10 px-4 text-[10px] font-bold uppercase tracking-widest shrink-0">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── main page ───────────────────────────────────────────────────────────────

export function SettingsPage() {
  const userId = useAuthStore((s) => s.userId);
  const role = useAuthStore((s) => s.role);
  const logout = useAuthStore((s) => s.logout);

  const isAdmin = role === 'admin';

  const [providerStatus, setProviderStatus] = useState<ProvidersStatusResponse | null>(null);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [araiosStatus, setAraiosStatus] = useState<AraiOSIntegrationResponse | null>(null);
  const [loadingAraiOS, setLoadingAraiOS] = useState(true);
  const [savingAraiOS, setSavingAraiOS] = useState(false);
  const [araiosBaseUrl, setAraiosBaseUrl] = useState('');
  const [araiosAgentApiKey, setAraiosAgentApiKey] = useState('');
  const [showAraiOSKey, setShowAraiOSKey] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const [providers, araios] = await Promise.all([
        api.get<ProvidersStatusResponse>('/onboarding/api-keys/status'),
        api.get<AraiOSIntegrationResponse>('/onboarding/araios'),
      ]);
      setProviderStatus(providers);
      setAraiosStatus(araios);
      setAraiosBaseUrl(araios.base_url || '');
      persistAraiosSwitchUrl(araios.base_url);
    } catch {
      // silent — panel will show loading state
    } finally {
      setLoadingProviders(false);
      setLoadingAraiOS(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

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
      }
      await api.post('/onboarding/api-keys', body);
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
      await api.delete('/onboarding/api-keys', { provider });
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini' };
      toast.success(`${labels[provider]} provider removed`);
      await fetchStatus();
    } catch {
      toast.error('Failed to remove provider');
    }
  }

  async function handleSetPrimary(provider: 'anthropic' | 'openai' | 'gemini') {
    try {
      await api.post('/onboarding/primary-provider', { provider });
      const labels = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Gemini' };
      toast.success(`${labels[provider]} set as primary`);
      await fetchStatus();
    } catch {
      toast.error('Failed to set primary provider');
    }
  }

  async function handleSaveAraiOS() {
    if (!araiosBaseUrl.trim()) {
      toast.error('AraiOS base URL is required');
      return;
    }
    setSavingAraiOS(true);
    try {
      await api.post('/onboarding/araios', {
        enabled: true,
        base_url: araiosBaseUrl.trim(),
        agent_api_key: araiosAgentApiKey.trim() || undefined,
      });
      toast.success('AraiOS integration updated');
      setAraiosAgentApiKey('');
      await fetchStatus();
    } catch {
      toast.error('Failed to update AraiOS integration');
    } finally {
      setSavingAraiOS(false);
    }
  }

  async function handleDisableAraiOS() {
    setSavingAraiOS(true);
    try {
      await api.post('/onboarding/araios', { enabled: false });
      toast.success('AraiOS integration disabled');
      setAraiosAgentApiKey('');
      await fetchStatus();
    } catch {
      toast.error('Failed to disable AraiOS integration');
    } finally {
      setSavingAraiOS(false);
    }
  }

  const primaryProvider = providerStatus?.primary_provider ?? 'anthropic';

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
              Credentials are derived from your araiOS session. Contact your system administrator to modify role assignments.
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
                  to="/settings/admin"
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
            <button onClick={() => { setLoadingProviders(true); setLoadingAraiOS(true); fetchStatus(); }}
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

        {/* AraiOS Integration Panel */}
        <Panel className="p-6 space-y-6 md:col-span-2">
          <div className="flex items-center gap-3 border-b border-[color:var(--border-subtle)] pb-4">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <Plug size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">AraiOS Integration</h2>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-tighter">
                Configure the araios_api tool endpoint and key
              </p>
            </div>
            <button
              onClick={() => { setLoadingProviders(true); setLoadingAraiOS(true); fetchStatus(); }}
              className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors p-1"
            >
              <RefreshCw size={14} className={loadingAraiOS ? 'animate-spin' : ''} />
            </button>
          </div>

          {loadingAraiOS && !araiosStatus ? (
            <div className="flex items-center justify-center py-6 text-[color:var(--text-muted)]">
              <Loader2 size={18} className="animate-spin" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-bold uppercase tracking-widest">Status</div>
                    <div className="text-[10px] text-[color:var(--text-muted)] mt-1">
                      {araiosStatus?.configured ? 'Configured and ready for araios_api tool calls.' : 'Not configured.'}
                    </div>
                  </div>
                  <StatusChip
                    label={araiosStatus?.configured ? 'Connected' : 'Not configured'}
                    tone={araiosStatus?.configured ? 'good' : 'warn'}
                  />
                </div>
                {araiosStatus?.masked_agent_api_key && (
                  <div className="mt-3 text-[10px] font-mono text-[color:var(--text-muted)]">
                    {araiosStatus.masked_agent_api_key}
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Base URL</label>
                <input
                  value={araiosBaseUrl}
                  onChange={(e) => setAraiosBaseUrl(e.target.value)}
                  placeholder="http://localhost:4747/araios"
                  className="input-field h-10 font-mono text-xs"
                />
              </div>

              <div className="space-y-2">
                <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                  Agent API Key <span className="normal-case font-normal">(optional, for rotation)</span>
                </label>
                <div className="relative">
                  <input
                    type={showAraiOSKey ? 'text' : 'password'}
                    value={araiosAgentApiKey}
                    onChange={(e) => setAraiosAgentApiKey(e.target.value)}
                    placeholder="sk-arais-agent-..."
                    className="input-field h-10 pr-10 font-mono text-xs w-full"
                  />
                  <button
                    type="button"
                    onClick={() => setShowAraiOSKey((value) => !value)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]"
                  >
                    {showAraiOSKey ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
                <p className="text-[10px] text-[color:var(--text-muted)]">
                  Leave blank to keep the current key. Enter a new key to rotate credentials.
                </p>
              </div>

              <div className="flex items-center gap-3">
                <button
                  onClick={handleSaveAraiOS}
                  disabled={savingAraiOS || !araiosBaseUrl.trim()}
                  className="btn-primary h-10 px-4 text-[10px] font-bold uppercase tracking-widest"
                >
                  {savingAraiOS ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                  Save AraiOS Config
                </button>
                <button
                  onClick={handleDisableAraiOS}
                  disabled={savingAraiOS || !araiosStatus?.configured}
                  className="btn-secondary h-10 px-4 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500/10 hover:border-rose-500/20"
                >
                  Disable
                </button>
              </div>
            </div>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}
