import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Zap, ArrowRight, ArrowLeft, Check, Bot, User, Flag, Cpu,
  Eye, EyeOff, Loader2, KeyRound, Plus, X, Wifi, Server, SkipForward,
} from 'lucide-react';
import { api } from '../lib/api';
import type { RuntimeSSHTarget, RuntimeSSHTargetTestResponse } from '../types/api';
import {
  buildAgentIdentityMemoryContent,
  buildUserProfileMemoryContent,
  resolveAgentIdentity,
  resolveUserProfile,
} from '../lib/onboarding-defaults';
import { instanceRouteFromPath } from '../lib/routes';

// ── types ────────────────────────────────────────────────────────────────────

interface StepMeta {
  id: string;
  label: string;
  icon: React.ReactNode;
  optional?: boolean;
}

interface StarterPromptOption {
  label: string;
  prompt: string;
}

interface DesktopCodexOauthStatus {
  enabled: boolean;
  auth_file_found: boolean;
}

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
  runtime_target_id: string | null;
}

const emptyRuntimeTargetForm = {
  name: '',
  host: '',
  port: '22',
  username: '',
  workspaces_dir: '',
  auth_type: 'private_key' as 'private_key' | 'password',
  private_key: '',
  password: '',
};

const RUNTIME_VERIFICATION_FIELDS = new Set<keyof typeof emptyRuntimeTargetForm>([
  'host', 'port', 'username', 'workspaces_dir', 'auth_type', 'private_key', 'password',
]);

const STEPS: StepMeta[] = [
  { id: 'welcome',  label: 'Welcome',       icon: <Zap size={14} /> },
  { id: 'llm',      label: 'Providers',     icon: <Bot size={14} /> },
  { id: 'runtime',  label: 'Runtime',       icon: <Cpu size={14} />, optional: true },
  { id: 'agent',    label: 'Your Agent',    icon: <Bot size={14} /> },
  { id: 'user',     label: 'About You',     icon: <User size={14} /> },
  { id: 'done',     label: 'Launch',        icon: <Flag size={14} /> },
];

const STARTER_PROMPT_OPTIONS: StarterPromptOption[] = [
  {
    label: 'Priority Plan',
    prompt: 'Map my top priorities for this workspace, propose the first 3 high-impact automations, and execute the safest one now.',
  },
  {
    label: 'Trigger Setup',
    prompt: 'Design and create a trigger strategy for this workspace: one daily summary trigger, one failure-alert trigger, and one webhook trigger.',
  },
  {
    label: 'Memory Audit',
    prompt: 'Audit my current memory structure, propose a cleaner hierarchy with root categories, and apply the highest-value memory improvements.',
  },
];

// ── sub-components ───────────────────────────────────────────────────────────

