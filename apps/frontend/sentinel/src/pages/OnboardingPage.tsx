import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { notificationPublisher } from '../lib/notifications';
import {
  ArrowRight, ArrowLeft, Check, Bot, User, Flag,
  Eye, EyeOff, Loader2, KeyRound, Server, FolderOpen,
} from 'lucide-react';
import { useProductTourStore } from '../store/product-tour-store';
import { Logo } from '../components/ui/Logo';
import { MachinesStep, WorkspacesStep } from '../components/onboarding/EnvironmentSteps';
import { OllamaSetupCard } from '../components/onboarding/OllamaSetupCard';
import { api } from '../lib/api';
import {
  resolveAgentIdentity,
} from '../lib/onboarding-defaults';
import { instanceRouteFromPath } from '../lib/routes';
import '../components/onboarding/setup.css';

const notify = notificationPublisher('Setup');

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

const STEPS: StepMeta[] = [
  { id: 'welcome',  label: 'Welcome',       icon: <Logo size={14} /> },
  { id: 'llm',      label: 'Model access',     icon: <Bot size={14} /> },
  { id: 'machines', label: 'Machines', icon: <Server size={14} />, optional: true },
  { id: 'workspaces', label: 'Workspaces', icon: <FolderOpen size={14} />, optional: true },
  { id: 'agent',    label: 'Agent identity',    icon: <Bot size={14} /> },
  { id: 'user',     label: 'Your context',     icon: <User size={14} /> },
  { id: 'done',     label: 'Ready',        icon: <Flag size={14} /> },
];

const STARTER_PROMPT_OPTIONS: StarterPromptOption[] = [
  {
    label: 'Priority Plan',
    prompt: 'Map my top priorities for this instance, propose the first 3 high-impact automations, and execute the safest one now.',
  },
  {
    label: 'Trigger Setup',
    prompt: 'Design and create a trigger strategy for this instance: one daily summary trigger, one failure-alert trigger, and one webhook trigger.',
  },
  {
    label: 'Memory Audit',
    prompt: 'Audit my current memory structure, propose a cleaner hierarchy with root categories, and apply the highest-value memory improvements.',
  },
];

// ── sub-components ───────────────────────────────────────────────────────────

function StepIndicator({ current }: { current: number }) {
  return (
    <nav className="setup-steps" aria-label="Setup progress">
      {STEPS.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={step.id} className="setup-step" aria-current={active ? 'step' : undefined} data-done={done || undefined}>
            <div className="setup-step-number">
              {done ? <Check size={12} /> : i + 1}
            </div>
            <div className="flex flex-col min-w-0">
              <span className="setup-step-label">
                {step.label}
              </span>
              {step.optional && (
                <span className="text-[9px] text-(--text-muted) font-medium uppercase tracking-wider">Optional</span>
              )}
            </div>
          </div>
        );
      })}
    </nav>
  );
}

const onboardingInputClass = 'h-10 rounded-lg border border-(--border-subtle) bg-(--surface-0) px-3 text-xs font-medium text-(--text-primary) placeholder:text-(--text-muted) outline-hidden focus:border-(--accent-solid)';

function WelcomeStep() {
  return (
    <div className="setup-welcome">
      <span className="setup-eyebrow">Make it yours</span>
      <h1>A workspace for<br />what comes next.</h1>
      <p>Connect a model provider, choose where tools run, and give Sentinel the context to work with you. You can revisit providers in Settings, environments in Workspaces, and saved context in Memory.</p>
      <div className="setup-introduction">
        {[
          { icon: <KeyRound size={18} />, label: 'Connect your model', desc: 'Use your provider credentials or an available account connection.' },
          { icon: <Server size={18} />, label: 'Choose an environment', desc: 'Add a machine and workspace now, or return when you have a project.' },
          { icon: <User size={18} />, label: 'Add the useful context', desc: 'Your agent’s role, your preferences, and what matters to your work.' },
        ].map(item => (
          <div key={item.label}>
            <span>{item.icon}</span><div><strong>{item.label}</strong><p>{item.desc}</p></div>
          </div>
        ))}
      </div>
      <p className="setup-guide-note">After setup, the interactive field guide helps you practice Sentinel’s controls and shortcuts.</p>
    </div>
  );
}

