import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  Zap, ArrowRight, ArrowLeft, Check, Bot, User, Plug, Flag,
  Eye, EyeOff, Loader2,
} from 'lucide-react';
import { api } from '../lib/api';
import {
  buildAgentIdentityMemoryContent,
  buildSystemPrompt,
  buildUserProfileMemoryContent,
  resolveAgentIdentity,
  resolveUserProfile,
} from '../lib/onboarding-defaults';

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

const STEPS: StepMeta[] = [
  { id: 'welcome',  label: 'Welcome',       icon: <Zap size={14} /> },
  { id: 'llm',      label: 'Providers',     icon: <Bot size={14} /> },
  { id: 'agent',    label: 'Your Agent',    icon: <Bot size={14} /> },
  { id: 'user',     label: 'About You',     icon: <User size={14} /> },
  { id: 'araios',   label: 'AraisOS',       icon: <Plug size={14} />, optional: true },
  { id: 'done',     label: 'Launch',        icon: <Flag size={14} /> },
];

const STARTER_PROMPT_OPTIONS: StarterPromptOption[] = [
  {
    label: 'Priority Plan',
    prompt: 'Map my top priorities for this workspace, propose the first 3 high-impact automations, and execute the safest one now.',
  },
  {
    label: 'AraiOS Discovery',
    prompt: 'Use araios_api to inspect /api/agent, summarize available modules, and recommend the best module to build first.',
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

const ONBOARDING_ARAIOS_PREFILL_KEY = 'sentinel.onboarding.araios.prefill';

// ── helper ───────────────────────────────────────────────────────────────────

function normalizeAraisUrl(value: string): string {
  return value.trim().replace(/\/+$/, '');
}

function isLocalGatewayAraisUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    const isLoopbackHost = host === 'localhost' || host === '127.0.0.1' || host === '::1';
    const path = parsed.pathname.replace(/\/+$/, '');
    return isLoopbackHost && path === '/araios';
  } catch {
    return false;
  }
}

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
          Let's take 2 minutes to set up your workspace. We'll configure your AI agent,
          create your memory foundation, and optionally connect AraisOS.
        </p>
      </div>
      <div className="grid grid-cols-3 gap-4 max-w-lg w-full">
        {[
          { label: 'Root Memories', desc: 'Your agent knows who it is and who you are' },
          { label: 'API Keys', desc: 'Connect Claude for intelligence' },
          { label: 'AraisOS', desc: 'Optional platform integration' },
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

function ProviderCard({ name, color, apiKey, setApiKey, oauthToken, setOauthToken, apiPlaceholder, oauthPlaceholder, oauthPrefix, apiHint, oauthInstructions }: {
  name: string; color: string;
  apiKey: string; setApiKey: (v: string) => void;
  oauthToken: string; setOauthToken: (v: string) => void;
  apiPlaceholder: string; oauthPlaceholder: string; oauthPrefix: string;
  apiHint: string;
  oauthInstructions?: React.ReactNode;
}) {
  const [showKey, setShowKey] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [mode, setMode] = useState<'oauth' | 'api'>('oauth');
  const [showHelp, setShowHelp] = useState(false);
  const hasValue = !!(apiKey || oauthToken);

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
            { id: 'oauth', label: 'OAuth Token' },
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
            <div className="relative">
              <input type={showToken ? 'text' : 'password'} value={oauthToken} onChange={e => setOauthToken(e.target.value)}
                placeholder={oauthPlaceholder} className="input-field h-9 pr-10 font-mono text-xs" />
              <button type="button" onClick={() => setShowToken(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-[color:var(--text-muted)]">Starts with <span className="font-mono text-[color:var(--text-primary)]">{oauthPrefix}</span></p>
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

function GeminiCard({ apiKey, setApiKey }: { apiKey: string; setApiKey: (v: string) => void }) {
  const [showKey, setShowKey] = useState(false);
  const hasValue = !!apiKey;

  return (
    <div className={`rounded-xl border-2 transition-all ${hasValue ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-[color:var(--border)] bg-[color:var(--surface-1)]'}`}>
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[color:var(--border-subtle)]">
        <div className={`h-2 w-2 rounded-full ${hasValue ? 'bg-emerald-500' : ''}`} style={hasValue ? {} : { backgroundColor: '#4285F4' }} />
        <span className="text-xs font-bold uppercase tracking-widest">Google Gemini</span>
        {hasValue && <Check size={14} className="text-emerald-500 ml-auto" />}
      </div>
      <div className="px-4 py-2.5 space-y-2">
        <div className="relative">
          <input type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)}
            placeholder="AIza..." className="input-field h-9 pr-10 font-mono text-xs" />
          <button type="button" onClick={() => setShowKey(v => !v)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
            {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <p className="text-[10px] text-[color:var(--text-muted)]">Get your key at <span className="font-mono text-[color:var(--text-primary)]">aistudio.google.com/apikey</span></p>
      </div>
    </div>
  );
}

function LLMStep({
  apiKey, setApiKey, oauthToken, setOauthToken,
  openaiApiKey, setOpenaiApiKey, openaiOauthToken, setOpenaiOauthToken,
  geminiApiKey, setGeminiApiKey,
}: {
  apiKey: string; setApiKey: (v: string) => void;
  oauthToken: string; setOauthToken: (v: string) => void;
  openaiApiKey: string; setOpenaiApiKey: (v: string) => void;
  openaiOauthToken: string; setOpenaiOauthToken: (v: string) => void;
  geminiApiKey: string; setGeminiApiKey: (v: string) => void;
}) {
  const [copiedAnthropic, setCopiedAnthropic] = useState(false);
  const [copiedOpenai, setCopiedOpenai] = useState(false);

  function copyAnthropicCmd() {
    navigator.clipboard.writeText('claude setup-token');
    setCopiedAnthropic(true);
    setTimeout(() => setCopiedAnthropic(false), 2000);
  }

  function copyOpenaiCmd() {
    navigator.clipboard.writeText('npx codex --full-setup');
    setCopiedOpenai(true);
    setTimeout(() => setCopiedOpenai(false), 2000);
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
          <span className="flex-1">npx codex --full-setup</span>
          <button onClick={copyOpenaiCmd} className="text-[9px] font-bold uppercase tracking-widest hover:opacity-70 transition-opacity shrink-0" style={{ color: '#10A37F' }}>
            {copiedOpenai ? <Check size={10} /> : 'Copy'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>2.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Sign in via <span className="font-mono text-[color:var(--text-primary)]">auth.openai.com</span></p>
      </div>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[9px] font-black" style={{ color: '#10A37F' }}>3.</span>
        <p className="text-[10px] text-[color:var(--text-muted)]">Copy token from <span className="font-mono text-[color:var(--text-primary)]">~/.codex/auth.json</span>, paste above.</p>
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
          oauthPrefix="sk-ant-oat01-"
          apiHint="Get your key at console.anthropic.com"
          oauthInstructions={anthropicOauthInstructions}
        />
        <ProviderCard
          name="OpenAI"
          color="#10A37F"
          apiKey={openaiApiKey} setApiKey={setOpenaiApiKey}
          oauthToken={openaiOauthToken} setOauthToken={setOpenaiOauthToken}
          apiPlaceholder="sk-..."
          oauthPlaceholder="Paste Codex OAuth token..."
          oauthPrefix="eyJhbG..."
          apiHint="Get your key at platform.openai.com/api-keys"
          oauthInstructions={openaiOauthInstructions}
        />
        <GeminiCard apiKey={geminiApiKey} setApiKey={setGeminiApiKey} />
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

function AraisOSStep({ use, setUse, url, setUrl, token, setToken, autoFilled, configured }: {
  use: boolean | null; setUse: (v: boolean) => void;
  url: string; setUrl: (v: string) => void;
  token: string; setToken: (v: string) => void;
  autoFilled: boolean;
  configured: boolean;
}) {
  const [showToken, setShowToken] = useState(false);

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <div>
        <h2 className="text-xl font-black tracking-tight text-[color:var(--text-primary)]">AraisOS Integration</h2>
        <p className="text-sm text-[color:var(--text-muted)] mt-1">
          AraisOS is a platform for creating and managing custom agent modules. Connecting it lets your agent discover, use, and register new capabilities.
        </p>
      </div>

      {autoFilled && (
        <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-4 py-3 text-[11px] text-emerald-400">
          Auto-filled from this stack (one-time handoff). Review and continue.
        </div>
      )}
      {!autoFilled && configured && (
        <div className="rounded-lg bg-[color:var(--surface-2)] border border-[color:var(--border-subtle)] px-4 py-3 text-[11px] text-[color:var(--text-muted)]">
          AraiOS integration is already configured for this workspace. Enter a new key only if you want to rotate it.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {[true, false].map(val => (
          <button key={String(val)} onClick={() => setUse(val)}
            className={`rounded-xl border-2 p-4 text-left transition-all ${use === val ? 'border-[color:var(--accent-solid)] bg-[color:var(--surface-2)]' : 'border-[color:var(--border)] bg-[color:var(--surface-1)] hover:border-[color:var(--border)]'}`}>
            <div className="flex items-center gap-2 mb-1">
              <div className={`h-4 w-4 rounded-full border-2 flex items-center justify-center ${use === val ? 'border-[color:var(--accent-solid)] bg-[color:var(--accent-solid)]' : 'border-[color:var(--border)]'}`}>
                {use === val && <div className="h-2 w-2 rounded-full bg-[color:var(--app-bg)]" />}
              </div>
              <span className="text-[11px] font-bold uppercase tracking-widest">
                {val ? 'Connect' : 'Skip'}
              </span>
            </div>
            <p className="text-[11px] text-[color:var(--text-muted)] leading-snug">
              {val ? 'Set up AraisOS integration with base URL and auth token.' : 'Skip for now. You can connect later.'}
            </p>
          </button>
        ))}
      </div>

      {use === true && (
        <div className="flex flex-col gap-4 animate-in fade-in duration-200">
          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Base URL</label>
            <input value={url} onChange={e => setUrl(e.target.value)}
              placeholder="http://localhost:4747/araios"
              className="input-field h-11 font-mono text-sm" />
          </div>
          <div className="space-y-2">
            <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Agent API Key</label>
            <div className="relative">
              <input type={showToken ? 'text' : 'password'}
                value={token} onChange={e => setToken(e.target.value)}
                placeholder="sk-arais-agent-****"
                className="input-field h-11 pr-10 font-mono text-sm" />
              <button type="button" onClick={() => setShowToken(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-[10px] text-[color:var(--text-muted)]">
              Use the long-lived <span className="font-bold text-[color:var(--text-primary)]">agent API key</span>, not an admin key.
              Sentinel stores it securely in your workspace and the <span className="font-mono">araios_api</span> tool handles token exchange automatically.
            </p>
          </div>
          <div className="rounded-lg bg-[color:var(--surface-2)] px-4 py-3 text-[11px] text-[color:var(--text-muted)]">
            After setup, ask Sentinel to call <span className="font-mono text-[color:var(--text-primary)]">araios_api</span> with path <span className="font-mono text-[color:var(--text-primary)]">/api/agent</span> to discover available modules and endpoints.
          </div>
        </div>
      )}
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
  const [step, setStep] = useState(0);

  // LLM keys — Anthropic
  const [apiKey, setApiKey] = useState('');
  const [oauthToken, setOauthToken] = useState('');
  // LLM keys — OpenAI
  const [openaiApiKey, setOpenaiApiKey] = useState('');
  const [openaiOauthToken, setOpenaiOauthToken] = useState('');
  // LLM keys — Gemini
  const [geminiApiKey, setGeminiApiKey] = useState('');

  // Agent identity
  const [agentName, setAgentName] = useState('');
  const [agentRole, setAgentRole] = useState('');
  const [agentPersonality, setAgentPersonality] = useState('');

  // User profile
  const [userName, setUserName] = useState('');
  const [userContext, setUserContext] = useState('');

  // AraisOS
  const [useAraisOS, setUseAraisOS] = useState<boolean | null>(null);
  const [araisUrl, setAraisUrl] = useState('');
  const [araisToken, setAraisToken] = useState('');
  const [araisAutoFilled, setAraisAutoFilled] = useState(false);
  const [araisConfigured, setAraisConfigured] = useState(false);

  // Done step
  const [firstMessage, setFirstMessage] = useState(
    STARTER_PROMPT_OPTIONS[0].prompt
  );
  const [isCompleting, setIsCompleting] = useState(false);
  const [completedItems, setCompletedItems] = useState<string[]>([]);

  const isLastStep = step === STEPS.length - 1;

  useEffect(() => {
    let mounted = true;

    async function initializeAraisDefaults() {
      const origin = window.location.origin.replace(/\/$/, '');
      const publicFallbackUrl = `${origin}/araios`;
      let runtimeFallbackUrl = publicFallbackUrl;
      let persistedBaseUrl = '';
      let persistedConfigured = false;

      try {
        const defaults = await api.get<{ araios_runtime_url?: string | null }>('/onboarding/defaults');
        const configuredRuntimeUrl = normalizeAraisUrl(defaults.araios_runtime_url || '');
        if (configuredRuntimeUrl) {
          runtimeFallbackUrl = configuredRuntimeUrl;
        }
      } catch {
        runtimeFallbackUrl = publicFallbackUrl;
      }

      try {
        const integration = await api.get<{ configured?: boolean; base_url?: string | null }>('/settings/araios');
        persistedConfigured = !!integration.configured;
        persistedBaseUrl = normalizeAraisUrl(integration.base_url || '');
      } catch {
        persistedConfigured = false;
        persistedBaseUrl = '';
      }

      let didAutofill = false;
      try {
        const raw = sessionStorage.getItem(ONBOARDING_ARAIOS_PREFILL_KEY);
        if (!raw) {
          if (mounted) {
            const resolved = persistedBaseUrl || runtimeFallbackUrl;
            setAraisUrl(resolved);
            if (persistedConfigured) {
              setUseAraisOS(true);
              setAraisConfigured(true);
              setAraisAutoFilled(true);
            }
          }
          return;
        }

        const parsed = JSON.parse(raw) as { base_url?: string; agent_api_key?: string };
        const prefillBaseUrl = normalizeAraisUrl(parsed.base_url || '');
        const agentApiKey = (parsed.agent_api_key || '').trim();
        const resolvedBaseUrl = prefillBaseUrl
          ? (isLocalGatewayAraisUrl(prefillBaseUrl) ? runtimeFallbackUrl : prefillBaseUrl)
          : runtimeFallbackUrl;

        if (mounted && resolvedBaseUrl) {
          setAraisUrl(resolvedBaseUrl);
        }
        if (mounted && agentApiKey) {
          setAraisToken(agentApiKey);
          setUseAraisOS(true);
        }

        if (prefillBaseUrl || agentApiKey) {
          didAutofill = true;
        }
      } catch {
        if (mounted) {
          setAraisUrl(runtimeFallbackUrl);
        }
      } finally {
        // One-time prefill only: remove immediately after first read.
        sessionStorage.removeItem(ONBOARDING_ARAIOS_PREFILL_KEY);
        if (mounted && didAutofill) {
          setAraisAutoFilled(true);
        }
        if (mounted && persistedConfigured) {
          setAraisConfigured(true);
        }
      }
    }

    void initializeAraisDefaults();
    return () => {
      mounted = false;
    };
  }, []);

  function canProceed(): boolean {
    const id = STEPS[step].id;
    if (id === 'araios') {
      if (useAraisOS === null) return false;
      if (useAraisOS === false) return true;
      return !!araisUrl.trim() && (!!araisToken.trim() || araisConfigured);
    }
    return true; // all other steps are optional content-wise
  }

  async function handleFinish() {
    setIsCompleting(true);
    const items: string[] = [];

    try {
      const identity = resolveAgentIdentity(agentName, agentRole, agentPersonality);
      const userProfile = resolveUserProfile(userName, userContext);

      // 1. Save API keys
      const hasAnthropic = !!(apiKey || oauthToken);
      const hasOpenai = !!(openaiApiKey || openaiOauthToken);
      const hasGemini = !!geminiApiKey;
      if (hasAnthropic || hasOpenai || hasGemini) {
        await api.post('/settings/api-keys', {
          anthropic_api_key: apiKey || undefined,
          anthropic_oauth_token: oauthToken || undefined,
          openai_api_key: openaiApiKey || undefined,
          openai_oauth_token: openaiOauthToken || undefined,
          gemini_api_key: geminiApiKey || undefined,
        });
        const saved: string[] = [];
        if (hasAnthropic) saved.push('Anthropic');
        if (hasOpenai) saved.push('OpenAI');
        if (hasGemini) saved.push('Gemini');
        items.push(`${saved.join(' + ')} provider${saved.length > 1 ? 's' : ''} saved`);
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

      // 4. Persist AraiOS integration settings
      if (useAraisOS === true) {
        await api.post('/settings/araios', {
          enabled: true,
          base_url: normalizeAraisUrl(araisUrl),
          agent_api_key: araisToken.trim() || undefined,
        });
        items.push('AraiOS integration configured');
        setCompletedItems([...items]);
      } else if (useAraisOS === false) {
        await api.post('/settings/araios', { enabled: false });
      }

      // 5. Mark onboarding complete + persist system prompt
      await api.post('/onboarding/complete', {
        system_prompt: buildSystemPrompt(identity),
      });
      items.push('Workspace ready');
      setCompletedItems([...items]);

      await new Promise(r => setTimeout(r, 600)); // brief pause so user sees the checkmarks

      navigate('/sessions', { state: { firstMessage: firstMessage.trim() || undefined } });
    } catch (err) {
      toast.error('Setup failed — please try again');
      setIsCompleting(false);
    }
  }

  const progress = (step / (STEPS.length - 1)) * 100;

  return (
    <div className="min-h-screen w-full flex flex-col bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
      {/* Top progress bar */}
      <div className="h-0.5 w-full bg-[color:var(--surface-2)]">
        <div className="h-full bg-[color:var(--accent-solid)] transition-all duration-500"
          style={{ width: `${progress}%` }} />
      </div>

      <div className="flex flex-1 min-h-0">
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
        <main className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto p-8 md:p-16">
            <div className="w-full max-w-xl mx-auto my-auto min-h-full flex flex-col justify-center animate-in fade-in duration-300" key={step}>
              {step === 0 && <WelcomeStep />}
              {step === 1 && <LLMStep apiKey={apiKey} setApiKey={setApiKey} oauthToken={oauthToken} setOauthToken={setOauthToken} openaiApiKey={openaiApiKey} setOpenaiApiKey={setOpenaiApiKey} openaiOauthToken={openaiOauthToken} setOpenaiOauthToken={setOpenaiOauthToken} geminiApiKey={geminiApiKey} setGeminiApiKey={setGeminiApiKey} />}
              {step === 2 && <AgentStep name={agentName} setName={setAgentName} role={agentRole} setRole={setAgentRole} personality={agentPersonality} setPersonality={setAgentPersonality} />}
              {step === 3 && <UserStep userName={userName} setUserName={setUserName} userContext={userContext} setUserContext={setUserContext} />}
              {step === 4 && (
                <AraisOSStep
                  use={useAraisOS}
                  setUse={setUseAraisOS}
                  url={araisUrl}
                  setUrl={setAraisUrl}
                  token={araisToken}
                  setToken={setAraisToken}
                  autoFilled={araisAutoFilled}
                  configured={araisConfigured}
                />
              )}
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
          <div className="border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-8 py-4 flex items-center justify-between">
            <button
              onClick={() => setStep(s => Math.max(0, s - 1))}
              disabled={step === 0 || isCompleting}
              className="btn-secondary h-10 px-5 gap-2 text-sm disabled:opacity-30"
            >
              <ArrowLeft size={16} /> Back
            </button>

            <div className="flex items-center gap-2">
              {STEPS.map((_, i) => (
                <div key={i} className={`h-1.5 rounded-full transition-all duration-300 ${i === step ? 'w-6 bg-[color:var(--accent-solid)]' : i < step ? 'w-1.5 bg-emerald-500' : 'w-1.5 bg-[color:var(--surface-2)]'}`} />
              ))}
            </div>

            {isLastStep ? (
              <button
                onClick={handleFinish}
                disabled={isCompleting}
                className="btn-primary h-10 px-6 gap-2 text-sm"
              >
                {isCompleting ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                {isCompleting ? 'Setting up…' : 'Launch Sentinel'}
              </button>
            ) : (
              <button
                onClick={() => setStep(s => s + 1)}
                disabled={!canProceed()}
                className="btn-primary h-10 px-6 gap-2 text-sm"
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