function StepIndicator({ current }: { current: number }) {
  return (
    <nav className="flex flex-col gap-1 w-44 shrink-0">
      {STEPS.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={step.id} className="flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all"
            style={{ background: active ? 'var(--surface-2)' : 'transparent' }}>
            <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold transition-all ${
              done ? 'bg-emerald-500 text-white' : active ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)]'
            }`}>
              {done ? <Check size={12} /> : i + 1}
            </div>
            <div className="flex flex-col min-w-0">
              <span className={`text-[11px] font-bold leading-tight ${active ? 'text-[color:var(--text-primary)]' : done ? 'text-emerald-500' : 'text-[color:var(--text-muted)]'}`}>
                {step.label}
              </span>
              {step.optional && (
                <span className="text-[9px] text-[color:var(--text-muted)] font-medium uppercase tracking-wider">Optional</span>
              )}
            </div>
          </div>
        );
      })}
    </nav>
  );
}

const onboardingInputClass = 'h-10 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-xs font-medium text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] outline-none focus:border-[color:var(--accent-solid)]';

function RuntimeStep({
  targets,
  selectedTargetId,
  onSelect,
  form,
  updateForm,
  onTest,
  onCreate,
  testing,
  saving,
  formOpen,
  setFormOpen,
  verified,
  resolvedHome,
}: {
  targets: RuntimeSSHTarget[];
  selectedTargetId: string;
  onSelect: (targetId: string) => void;
  form: typeof emptyRuntimeTargetForm;
  updateForm: (updates: Partial<typeof emptyRuntimeTargetForm>) => void;
  onTest: () => void;
  onCreate: () => void;
  testing: boolean;
  saving: boolean;
  formOpen: boolean;
  setFormOpen: Dispatch<SetStateAction<boolean>>;
  verified: boolean;
  resolvedHome: string | null;
}) {
  const hasTargets = targets.length > 0;
  const effectiveFormOpen = !hasTargets || formOpen;
  const suggestedWorkspaceRoot = resolvedHome
    ? `${resolvedHome.replace(/\/+$/, '')}/sentinel/workspaces`
    : null;
  const canShowSuggestion = !!(suggestedWorkspaceRoot && form.workspaces_dir.trim() !== suggestedWorkspaceRoot);

  function renderForm() {
    return (
      <div className="rounded-xl border border-[color:var(--accent-solid)]/40 bg-[color:var(--surface-1)] p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">
            {hasTargets ? 'New runtime target' : 'Create your first runtime target'}
          </span>
          {verified ? (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
              <Check size={10} /> Verified
            </span>
          ) : (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">
              Not verified
            </span>
          )}
          <div className="flex-1" />
          {hasTargets && (
            <button type="button" onClick={() => setFormOpen(false)} className="p-1 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors" title="Cancel">
              <X size={14} />
            </button>
          )}
        </div>

        <p className="text-[11px] leading-relaxed text-[color:var(--text-muted)]">
          Sentinel creates one session workspace per session under this remote root and runs tools through SSH/tmux.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Name</span>
            <input value={form.name} onChange={(e) => updateForm({ name: e.target.value })} className={`${onboardingInputClass} w-full`} />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Username</span>
            <input value={form.username} onChange={(e) => updateForm({ username: e.target.value })} className={`${onboardingInputClass} w-full`} />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Host</span>
            <input value={form.host} onChange={(e) => updateForm({ host: e.target.value })} placeholder="hostname or IP" className={`${onboardingInputClass} w-full font-mono`} />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Port</span>
            <input value={form.port} onChange={(e) => updateForm({ port: e.target.value })} className={`${onboardingInputClass} w-full font-mono`} />
          </label>
        </div>

        <label className="block space-y-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Workspace root</span>
          <input value={form.workspaces_dir} onChange={(e) => updateForm({ workspaces_dir: e.target.value })} className={`${onboardingInputClass} w-full font-mono`} />
          {canShowSuggestion && (
            <button
              type="button"
              onClick={() => updateForm({ workspaces_dir: suggestedWorkspaceRoot! })}
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
              <button key={mode} type="button" onClick={() => updateForm({ auth_type: mode })}
                className={`px-3 py-1.5 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${form.auth_type === mode ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'}`}>
                {mode === 'private_key' ? 'Private key' : 'Password'}
              </button>
            ))}
          </div>
          {form.auth_type === 'private_key' ? (
            <textarea
              value={form.private_key}
              onChange={(e) => updateForm({ private_key: e.target.value })}
              placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              className="w-full min-h-28 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 py-2 font-mono text-[10px] font-medium leading-relaxed text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] outline-none focus:border-[color:var(--accent-solid)]"
            />
          ) : (
            <input type="password" value={form.password} onChange={(e) => updateForm({ password: e.target.value })} className={`${onboardingInputClass} w-full`} />
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
          {hasTargets && (
            <button type="button" onClick={() => setFormOpen(false)} className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest">
              Cancel
            </button>
          )}
          <div className="flex-1" />
          {!verified && (
            <span className="text-[10px] text-amber-400 font-medium">
              Test connection to enable save
            </span>
          )}
          <button type="button" onClick={onTest} disabled={testing || saving} className="btn-secondary h-10 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest">
            {testing ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
            Test connection
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={saving || testing || !verified}
            title={!verified ? 'Run Test connection successfully before saving' : undefined}
            className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            Save & select
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-2xl font-black tracking-tight mb-1">Runtime target</h2>
        <p className="text-sm text-[color:var(--text-muted)]">
          Select the SSH machine where Sentinel will create session workspaces and run tools. You can change or add more from Settings later.
        </p>
      </div>

      {!hasTargets && !effectiveFormOpen ? null : !hasTargets ? (
        renderForm()
      ) : (
        <div className="space-y-2">
          {targets.map((target) => {
            const isSelected = selectedTargetId === target.id;
            return (
              <button
                key={target.id}
                type="button"
                onClick={() => onSelect(target.id)}
                className={`group w-full text-left rounded-xl border transition-all flex items-start gap-3 px-4 py-3 ${
                  isSelected
                    ? 'border-[color:var(--accent-solid)] bg-[color:var(--surface-1)] ring-1 ring-[color:var(--accent-solid)]/30'
                    : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-1)]'
                }`}
              >
                <div className={`mt-1 h-2.5 w-2.5 rounded-full shrink-0 ${isSelected ? 'bg-emerald-500' : 'bg-[color:var(--text-muted)]/40 group-hover:bg-[color:var(--text-muted)]'}`} />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-bold truncate">{target.name}</span>
                    {isSelected && (
                      <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
                        Selected
                      </span>
                    )}
                    <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">
                      {target.auth_type === 'private_key' ? 'Key' : 'Password'}
                    </span>
                  </div>
                  <div className="font-mono text-[10px] text-[color:var(--text-muted)] truncate">
                    {target.username}@{target.host}:{target.port}
                  </div>
                  <div className="font-mono text-[10px] text-[color:var(--text-muted)] truncate">
                    {target.workspaces_dir}
                  </div>
                </div>
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => onSelect('')}
            className={`group w-full text-left rounded-xl border transition-all flex items-start gap-3 px-4 py-3 ${
              selectedTargetId === ''
                ? 'border-[color:var(--accent-solid)] bg-[color:var(--surface-1)] ring-1 ring-[color:var(--accent-solid)]/30'
                : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-1)]'
            }`}
          >
            <div className={`mt-1 h-2.5 w-2.5 rounded-full shrink-0 ${selectedTargetId === '' ? 'bg-emerald-500' : 'bg-[color:var(--text-muted)]/40 group-hover:bg-[color:var(--text-muted)]'}`} />
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-center gap-2">
                <SkipForward size={13} className="text-[color:var(--text-muted)]" />
                <span className="text-xs font-bold">Skip for now</span>
              </div>
              <div className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                Continue without a runtime target. You can configure one later from Settings.
              </div>
            </div>
          </button>

          {effectiveFormOpen ? (
            renderForm()
          ) : (
            <button
              type="button"
              onClick={() => setFormOpen(true)}
              className="w-full h-12 rounded-xl border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--accent-solid)] hover:bg-[color:var(--surface-1)] text-[color:var(--text-muted)] hover:text-[color:var(--accent-solid)] transition-colors flex items-center justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              <Plus size={14} /> Add another runtime target
            </button>
          )}
        </div>
      )}

      {!hasTargets && (
        <button
          type="button"
          onClick={() => onSelect('')}
          className="w-full text-left rounded-xl border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] transition-colors flex items-start gap-3 px-4 py-3"
        >
          <Server size={14} className="text-[color:var(--text-muted)] mt-0.5" />
          <div className="flex-1">
            <div className="text-xs font-bold">Skip for now</div>
            <div className="text-[10px] text-[color:var(--text-muted)] leading-relaxed mt-0.5">
              Continue without a runtime target. You can configure one later from Settings.
            </div>
          </div>
        </button>
      )}
    </div>
  );
}

// ── steps ─────────────────────────────────────────────────────────────────────

function WelcomeStep() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-8 text-center px-8">
      <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-2xl shadow-black/30">
        <Zap size={40} fill="currentColor" />
      </div>
      <div className="space-y-3 max-w-md">
        <h1 className="text-3xl font-black tracking-tight text-[color:var(--text-primary)]">Welcome to Sentinel</h1>
        <p className="text-[color:var(--text-muted)] leading-relaxed text-sm">
          Let's take 2 minutes to set up your workspace. We'll configure your AI agent
          and create your memory foundation.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-4 max-w-lg w-full">
        {[
          { label: 'Root Memories', desc: 'Your agent knows who it is and who you are' },
          { label: 'API Keys', desc: 'Connect Claude for intelligence' },
        ].map(item => (
          <div key={item.label} className="rounded-xl bg-[color:var(--surface-2)] p-4 space-y-1.5">
            <div className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">{item.label}</div>
            <div className="text-[11px] text-[color:var(--text-muted)] leading-snug">{item.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ProviderCard({
  name, color, apiKey, setApiKey, oauthToken, setOauthToken,
  apiPlaceholder, oauthPlaceholder, apiHint, oauthInstructions,
  oauthHint, oauthInputKind = 'token', defaultMode = 'oauth',
  canImportOauth = false, importedOauth = false, importingOauth = false, onImportOauth,
}: {
  name: string; color: string;
  apiKey: string; setApiKey: (v: string) => void;
  oauthToken: string; setOauthToken: (v: string) => void;
  apiPlaceholder: string; oauthPlaceholder: string;
  apiHint: string;
  oauthInstructions?: React.ReactNode;
  oauthHint: React.ReactNode;
  oauthInputKind?: 'token' | 'json';
  defaultMode?: 'oauth' | 'api';
  canImportOauth?: boolean;
  importedOauth?: boolean;
  importingOauth?: boolean;
  onImportOauth?: () => void;
}) {
  const [showKey, setShowKey] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [mode, setMode] = useState<'oauth' | 'api'>(defaultMode);
  const [showHelp, setShowHelp] = useState(false);
  const hasValue = !!(apiKey || oauthToken || importedOauth);
  const oauthLabel = oauthInputKind === 'json' ? 'OAuth Credentials' : 'OAuth Token';

  return (
    <div className={`rounded-xl border-2 transition-all ${hasValue ? `border-emerald-500/40 bg-emerald-500/5` : 'border-[color:var(--border)] bg-[color:var(--surface-1)]'}`}>
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[color:var(--border-subtle)]">
        <div className={`h-2 w-2 rounded-full ${hasValue ? 'bg-emerald-500' : `bg-[${color}]`}`} style={hasValue ? {} : { backgroundColor: color }} />
        <span className="text-xs font-bold uppercase tracking-widest">{name}</span>
        {hasValue && <Check size={14} className="text-emerald-500 ml-auto" />}
      </div>
      <div className="px-4 py-2.5 space-y-2">
        {/* Auth mode toggle */}
        <div className="flex rounded-lg bg-[color:var(--surface-2)] p-0.5 w-fit">
          {([
            { id: 'oauth', label: oauthLabel },
            { id: 'api',   label: 'API Key' },
          ] as const).map(m => (
            <button key={m.id} onClick={() => setMode(m.id)}
              className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${mode === m.id ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'}`}>
              {m.label}
            </button>
          ))}
        </div>

        {mode === 'oauth' ? (
          <div className="space-y-2">
            {oauthInputKind === 'json' ? (
              <textarea
                value={oauthToken}
                onChange={e => setOauthToken(e.target.value)}
                placeholder={oauthPlaceholder}
                className="input-field min-h-[132px] py-3 font-mono text-xs resize-y"
              />
            ) : (
              <div className="relative">
                <input type={showToken ? 'text' : 'password'} value={oauthToken} onChange={e => setOauthToken(e.target.value)}
                  placeholder={oauthPlaceholder} className="input-field h-9 pr-10 font-mono text-xs" />
                <button type="button" onClick={() => setShowToken(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                  {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            )}
            {canImportOauth && onImportOauth && (
              <button
                type="button"
                onClick={onImportOauth}
                disabled={importingOauth}
                className="btn-secondary h-9 w-full justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
              >
                {importingOauth ? <Loader2 size={13} className="animate-spin" /> : <KeyRound size={13} />}
                {importedOauth ? 'Codex Token Imported' : 'Import from Codex CLI'}
              </button>
            )}
            <div className="flex items-center justify-between">
              <div className="text-[10px] text-[color:var(--text-muted)]">{oauthHint}</div>
              {oauthInstructions && (
                <button onClick={() => setShowHelp(v => !v)} className="text-[10px] font-bold text-[color:var(--accent-solid)] hover:opacity-70 transition-opacity">
                  {showHelp ? 'Hide help' : 'How to get a token?'}
                </button>
              )}
            </div>
            {showHelp && oauthInstructions}
          </div>
        ) : (
          <div className="space-y-2">
            <div className="relative">
              <input type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)}
                placeholder={apiPlaceholder} className="input-field h-9 pr-10 font-mono text-xs" />
              <button type="button" onClick={() => setShowKey(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <p className="text-[10px] text-[color:var(--text-muted)]">{apiHint}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function LLMStep({
  apiKey, setApiKey, oauthToken, setOauthToken,
  openaiApiKey, setOpenaiApiKey, openaiOauthToken, setOpenaiOauthToken,
  geminiApiKey, setGeminiApiKey, geminiOauthCredentials, setGeminiOauthCredentials,
  codexOauthImportAvailable, openaiOauthImported, importingCodexOauth, onImportCodexOauth,
}: {
  apiKey: string; setApiKey: (v: string) => void;
  oauthToken: string; setOauthToken: (v: string) => void;
  openaiApiKey: string; setOpenaiApiKey: (v: string) => void;
  openaiOauthToken: string; setOpenaiOauthToken: (v: string) => void;
  geminiApiKey: string; setGeminiApiKey: (v: string) => void;
  geminiOauthCredentials: string; setGeminiOauthCredentials: (v: string) => void;
  codexOauthImportAvailable: boolean;
  openaiOauthImported: boolean;
  importingCodexOauth: boolean;
  onImportCodexOauth: () => void;
}) {
  const [copiedAnthropic, setCopiedAnthropic] = useState(false);
  const [copiedOpenai, setCopiedOpenai] = useState(false);
  const [copiedGemini, setCopiedGemini] = useState(false);

  function copyAnthropicCmd() {
    navigator.clipboard.writeText('claude setup-token');
    setCopiedAnthropic(true);
    setTimeout(() => setCopiedAnthropic(false), 2000);
  }

  function copyOpenaiCmd() {
    navigator.clipboard.writeText('codex login');
    setCopiedOpenai(true);
    setTimeout(() => setCopiedOpenai(false), 2000);
  }

  function copyGeminiCmd() {
    navigator.clipboard.writeText('gemini');
    setCopiedGemini(true);
    setTimeout(() => setCopiedGemini(false), 2000);
  }

  const anthropicOauthInstructions = (
    <div className="rounded-lg bg-[color:var(--surface-2)] divide-y divide-[color:var(--border-subtle)] mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black text-[color:var(--accent-solid)]">1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-[color:var(--app-bg)] px-2 py-1 font-mono text-[11px] text-[color:var(--text-primary)] border border-[color:var(--border)]">
          <span className="flex-1">claude setup-token</span>
          <button onClick={copyAnthropicCmd} className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] hover:opacity-70 transition-opacity shrink-0">
            {copiedAnthropic ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black text-[color:var(--accent-solid)]">2.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Log in, authorize, paste the printed token above.</p>
      </div>
    </div>
  );

  const openaiOauthInstructions = (
    <div className="rounded-lg bg-[color:var(--surface-2)] divide-y divide-[color:var(--border-subtle)] mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-[color:var(--app-bg)] px-2 py-1 font-mono text-[11px] text-[color:var(--text-primary)] border border-[color:var(--border)]">
          <span className="flex-1">codex login</span>
          <button onClick={copyOpenaiCmd} className="text-[9px] font-bold uppercase tracking-widest hover:opacity-70 transition-opacity shrink-0" style={{ color: '#10A37F' }}>
            {copiedOpenai ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>2.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Choose <span className="font-mono text-[color:var(--text-primary)]">Sign in with ChatGPT</span> and complete the browser flow.</p>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>3.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Copy token from <span className="font-mono text-[color:var(--text-primary)]">~/.codex/auth.json</span>, paste above.</p>
      </div>
    </div>
  );

  const geminiOauthInstructions = (
    <div className="rounded-lg bg-[color:var(--surface-2)] divide-y divide-[color:var(--border-subtle)] mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-[color:var(--app-bg)] px-2 py-1 font-mono text-[11px] text-[color:var(--text-primary)] border border-[color:var(--border)]">
          <span className="flex-1">gemini</span>
          <button onClick={copyGeminiCmd} className="text-[9px] font-bold uppercase tracking-widest hover:opacity-70 transition-opacity shrink-0" style={{ color: '#4285F4' }}>
            {copiedGemini ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>2.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Choose <span className="font-mono text-[color:var(--text-primary)]">Sign in with Google</span> and complete the browser flow.</p>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>3.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Copy the full JSON from <span className="font-mono text-[color:var(--text-primary)]">~/.gemini/oauth_creds.json</span>. Sentinel uses the same Code Assist credentials, so it must include <span className="font-mono text-[color:var(--text-primary)]">refresh_token</span>.</p>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-[color:var(--text-primary)]">Connect Providers</h2>
        <p className="text-sm text-[color:var(--text-muted)] mt-1">
          Configure one or multiple. When multiple are set, the first is primary and the others are fallbacks.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <ProviderCard
          name="Anthropic"
          color="#D97706"
          apiKey={apiKey} setApiKey={setApiKey}
          oauthToken={oauthToken} setOauthToken={setOauthToken}
          apiPlaceholder="sk-ant-api03-..."
          oauthPlaceholder="sk-ant-oat01-..."
          apiHint="Get your key at console.anthropic.com"
          oauthHint={<>Starts with <span className="font-mono text-[color:var(--text-primary)]">sk-ant-oat01-</span></>}
          oauthInstructions={anthropicOauthInstructions}
        />
        <ProviderCard
          name="OpenAI"
          color="#10A37F"
          apiKey={openaiApiKey} setApiKey={setOpenaiApiKey}
          oauthToken={openaiOauthToken} setOauthToken={setOpenaiOauthToken}
          apiPlaceholder="sk-..."
          oauthPlaceholder="Paste Codex OAuth token..."
          apiHint="Get your key at platform.openai.com/api-keys"
          oauthHint={<>Starts with <span className="font-mono text-[color:var(--text-primary)]">eyJhbG...</span></>}
          oauthInstructions={openaiOauthInstructions}
          canImportOauth={codexOauthImportAvailable}
          importedOauth={openaiOauthImported}
          importingOauth={importingCodexOauth}
          onImportOauth={onImportCodexOauth}
        />
        <ProviderCard
          name="Google Gemini"
          color="#4285F4"
          apiKey={geminiApiKey} setApiKey={setGeminiApiKey}
          oauthToken={geminiOauthCredentials} setOauthToken={setGeminiOauthCredentials}
          apiPlaceholder="AIza..."
          oauthPlaceholder='{"refresh_token":"...","access_token":"..."}'
          apiHint="Get your key at aistudio.google.com/apikey"
          oauthHint={<>Paste the full JSON from <span className="font-mono text-[color:var(--text-primary)]">~/.gemini/oauth_creds.json</span></>}
          oauthInstructions={geminiOauthInstructions}
          oauthInputKind="json"
          defaultMode="api"
        />
      </div>

      <div className="rounded-lg bg-[color:var(--surface-2)] px-4 py-3 text-[11px] text-[color:var(--text-muted)]">
        <span className="font-bold text-[color:var(--text-primary)]">Already configured? </span>
        If keys were set via environment variables, you can skip this step.
      </div>
    </div>
  );
}

function AgentStep({ name, setName, role, setRole, personality, setPersonality }: {
  name: string; setName: (v: string) => void;
  role: string; setRole: (v: string) => void;
  personality: string; setPersonality: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-[color:var(--text-primary)]">Your Agent</h2>
        <p className="text-sm text-[color:var(--text-muted)] mt-1">Define who your agent is. This becomes a pinned core memory.</p>
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Agent Name</label>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Sentinel, Aria, Max..."
          className="input-field h-11" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Purpose & Role</label>
        <textarea value={role} onChange={e => setRole(e.target.value)}
          placeholder="e.g. You are a senior software engineering assistant specialised in backend systems and infrastructure. You help architect, build, and debug complex distributed systems."
          className="input-field min-h-[100px] py-3 resize-none text-sm leading-relaxed" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
          Personality <span className="text-[color:var(--text-muted)] normal-case font-normal">(optional)</span>
        </label>
        <textarea value={personality} onChange={e => setPersonality(e.target.value)}
          placeholder="e.g. Direct and concise. Calls out bad ideas early. Prefers simple solutions. Always asks for context before diving in."
          className="input-field min-h-[80px] py-3 resize-none text-sm leading-relaxed" />
      </div>
    </div>
  );
}

function UserStep({ userName, setUserName, userContext, setUserContext }: {
  userName: string; setUserName: (v: string) => void;
  userContext: string; setUserContext: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-[color:var(--text-primary)]">About You</h2>
        <p className="text-sm text-[color:var(--text-muted)] mt-1">Give your agent context about who you are. This becomes a pinned core memory.</p>
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Your Name</label>
        <input value={userName} onChange={e => setUserName(e.target.value)} placeholder="e.g. John Smith"
          className="input-field h-11" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Your Context</label>
        <textarea value={userContext} onChange={e => setUserContext(e.target.value)}
          placeholder="e.g. I'm a senior engineer building a multi-agent AI platform called ARAIS. I work across backend (Python/FastAPI), frontend (React/TypeScript), and infrastructure (GCP/K8s). I prefer direct, technical answers and dislike over-engineering."
          className="input-field min-h-[120px] py-3 resize-none text-sm leading-relaxed" />
      </div>
    </div>
  );
}

function DoneStep({ firstMessage, setFirstMessage, isCompleting, completedItems, promptOptions }: {
  firstMessage: string; setFirstMessage: (v: string) => void;
  isCompleting: boolean; completedItems: string[];
  promptOptions: StarterPromptOption[];
}) {
  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-[color:var(--text-primary)]">
          {isCompleting ? 'Setting up your workspace…' : 'Ready to launch'}
        </h2>
        <p className="text-sm text-[color:var(--text-muted)] mt-1">
          {isCompleting ? 'Creating root memories and persisting your configuration.' : 'Your workspace has been configured. What would you like to create first?'}
        </p>
      </div>

      {(isCompleting || completedItems.length > 0) && (
        <div className="space-y-2">
          {completedItems.map(item => (
            <div key={item} className="flex items-center gap-2 text-sm text-emerald-500">
              <Check size={14} className="shrink-0" />
              <span>{item}</span>
            </div>
          ))}
          {isCompleting && (
            <div className="flex items-center gap-2 text-sm text-[color:var(--text-muted)]">
              <Loader2 size={14} className="animate-spin shrink-0" />
              <span>Working…</span>
            </div>
          )}
        </div>
      )}

      {!isCompleting && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {promptOptions.map((option) => (
              <button
                key={option.label}
                type="button"
                onClick={() => setFirstMessage(option.prompt)}
                className="px-2.5 py-1.5 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] transition-colors"
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">First task for your agent</label>
          <textarea
            value={firstMessage}
            onChange={e => setFirstMessage(e.target.value)}
            placeholder="e.g. Map my top priorities and launch the safest first automation."
            className="input-field min-h-[100px] py-3 resize-none text-sm leading-relaxed"
            autoFocus
          />
          <p className="text-[10px] text-[color:var(--text-muted)]">This will be your first message when the workspace opens. You can leave it blank.</p>
        </div>
      )}
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function OnboardingPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [step, setStep] = useState(0);

  // LLM keys — Anthropic
  const [apiKey, setApiKey] = useState('');
  const [oauthToken, setOauthToken] = useState('');
  // LLM keys — OpenAI
  const [openaiApiKey, setOpenaiApiKey] = useState('');
  const [openaiOauthToken, setOpenaiOauthToken] = useState('');
  const [codexOauthImportAvailable, setCodexOauthImportAvailable] = useState(false);
  const [openaiOauthImported, setOpenaiOauthImported] = useState(false);
  const [importingCodexOauth, setImportingCodexOauth] = useState(false);
  // LLM keys — Gemini
  const [geminiApiKey, setGeminiApiKey] = useState('');
  const [geminiOauthCredentials, setGeminiOauthCredentials] = useState('');
  const [runtimeTargets, setRuntimeTargets] = useState<RuntimeSSHTarget[]>([]);
  const [selectedRuntimeTargetId, setSelectedRuntimeTargetId] = useState('');
  const [runtimeForm, setRuntimeForm] = useState(emptyRuntimeTargetForm);
  const [testingRuntimeTarget, setTestingRuntimeTarget] = useState(false);
  const [savingRuntimeTarget, setSavingRuntimeTarget] = useState(false);
  const [runtimeFormOpen, setRuntimeFormOpen] = useState(false);
  const [runtimeVerified, setRuntimeVerified] = useState(false);
  const [runtimeResolvedHome, setRuntimeResolvedHome] = useState<string | null>(null);

  function updateRuntimeForm(updates: Partial<typeof emptyRuntimeTargetForm>) {
    setRuntimeForm((f) => ({ ...f, ...updates }));
    const touchesVerification = (Object.keys(updates) as Array<keyof typeof emptyRuntimeTargetForm>)
      .some((k) => RUNTIME_VERIFICATION_FIELDS.has(k));
    if (touchesVerification) {
      setRuntimeVerified(false);
      setRuntimeResolvedHome(null);
    }
  }

  // Agent identity
  const [agentName, setAgentName] = useState('');
  const [agentRole, setAgentRole] = useState('');
  const [agentPersonality, setAgentPersonality] = useState('');

  // User profile
  const [userName, setUserName] = useState('');
  const [userContext, setUserContext] = useState('');

  // Done step
  const [firstMessage, setFirstMessage] = useState(
    STARTER_PROMPT_OPTIONS[0].prompt
  );
  const [isCompleting, setIsCompleting] = useState(false);
  const [completedItems, setCompletedItems] = useState<string[]>([]);

  const isLastStep = step === STEPS.length - 1;
  const instanceName = location.pathname.match(/^\/instances\/([^/]+)/)?.[1]
    ? decodeURIComponent(location.pathname.match(/^\/instances\/([^/]+)/)?.[1] || '')
    : null;

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
    if (!instanceName) return;
    let cancelled = false;
    Promise.all([
      api.get<RuntimeSSHTarget[]>('/runtime-targets'),
      api.get<SentinelInstance>(`/instances/${encodeURIComponent(instanceName)}`),
    ])
      .then(([targets, instance]) => {
        if (cancelled) return;
        setRuntimeTargets(targets);
        setSelectedRuntimeTargetId(instance.runtime_target_id ?? '');
      })
      .catch(() => {
        if (!cancelled) setRuntimeTargets([]);
      });
    return () => {
      cancelled = true;
    };
  }, [instanceName]);

  async function handleImportCodexOauth() {
    setImportingCodexOauth(true);
    try {
      await api.post('/settings/desktop-codex-oauth/import');
      setOpenaiOauthImported(true);
      toast.success('Codex OAuth token imported');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to import Codex OAuth token');
    } finally {
      setImportingCodexOauth(false);
    }
  }

  function runtimeTargetBody() {
    return {
      name: runtimeForm.name.trim(),
      host: runtimeForm.host.trim(),
      port: Number(runtimeForm.port || 22),
      username: runtimeForm.username.trim(),
      workspaces_dir: runtimeForm.workspaces_dir.trim(),
      auth_type: runtimeForm.auth_type,
      private_key: runtimeForm.auth_type === 'private_key' ? runtimeForm.private_key : undefined,
      password: runtimeForm.auth_type === 'password' ? runtimeForm.password : undefined,
    };
  }

  function validateRuntimeTargetBody(body: ReturnType<typeof runtimeTargetBody>, opts: { requireWorkspaceDir: boolean; requireName: boolean }) {
    if (!body.host || !body.username) {
      toast.error('Host and username are required');
      return false;
    }
    if (opts.requireName && !body.name) {
      toast.error('Name is required');
      return false;
    }
    if (opts.requireWorkspaceDir && !body.workspaces_dir) {
      toast.error('Workspace root is required');
      return false;
    }
    if (body.auth_type === 'private_key' && !runtimeForm.private_key.trim()) {
      toast.error('Private key is required');
      return false;
    }
    if (body.auth_type === 'password' && !runtimeForm.password) {
      toast.error('SSH password is required');
      return false;
    }
    return true;
  }

  async function handleTestRuntimeTarget() {
    const body = runtimeTargetBody();
    if (!validateRuntimeTargetBody(body, { requireWorkspaceDir: false, requireName: false })) return;
    setTestingRuntimeTarget(true);
    try {
      const result = await api.post<RuntimeSSHTargetTestResponse>('/runtime-targets/test', body, { timeoutMs: 20_000 });
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
      toast.error(error instanceof Error ? error.message : 'Runtime target test failed');
    } finally {
      setTestingRuntimeTarget(false);
    }
  }

  async function handleCreateRuntimeTarget() {
    const body = runtimeTargetBody();
    if (!validateRuntimeTargetBody(body, { requireWorkspaceDir: true, requireName: true })) return;
    setSavingRuntimeTarget(true);
    try {
      const target = await api.post<RuntimeSSHTarget>('/runtime-targets', body);
      setRuntimeTargets((items) => [...items.filter((item) => item.id !== target.id), target]);
      setSelectedRuntimeTargetId(target.id);
      setRuntimeForm(emptyRuntimeTargetForm);
      setRuntimeFormOpen(false);
      setRuntimeVerified(false);
      setRuntimeResolvedHome(null);
      toast.success('Runtime target saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save runtime target');
    } finally {
      setSavingRuntimeTarget(false);
    }
  }

  function canProceed(): boolean {
    return true; // all steps are optional content-wise
  }

  async function handleFinish() {
    setIsCompleting(true);
    const items: string[] = [];

    try {
      const identity = resolveAgentIdentity(agentName, agentRole, agentPersonality);
      const userProfile = resolveUserProfile(userName, userContext);

      // 1. Save API keys
      const hasAnthropic = !!(apiKey || oauthToken);
      const hasOpenai = !!(openaiApiKey || openaiOauthToken || openaiOauthImported);
      const hasGemini = !!(geminiApiKey || geminiOauthCredentials);
      if (hasAnthropic || hasOpenai || hasGemini) {
        await api.post('/settings/api-keys', {
          anthropic_api_key: apiKey || undefined,
          anthropic_oauth_token: oauthToken || undefined,
          openai_api_key: openaiApiKey || undefined,
          openai_oauth_token: openaiOauthToken || undefined,
          gemini_api_key: geminiApiKey || undefined,
          gemini_oauth_credentials: geminiOauthCredentials || undefined,
        });
        const saved: string[] = [];
        if (hasAnthropic) saved.push('Anthropic');
        if (hasOpenai) saved.push('OpenAI');
        if (hasGemini) saved.push('Gemini');
        items.push(`${saved.join(' + ')} provider${saved.length > 1 ? 's' : ''} saved`);
        setCompletedItems([...items]);
      }

      if (instanceName && selectedRuntimeTargetId) {
        await api.patch(`/instances/${encodeURIComponent(instanceName)}/runtime-target`, {
          runtime_target_id: selectedRuntimeTargetId,
        });
        items.push('Runtime target selected');
        setCompletedItems([...items]);
      }

      // 2. Agent identity memory
      await api.post('/memory', {
        content: buildAgentIdentityMemoryContent(identity),
        title: 'Agent Identity',
        category: 'core',
        importance: 100,
        pinned: true,
      });
      items.push('Agent identity memory created');
      setCompletedItems([...items]);

      // 3. User profile memory
      await api.post('/memory', {
        content: buildUserProfileMemoryContent(userProfile),
        title: 'User Profile',
        category: 'core',
        importance: 90,
        pinned: true,
      });
      items.push('User profile memory created');
      setCompletedItems([...items]);

      // 4. Mark onboarding complete; backend composes and persists system prompt
      await api.post('/onboarding/complete', {
        agent_name: identity.rawName || undefined,
        agent_role: identity.rawRole || undefined,
        agent_personality: identity.rawPersonality || undefined,
      });
      items.push('Workspace ready');
      setCompletedItems([...items]);

      await new Promise(r => setTimeout(r, 600)); // brief pause so user sees the checkmarks

      localStorage.setItem('sentinel-mode', 'advanced');
      navigate(instanceRouteFromPath(location.pathname, 'sessions'), { state: { firstMessage: firstMessage.trim() || undefined } });
    } catch (err) {
      toast.error('Setup failed — please try again');
      setIsCompleting(false);
    }
  }

  const progress = (step / (STEPS.length - 1)) * 100;

  return (
    <div className="h-screen w-full overflow-hidden flex flex-col bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
      {/* Top progress bar */}
      <div className="h-0.5 w-full bg-[color:var(--surface-2)]">
        <div className="h-full bg-[color:var(--accent-solid)] transition-all duration-500"
          style={{ width: `${progress}%` }} />
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="hidden md:flex flex-col justify-center px-8 py-12 border-r border-[color:var(--border-subtle)] w-64 shrink-0">
          <div className="flex items-center gap-2 mb-10">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]">
              <Zap size={16} fill="currentColor" />
            </div>
            <span className="text-sm font-black uppercase tracking-widest">Sentinel</span>
          </div>
          <StepIndicator current={step} />
        </aside>

        {/* Content */}
        <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-4 py-5 pb-28 sm:px-6 sm:py-6 md:px-12 md:py-10 md:pb-8">
            <div className="w-full max-w-xl mx-auto flex flex-col gap-4 animate-in fade-in duration-300" key={step}>
              {step === 0 && <WelcomeStep />}
              {step === 1 && <LLMStep apiKey={apiKey} setApiKey={setApiKey} oauthToken={oauthToken} setOauthToken={setOauthToken} openaiApiKey={openaiApiKey} setOpenaiApiKey={setOpenaiApiKey} openaiOauthToken={openaiOauthToken} setOpenaiOauthToken={setOpenaiOauthToken} geminiApiKey={geminiApiKey} setGeminiApiKey={setGeminiApiKey} geminiOauthCredentials={geminiOauthCredentials} setGeminiOauthCredentials={setGeminiOauthCredentials} codexOauthImportAvailable={codexOauthImportAvailable} openaiOauthImported={openaiOauthImported} importingCodexOauth={importingCodexOauth} onImportCodexOauth={handleImportCodexOauth} />}
              {step === 2 && (
                <RuntimeStep
                  targets={runtimeTargets}
                  selectedTargetId={selectedRuntimeTargetId}
                  onSelect={setSelectedRuntimeTargetId}
                  form={runtimeForm}
                  updateForm={updateRuntimeForm}
                  onTest={() => void handleTestRuntimeTarget()}
                  onCreate={() => void handleCreateRuntimeTarget()}
                  testing={testingRuntimeTarget}
                  saving={savingRuntimeTarget}
                  formOpen={runtimeFormOpen}
                  setFormOpen={setRuntimeFormOpen}
                  verified={runtimeVerified}
                  resolvedHome={runtimeResolvedHome}
                />
              )}
              {step === 3 && <AgentStep name={agentName} setName={setAgentName} role={agentRole} setRole={setAgentRole} personality={agentPersonality} setPersonality={setAgentPersonality} />}
              {step === 4 && <UserStep userName={userName} setUserName={setUserName} userContext={userContext} setUserContext={setUserContext} />}
              {step === 5 && (
                <DoneStep
                  firstMessage={firstMessage}
                  setFirstMessage={setFirstMessage}
                  isCompleting={isCompleting}
                  completedItems={completedItems}
                  promptOptions={STARTER_PROMPT_OPTIONS}
                />
              )}
            </div>
          </div>

          {/* Bottom nav */}
          <div className="sticky bottom-0 z-10 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/95 backdrop-blur px-4 py-3 sm:px-6 md:px-8 md:py-4 flex items-center justify-between gap-3">
            <button
              onClick={() => setStep(s => Math.max(0, s - 1))}
              disabled={step === 0 || isCompleting}
              className="btn-secondary h-10 px-5 gap-2 text-sm whitespace-nowrap disabled:opacity-30"
            >
              <ArrowLeft size={16} /> Back
            </button>

            <div className="hidden sm:flex items-center gap-2">
              {STEPS.map((_, i) => (
                <div key={i} className={`h-1.5 rounded-full transition-all duration-300 ${i === step ? 'w-6 bg-[color:var(--accent-solid)]' : i < step ? 'w-1.5 bg-emerald-500' : 'w-1.5 bg-[color:var(--surface-2)]'}`} />
              ))}
            </div>

            {isLastStep ? (
              <button
                onClick={handleFinish}
                disabled={isCompleting}
                className="btn-primary h-10 px-6 gap-2 text-sm whitespace-nowrap"
              >
                {isCompleting ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                {isCompleting ? 'Setting up…' : 'Launch Sentinel'}
              </button>
            ) : (
              <button
                onClick={() => setStep(s => s + 1)}
                disabled={!canProceed()}
                className="btn-primary h-10 px-6 gap-2 text-sm whitespace-nowrap"
              >
                {STEPS[step].id === 'welcome' ? 'Get Started' : 'Continue'}
                <ArrowRight size={16} />
              </button>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