function ProviderCard({
  name, color, apiKey, setApiKey, oauthToken, setOauthToken,
  apiPlaceholder, oauthPlaceholder, apiHint, oauthInstructions,
  oauthHint, oauthInputKind = 'token', defaultMode = 'oauth',
  canSyncOauth = false, oauthSynced = false, syncingOauth = false, onSyncOauth,
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
  canSyncOauth?: boolean;
  oauthSynced?: boolean;
  syncingOauth?: boolean;
  onSyncOauth?: () => void;
}) {
  const [showKey, setShowKey] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [mode, setMode] = useState<'oauth' | 'api'>(defaultMode);
  const [showHelp, setShowHelp] = useState(false);
  const hasValue = !!(apiKey || oauthToken || oauthSynced);
  const oauthLabel = oauthInputKind === 'json' ? 'OAuth Credentials' : 'OAuth Token';

  return (
    <div className="setup-provider" data-filled={hasValue || undefined}>
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-(--border-subtle)">
        <div className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
        <span className="text-xs font-medium tracking-wide">{name}</span>
        {hasValue && <span className="setup-credential-state">{oauthSynced ? 'Auto-sync' : 'Added'}</span>}
      </div>
      <div className="px-4 py-2.5 space-y-2">
        {/* Auth mode toggle */}
        <div className="setup-auth-modes">
          {([
            { id: 'oauth', label: oauthLabel },
            { id: 'api',   label: 'API Key' },
          ] as const).map(m => (
            <button key={m.id} onClick={() => setMode(m.id)} aria-pressed={mode === m.id}>
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
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary)">
                  {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            )}
            {canSyncOauth && onSyncOauth && (
              <button
                type="button"
                onClick={onSyncOauth}
                disabled={syncingOauth}
                className="btn-secondary h-9 w-full justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
              >
                {syncingOauth ? <Loader2 size={13} className="animate-spin" /> : <KeyRound size={13} />}
                {oauthSynced ? `${name === 'Anthropic' ? 'Claude' : name === 'Google Gemini' ? 'Antigravity' : 'Codex'} Auto-sync Enabled` : `Use ${name === 'Anthropic' ? 'Claude' : name === 'Google Gemini' ? 'Antigravity' : 'Codex'} CLI Automatically`}
              </button>
            )}
            <div className="flex items-center justify-between">
              <div className="text-[10px] text-(--text-muted)">{oauthHint}</div>
              {oauthInstructions && (
                <button onClick={() => setShowHelp(v => !v)} className="text-[10px] font-bold text-(--accent-solid) hover:opacity-70 transition-opacity">
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
                className="absolute right-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary)">
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <p className="text-[10px] text-(--text-muted)">{apiHint}</p>
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
  claudeOauthImported, importingClaudeOauth, onImportClaudeOauth,
  geminiOauthImported, importingGeminiOauth, onImportGeminiOauth,
}: {
  apiKey: string; setApiKey: (v: string) => void;
  oauthToken: string; setOauthToken: (v: string) => void;
  openaiApiKey: string; setOpenaiApiKey: (v: string) => void;
  openaiOauthToken: string; setOpenaiOauthToken: (v: string) => void;
  geminiApiKey: string; setGeminiApiKey: (v: string) => void;
  geminiOauthCredentials: string; setGeminiOauthCredentials: (v: string) => void;
  geminiOauthImported: boolean;
  importingGeminiOauth: boolean;
  onImportGeminiOauth: () => void;
  claudeOauthImported: boolean;
  importingClaudeOauth: boolean;
  onImportClaudeOauth: () => void;
  codexOauthImportAvailable: boolean;
  openaiOauthImported: boolean;
  importingCodexOauth: boolean;
  onImportCodexOauth: () => void;
}) {
  const [copiedAnthropic, setCopiedAnthropic] = useState(false);
  const [copiedOpenai, setCopiedOpenai] = useState(false);
  const [copiedGemini, setCopiedGemini] = useState(false);

  function copyAnthropicCmd() {
    navigator.clipboard.writeText('claude auth login');
    setCopiedAnthropic(true);
    setTimeout(() => setCopiedAnthropic(false), 2000);
  }

  function copyOpenaiCmd() {
    navigator.clipboard.writeText('codex login');
    setCopiedOpenai(true);
    setTimeout(() => setCopiedOpenai(false), 2000);
  }

  function copyGeminiCmd() {
    navigator.clipboard.writeText('agy');
    setCopiedGemini(true);
    setTimeout(() => setCopiedGemini(false), 2000);
  }

  const anthropicOauthInstructions = (
    <div className="rounded-lg bg-(--surface-2) divide-y divide-(--border-subtle) mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black text-(--accent-solid)">1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-(--app-bg) px-2 py-1 font-mono text-[11px] text-(--text-primary) border border-(--border)">
          <span className="flex-1">claude auth login</span>
          <button onClick={copyAnthropicCmd} className="text-[9px] font-bold uppercase tracking-widest text-(--accent-solid) hover:opacity-70 transition-opacity shrink-0">
            {copiedAnthropic ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black text-(--accent-solid)">2.</span>
        <p className="text-[10px] text-(--text-muted)">Sign in, then enable CLI auto-sync above. You can also paste an OAuth token manually.</p>
      </div>
    </div>
  );

  const openaiOauthInstructions = (
    <div className="rounded-lg bg-(--surface-2) divide-y divide-(--border-subtle) mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-(--app-bg) px-2 py-1 font-mono text-[11px] text-(--text-primary) border border-(--border)">
          <span className="flex-1">codex login</span>
          <button onClick={copyOpenaiCmd} className="text-[9px] font-bold uppercase tracking-widest hover:opacity-70 transition-opacity shrink-0" style={{ color: '#10A37F' }}>
            {copiedOpenai ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>2.</span>
        <p className="text-[10px] text-(--text-muted)">Choose <span className="font-mono text-(--text-primary)">Sign in with ChatGPT</span> and complete the browser flow.</p>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>3.</span>
        <p className="text-[10px] text-(--text-muted)">Enable CLI auto-sync above, or paste a token manually.</p>
      </div>
    </div>
  );

  const geminiOauthInstructions = (
    <div className="rounded-lg bg-(--surface-2) divide-y divide-(--border-subtle) mt-1">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>1.</span>
        <div className="flex items-center gap-2 flex-1 rounded-md bg-(--app-bg) px-2 py-1 font-mono text-[11px] text-(--text-primary) border border-(--border)">
          <span className="flex-1">agy</span>
          <button onClick={copyGeminiCmd} className="text-[9px] font-bold uppercase tracking-widest hover:opacity-70 transition-opacity shrink-0" style={{ color: '#4285F4' }}>
            {copiedGemini ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>2.</span>
        <p className="text-[10px] text-(--text-muted)">Choose <span className="font-mono text-(--text-primary)">Sign in with Google</span> and complete the browser flow.</p>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#4285F4' }}>3.</span>
        <p className="text-[10px] text-(--text-muted)">On macOS, enable CLI auto-sync above. You can also paste an exported Antigravity OAuth credential bundle.</p>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-(--text-primary)">Connect a model provider.</h2>
        <p className="text-sm text-(--text-muted) mt-1">
          Use API keys, an account connection, or a local or remote Ollama server. You can change providers later in Settings → LLM Providers.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <ProviderCard
          name="Anthropic"
          canSyncOauth
          oauthSynced={claudeOauthImported}
          syncingOauth={importingClaudeOauth}
          onSyncOauth={onImportClaudeOauth}
          color="#D97706"
          apiKey={apiKey} setApiKey={setApiKey}
          oauthToken={oauthToken} setOauthToken={setOauthToken}
          apiPlaceholder="sk-ant-api03-..."
          oauthPlaceholder="sk-ant-oat01-..."
          apiHint="Get your key at console.anthropic.com"
          oauthHint={<>Starts with <span className="font-mono text-(--text-primary)">sk-ant-oat01-</span></>}
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
          oauthHint={<>Starts with <span className="font-mono text-(--text-primary)">eyJhbG...</span></>}
          oauthInstructions={openaiOauthInstructions}
          canSyncOauth={codexOauthImportAvailable}
          oauthSynced={openaiOauthImported}
          syncingOauth={importingCodexOauth}
          onSyncOauth={onImportCodexOauth}
        />
        <OllamaSetupCard />
        <ProviderCard
          name="Google Gemini"
          canSyncOauth
          oauthSynced={geminiOauthImported}
          syncingOauth={importingGeminiOauth}
          onSyncOauth={onImportGeminiOauth}
          color="#4285F4"
          apiKey={geminiApiKey} setApiKey={setGeminiApiKey}
          oauthToken={geminiOauthCredentials} setOauthToken={setGeminiOauthCredentials}
          apiPlaceholder="AIza..."
          oauthPlaceholder='{"refresh_token":"...","access_token":"..."}'
          apiHint="Get your key at aistudio.google.com/apikey"
          oauthHint={<>Connect your Antigravity Google login.</>}
          oauthInstructions={geminiOauthInstructions}
          oauthInputKind="json"
          defaultMode="oauth"
        />
      </div>

      <div className="rounded-lg bg-(--surface-2) px-4 py-3 text-[11px] text-(--text-muted)">
        <span className="font-bold text-(--text-primary)">Already configured? </span>
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
        <h2 className="text-xl font-black tracking-tight text-(--text-primary)">Your Agent</h2>
        <p className="text-sm text-(--text-muted) mt-1">Define who your agent is. This becomes a pinned core memory.</p>
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Agent Name</label>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Sentinel, Aria, Max..."
          className="input-field h-11" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Purpose & Role</label>
        <textarea value={role} onChange={e => setRole(e.target.value)}
          placeholder="e.g. You are a senior software engineering assistant specialised in backend systems and infrastructure. You help architect, build, and debug complex distributed systems."
          className="input-field min-h-[100px] py-3 resize-none text-sm leading-relaxed" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
          Personality <span className="text-(--text-muted) normal-case font-normal">(optional)</span>
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
        <h2 className="text-xl font-black tracking-tight text-(--text-primary)">About You</h2>
        <p className="text-sm text-(--text-muted) mt-1">Give your agent context about who you are. This becomes a pinned core memory.</p>
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Your Name</label>
        <input value={userName} onChange={e => setUserName(e.target.value)} placeholder="e.g. John Smith"
          className="input-field h-11" />
      </div>
      <div className="space-y-2">
        <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Your Context</label>
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
        <h2 className="text-xl font-black tracking-tight text-(--text-primary)">
          {isCompleting ? 'Setting up your instance…' : 'Ready to launch'}
        </h2>
        <p className="text-sm text-(--text-muted) mt-1">
          {isCompleting ? 'Saving your preferences and preparing your agent’s context.' : 'Your choices are ready. Launch Sentinel to save setup and open your workspace. You can add a first task below, or begin with the field guide.'}
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
            <div className="flex items-center gap-2 text-sm text-(--text-muted)">
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
                className="px-2.5 py-1.5 rounded-md border border-(--border-subtle) bg-(--surface-1) text-[10px] font-bold uppercase tracking-widest text-(--text-muted) hover:text-(--text-primary) hover:border-(--border-strong) transition-colors"
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">First task for your agent</label>
          <textarea
            value={firstMessage}
            onChange={e => setFirstMessage(e.target.value)}
            placeholder="e.g. Map my top priorities and launch the safest first automation."
            className="input-field min-h-[100px] py-3 resize-none text-sm leading-relaxed"
            autoFocus
          />
          <p className="text-[10px] text-(--text-muted)">This will be your first message when the instance opens. You can leave it blank.</p>
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
  const [geminiOauthImported, setGeminiOauthImported] = useState(false);
  const [importingGeminiOauth, setImportingGeminiOauth] = useState(false);
  const [claudeOauthImported, setClaudeOauthImported] = useState(false);
  const [importingClaudeOauth, setImportingClaudeOauth] = useState(false);
  // LLM keys — OpenAI
  const [openaiApiKey, setOpenaiApiKey] = useState('');
  const [openaiOauthToken, setOpenaiOauthToken] = useState('');
  const [codexOauthImportAvailable, setCodexOauthImportAvailable] = useState(false);
  const [openaiOauthImported, setOpenaiOauthImported] = useState(false);
  const [importingCodexOauth, setImportingCodexOauth] = useState(false);
  // LLM keys — Gemini
  const [geminiApiKey, setGeminiApiKey] = useState('');
  const [geminiOauthCredentials, setGeminiOauthCredentials] = useState('');
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

  async function handleImportClaudeOauth() {
    setImportingClaudeOauth(true);
    try {
      await api.post('/settings/desktop-claude-oauth/connect');
      setOauthToken('');
      setClaudeOauthImported(true);
      notify.success('Claude CLI auto-sync enabled');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Claude CLI auto-sync');
    } finally {
      setImportingClaudeOauth(false);
    }
  }

  async function handleImportGeminiOauth() {
    setImportingGeminiOauth(true);
    try {
      await api.post('/settings/desktop-gemini-oauth/connect');
      setGeminiOauthCredentials('');
      setGeminiOauthImported(true);
      notify.success('Antigravity auto-sync enabled');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Antigravity auto-sync');
    } finally {
      setImportingGeminiOauth(false);
    }
  }

  async function handleImportCodexOauth() {
    setImportingCodexOauth(true);
    try {
      await api.post('/settings/desktop-codex-oauth/connect');
      setOpenaiOauthImported(true);
      notify.success('Codex CLI auto-sync enabled');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to enable Codex CLI auto-sync');
    } finally {
      setImportingCodexOauth(false);
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

      // 1. Save API keys
      const hasAnthropic = !!(apiKey || oauthToken || claudeOauthImported);
      const hasOpenai = !!(openaiApiKey || openaiOauthToken || openaiOauthImported);
      const hasGemini = !!(geminiApiKey || geminiOauthCredentials || geminiOauthImported);
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

      // Persist the prompt and protected setup memories as one idempotent operation.
      await api.post('/onboarding/complete', {
        agent_name: identity.rawName || undefined,
        agent_role: identity.rawRole || undefined,
        agent_personality: identity.rawPersonality || undefined,
        user_name: userName.trim() || undefined,
        user_context: userContext.trim() || undefined,
      });
      items.push('Agent identity memory created', 'User profile memory created', 'Instance ready');
      setCompletedItems([...items]);

      await new Promise(r => setTimeout(r, 600)); // brief pause so user sees the checkmarks

      localStorage.setItem('sentinel-mode', 'advanced');
      const instanceName = decodeURIComponent(location.pathname.match(/^\/instances\/([^/]+)/)?.[1] ?? 'default');
      useProductTourStore.getState().open(instanceName);
      navigate(instanceRouteFromPath(location.pathname, 'workspace'), {
        state: { openSessions: true, firstMessage: firstMessage.trim() || undefined },
      });
    } catch (err) {
      notify.error('Setup failed — please try again');
      setIsCompleting(false);
    }
  }

  const progress = (step / (STEPS.length - 1)) * 100;

  return (
    <div className="sentinel-setup h-screen w-full overflow-hidden flex flex-col bg-(--app-bg) text-(--text-primary)">
      {/* Top progress bar */}
      <div className="h-0.5 w-full bg-(--surface-2)">
        <div className="h-full bg-(--accent-solid) transition-all duration-500"
          style={{ width: `${progress}%` }} />
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="setup-sidebar hidden md:flex">
          <div className="flex items-center gap-3 px-3 mb-8">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center text-(--text-primary)">
              <Logo size={28} />
            </div>
            <span className="setup-brand">Sentinel<small>WORKSPACE SETUP</small></span>
          </div>
          <StepIndicator current={step} />
        </aside>

        {/* Content */}
        <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-4 py-5 pb-28 sm:px-6 sm:py-6 md:px-12 md:py-10 md:pb-8">
            <div className="w-full max-w-xl mx-auto flex flex-col gap-4 animate-in fade-in duration-300" key={step}>
              {STEPS[step].id === 'welcome' && <WelcomeStep />}
              {STEPS[step].id === 'llm' && <LLMStep apiKey={apiKey} setApiKey={setApiKey} oauthToken={oauthToken} setOauthToken={setOauthToken} openaiApiKey={openaiApiKey} setOpenaiApiKey={setOpenaiApiKey} openaiOauthToken={openaiOauthToken} setOpenaiOauthToken={setOpenaiOauthToken} geminiApiKey={geminiApiKey} setGeminiApiKey={setGeminiApiKey} geminiOauthCredentials={geminiOauthCredentials} setGeminiOauthCredentials={setGeminiOauthCredentials} codexOauthImportAvailable={codexOauthImportAvailable} openaiOauthImported={openaiOauthImported} importingCodexOauth={importingCodexOauth} onImportCodexOauth={handleImportCodexOauth} claudeOauthImported={claudeOauthImported} importingClaudeOauth={importingClaudeOauth} onImportClaudeOauth={handleImportClaudeOauth} geminiOauthImported={geminiOauthImported} importingGeminiOauth={importingGeminiOauth} onImportGeminiOauth={handleImportGeminiOauth} />}
              {STEPS[step].id === 'machines' && <MachinesStep />}
              {STEPS[step].id === 'workspaces' && <WorkspacesStep onAddMachine={() => setStep(STEPS.findIndex(item => item.id === 'machines'))} />}
              {STEPS[step].id === 'agent' && <AgentStep name={agentName} setName={setAgentName} role={agentRole} setRole={setAgentRole} personality={agentPersonality} setPersonality={setAgentPersonality} />}
              {STEPS[step].id === 'user' && <UserStep userName={userName} setUserName={setUserName} userContext={userContext} setUserContext={setUserContext} />}
              {STEPS[step].id === 'done' && (
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
          <div className="setup-footer">
            <button
              onClick={() => setStep(s => Math.max(0, s - 1))}
              disabled={step === 0 || isCompleting}
              className="btn-secondary h-10 px-5 gap-2 text-sm whitespace-nowrap disabled:opacity-30"
            >
              <ArrowLeft size={16} /> Back
            </button>

            <span className="setup-position">{step + 1} / {STEPS.length}<span>{STEPS[step].label}</span></span>

            {isLastStep ? (
              <button
                onClick={handleFinish}
                disabled={isCompleting}
                className="btn-primary h-10 px-6 gap-2 text-sm whitespace-nowrap"
              >
                {isCompleting ? <Loader2 size={16} className="animate-spin" /> : <Logo size={16} />}
                {isCompleting ? 'Setting up…' : 'Launch Sentinel'}
              </button>
            ) : (
              <button
                onClick={() => setStep(s => s + 1)}
                disabled={!canProceed()}
                className="btn-primary h-10 px-6 gap-2 text-sm whitespace-nowrap"
              >
                {STEPS[step].id === 'welcome' ? 'Set up Sentinel' : 'Continue'}
                <ArrowRight size={16} />
              </button>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
