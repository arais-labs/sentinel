import {
  ArrowDown,
  Bot,
  ChevronDown,
  Home,
  Expand,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Users,
  Square,
  Wand2,
  Wrench,
  X,
  Trash2,
  Terminal,
  Folder,
  Globe,
  ExternalLink,
  Zap,
  Activity,
  Brain,
  Sparkles,
  Paperclip,
  Check,
  Pencil,
  GitBranch,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { ChangeEvent, ClipboardEvent, FormEvent, useEffect, useMemo, useRef, useState, memo, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { SessionMessageCard, buildToolArgumentsByCallId, ToolPayloadView, ToolPayloadCompactSummary } from '../components/session/SessionMessageCard';
import { SessionHistorySidebar } from '../components/session/SessionHistorySidebar';
import { DesktopPreview } from '../components/session/DesktopPreview';
import { TerminalPreview } from '../components/session/TerminalPreview';
import { getTerminalLabel, summarizeCommand } from '../lib/terminalIdentity';
import { SubAgentTaskModal } from '../components/SubAgentTaskModal';
import { SpawnSubAgentModal } from '../components/SpawnSubAgentModal';
import { Markdown } from '../components/ui/Markdown';
import { WorkbenchExplorerPane } from '../components/workbench/WorkbenchExplorerPane';
import { Workbench, type WorkbenchTab } from '../components/workbench/Workbench';
import { StatusChip } from '../components/ui/StatusChip';
import { SESSION_DEBUG_PANEL_ENABLED, wsSessionsBaseUrl } from '../lib/env';
import { formatCompactDate, toPrettyJson, truncate } from '../lib/format';
import { buildRuntimeGitChangedTree } from '../lib/runtimeGitTree';
import {
  approvalKey,
  approvalRefFromMetadata,
  isWaitingApproval,
  type ApprovalRef,
} from '../lib/approvals';
import { api } from '../lib/api';
import { instanceRouteFromPath } from '../lib/routes';
import {
  applyToolcallEnd,
  applyToolResult,
  defaultStreamingState,
  hasVisibleStreamingText,
  shouldShowThinkingIndicator,
  streamingCallKey,
  streamingCallKeyFromParts,
} from './sessionStreaming';
import type { StreamingState, StreamingToolCall } from './sessionStreaming';
import type {
  AgentModeOption,
  AgentModesResponse,
  Message,
  MessageAttachment,
  MessageListResponse,
  ModelOption,
  ModelsResponse,
  RuntimeLiveView,
  Session,
  SessionContextUsage,
  SessionRuntimeFileEntry,
  SessionRuntimeFilePreviewResponse,
  SessionRuntimeFilesResponse,
  SessionRuntimeGitChangedFilesResponse,
  SessionRuntimeGitDiffResponse,
  SessionRuntimeGitRoot,
  SessionRuntimeGitRootsResponse,
  SessionListResponse,
  SubAgentTask,
  SubAgentTaskListResponse,
  WsConnectionState,
  WsEvent,
} from '../types/api';

// --- Utility Functions ---

function taskStatusTone(status: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  switch (status) {
    case 'running':
    case 'completed':
      return 'good';
    case 'pending':
    case 'connecting':
    case 'reconnecting':
      return 'warn';
    case 'failed':
    case 'cancelled':
    case 'disconnected':
      return 'danger';
    default:
      return 'default';
  }
}

function sortMessages(items: Message[]) {
  return [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function inferCodeLanguageFromName(name: string): string {
  const normalized = name.trim().toLowerCase();
  if (!normalized) return 'text';
  if (normalized.endsWith('.ts') || normalized.endsWith('.tsx')) return 'typescript';
  if (normalized.endsWith('.js') || normalized.endsWith('.mjs') || normalized.endsWith('.cjs')) return 'javascript';
  if (normalized.endsWith('.py')) return 'python';
  if (normalized.endsWith('.rs')) return 'rust';
  if (normalized.endsWith('.go')) return 'go';
  if (normalized.endsWith('.java')) return 'java';
  if (normalized.endsWith('.kt')) return 'kotlin';
  if (normalized.endsWith('.rb')) return 'ruby';
  if (normalized.endsWith('.php')) return 'php';
  if (normalized.endsWith('.sh') || normalized.endsWith('.bash') || normalized.endsWith('.zsh')) return 'bash';
  if (normalized.endsWith('.css')) return 'css';
  if (normalized.endsWith('.scss')) return 'scss';
  if (normalized.endsWith('.html') || normalized.endsWith('.htm')) return 'html';
  if (normalized.endsWith('.json')) return 'json';
  if (normalized.endsWith('.md')) return 'markdown';
  if (normalized.endsWith('.yaml') || normalized.endsWith('.yml')) return 'yaml';
  if (normalized.endsWith('.toml')) return 'toml';
  if (normalized.endsWith('.sql')) return 'sql';
  if (normalized.endsWith('.xml')) return 'xml';
  if (normalized.endsWith('.diff') || normalized.endsWith('.patch')) return 'diff';
  return 'text';
}

function toMarkdownCodeFence(content: string, language: string): string {
  let fence = '```';
  while (content.includes(fence)) {
    fence += '`';
  }
  return `${fence}${language}\n${content}\n${fence}`;
}

interface SessionDebugEvent {
  id: string;
  at: string;
  type: string;
  summary: string;
}

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
}

// Top-level right-rail tabs. `runtime` is a composite tab that contains
// three sub-views (Desktop / Terminals / Files) selected via an inner
// segmented control — see `RuntimeView` below. The previous flat enum
// (desktop / terminals / runtime / …) collapsed those three siblings into
// one parent so the rail stays under three primary tabs.
type RightRailTab = 'runtime' | 'sub_agents' | 'sessions' | 'debug';
type RuntimeView = 'desktop' | 'terminals' | 'files';

interface ActiveTerminal {
  id: string;
  /** Backend-supplied label, used only as a fallback for auto-allocated terminals. */
  label: string | null;
  createdBy: 'agent' | 'user';
  createdAt: number;
  auto: boolean;
  busy: boolean;
  /** Most recent command run in this terminal (for tooltip + rail header). */
  lastCommand: string | null;
}

function buildRuntimeDiffBaseRefOptions(
  roots: SessionRuntimeGitRoot[],
  currentRef: string | null | undefined,
): string[] {
  const options = new Set<string>();
  options.add('HEAD');
  for (const root of roots) {
    if (!root.detached_head && root.branch) {
      options.add(root.branch);
      options.add(`origin/${root.branch}`);
    }
  }
  options.add('origin/main');
  options.add('origin/master');
  const normalizedCurrent = (currentRef ?? '').trim();
  if (normalizedCurrent) {
    options.add(normalizedCurrent);
  }
  return Array.from(options);
}

function humanizeAgentError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes('all providers failed')) {
    const normalized = raw.replace(/\s+/g, ' ').trim();
    if (normalized.toLowerCase().startsWith('all providers failed')) {
      const firstDot = normalized.indexOf('.');
      if (firstDot >= 0 && firstDot + 1 < normalized.length) {
        return `All AI providers failed.${normalized.slice(firstDot + 1)}`.slice(0, 700);
      }
      return 'All AI providers failed.';
    }
    return normalized.slice(0, 700);
  }
  if (lower.includes('rate_limit') || lower.includes('rate limit') || lower.includes('http_429') || lower.includes('429')) {
    return 'API rate limit reached. Please wait a moment and try again, or check your Anthropic account usage limits.';
  }
  if (lower.includes('authentication') || lower.includes('401') || lower.includes('invalid api key') || lower.includes('invalid_api_key')) {
    return 'API authentication failed. Please check your API key in Settings.';
  }
  if (lower.includes('insufficient') || lower.includes('billing') || lower.includes('payment') || lower.includes('402')) {
    return 'API billing issue. Please check your account balance and payment method.';
  }
  if (lower.includes('overloaded') || lower.includes('503') || lower.includes('server_error')) {
    return 'The AI provider is currently overloaded. Please try again in a few moments.';
  }
  if (lower.includes('timeout') || lower.includes('timed out')) {
    return 'Request timed out. The server took too long to respond. Please try again.';
  }
  // Truncate very long raw errors
  if (raw.length > 200) {
    return raw.slice(0, 200) + '…';
  }
  return raw;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasUnresolvedToolCalls(messages: Message[]): boolean {
  const resolvedIds = new Set<string>();
  const pendingIds = new Set<string>();
  for (const message of messages) {
    if (message.role === 'assistant') {
      const metadata = isObjectRecord(message.metadata) ? message.metadata : {};
      const toolCalls = metadata.tool_calls;
      if (!Array.isArray(toolCalls)) continue;
      for (const rawCall of toolCalls) {
        if (!isObjectRecord(rawCall)) continue;
        const callId = typeof rawCall.id === 'string' ? rawCall.id.trim() : '';
        if (!callId || resolvedIds.has(callId)) continue;
        pendingIds.add(callId);
      }
      continue;
    }
    if (message.role !== 'tool' && message.role !== 'tool_result') {
      continue;
    }
    const callId = typeof message.tool_call_id === 'string' ? message.tool_call_id.trim() : '';
    if (!callId) continue;
    resolvedIds.add(callId);
    pendingIds.delete(callId);
  }
  return pendingIds.size > 0;
}

const APPROVAL_DEBUG_STORAGE_KEY = 'sentinel.debug.approvals';
const AGENT_MODE_STORAGE_KEY = 'sentinel-selected-agent-mode';

function isApprovalDebugEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const raw = window.localStorage.getItem(APPROVAL_DEBUG_STORAGE_KEY);
    return raw === '1' || raw === 'true';
  } catch {
    return false;
  }
}

function approvalDebugLog(event: string, details: Record<string, unknown>): void {
  if (!isApprovalDebugEnabled()) return;
  console.info(`[approval-debug] ${event}`, details);
}

function parseTier(value: string | null): ModelOption['tier'] | null {
  if (value === 'fast' || value === 'normal' || value === 'hard') {
    return value;
  }
  return null;
}

function serializeToolArguments(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toolArgumentsFromToolResultPayload(payload: Record<string, unknown>): string {
  const fromPayload = payload.tool_arguments;
  if (fromPayload != null) {
    return serializeToolArguments(fromPayload);
  }
  const metadata = isObjectRecord(payload.metadata) ? payload.metadata : null;
  if (metadata && metadata.tool_arguments != null) {
    return serializeToolArguments(metadata.tool_arguments);
  }
  return '';
}

function hasMeaningfulToolArguments(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  if (trimmed === '{}' || trimmed === 'null') return false;
  return true;
}

function browserCommandLabelFromArguments(raw: string | undefined): string {
  if (!raw) return 'browser';
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const command = typeof parsed.command === 'string' ? parsed.command.trim() : '';
    return command ? command.replaceAll('_', ' ') : 'browser';
  } catch {
    return 'browser';
  }
}

function mergeStreamingToolArguments(current: string, delta: string): string {
  if (!delta) return current;
  const trimmedCurrent = current.trim();
  const trimmedDelta = delta.trim();

  if (!hasMeaningfulToolArguments(current) || trimmedCurrent === '{}') {
    return delta;
  }

  const currentLooksCompleteJson =
    (trimmedCurrent.startsWith('{') && trimmedCurrent.endsWith('}')) ||
    (trimmedCurrent.startsWith('[') && trimmedCurrent.endsWith(']'));
  const deltaLooksLikeFreshJson =
    trimmedDelta.startsWith('{') || trimmedDelta.startsWith('[');

  if (currentLooksCompleteJson && deltaLooksLikeFreshJson) {
    return delta;
  }

  return `${current}${delta}`;
}

function isSyntheticToolCallId(id: string): boolean {
  const normalized = id.trim().toLowerCase();
  return normalized.startsWith('tool-');
}

const MAX_IMAGE_ATTACHMENTS = 4;
const MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const ALLOWED_IMAGE_MIME_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== 'string') {
        reject(new Error('Failed to read file'));
        return;
      }
      const comma = value.indexOf(',');
      resolve(comma >= 0 ? value.slice(comma + 1) : value);
    };
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });
}

// --- Memoized Components ---

// --- Sub-Components ---

function StreamToolCard({
  call,
  active,
  onResolveApproval,
  resolvingApprovalKey,
  onOpenTerminal,
}: {
  call: StreamingToolCall;
  active: boolean;
  onResolveApproval: (approval: ApprovalRef, decision: 'approve' | 'reject') => void;
  resolvingApprovalKey: string | null;
  onOpenTerminal?: (terminalId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isScreenshotCall = call.name.toLowerCase().includes('screenshot');
  const pendingApproval = isWaitingApproval(call.metadata);
  const approvalRef = pendingApproval ? approvalRefFromMetadata(call.metadata) : null;
  const canResolveApproval = pendingApproval && approvalRef?.canResolve === true;
  const approvalLinkMissing = pendingApproval && !approvalRef;
  const approvalActionBusy = approvalRef ? resolvingApprovalKey === approvalKey(approvalRef) : false;
  // When the runtime tool result carries a terminal id, surface a chip in the
  // card header so the user can jump from "what did the agent run?" to "let
  // me see/control that terminal" in one click.
  const terminalIdFromMetadata =
    typeof call.metadata?.terminal_id === 'string' && call.metadata.terminal_id.length > 0
      ? (call.metadata.terminal_id as string)
      : null;

  useEffect(() => {
    if (pendingApproval) setExpanded(true);
  }, [pendingApproval]);

  return (
    <div className="flex w-full flex-col gap-1 animate-in items-start">
      <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">
          tool_call
        </span>
        {pendingApproval ? (
          <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-rose-400">• waiting approval</span>
        ) : active ? (
          <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-500/70">• running</span>
        ) : null}
      </div>
      <div
        onClick={!expanded ? () => setExpanded(true) : undefined}
        className={`${expanded ? 'w-full max-w-[90%]' : 'w-fit max-w-[90%]'} inline-flex flex-col rounded-2xl rounded-tl-none px-4 py-2 text-xs shadow-sm border transition-all duration-300 ease-in-out ${
          pendingApproval
            ? 'bg-rose-500/8 border-rose-500/30 shadow-md ring-1 ring-rose-500/20'
            : `bg-[color:var(--surface-1)] border-[color:var(--border-subtle)] ${expanded ? '' : 'cursor-pointer hover:border-sky-500/30 hover:bg-sky-500/[0.03]'}`
        } relative group/card`}
      >
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((value) => !value);
          }}
          className={`${expanded ? 'w-full mb-0.5' : 'w-auto'} flex items-center justify-between gap-4 py-0.5 text-left cursor-pointer`}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div className={`flex items-center justify-center w-6 h-6 rounded-lg ${pendingApproval ? 'bg-rose-500/15 text-rose-400 border border-rose-500/25' : 'bg-sky-500/10 text-sky-400 border border-sky-500/20'} shrink-0`}>
              <Wrench size={12} strokeWidth={2.5} />
            </div>
            <div className="flex flex-col min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-black uppercase tracking-[0.12em] truncate ${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-300'}`}>
                  {call.name}
                </span>
                {pendingApproval && (
                  <span className="inline-flex items-center rounded-full border border-rose-500/35 bg-rose-500/15 px-1.5 py-0.5 text-[8px] font-black uppercase tracking-widest text-rose-300 animate-pulse">
                    Action Required
                  </span>
                )}
                {terminalIdFromMetadata && onOpenTerminal ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenTerminal(terminalIdFromMetadata);
                    }}
                    className="inline-flex items-center gap-1 rounded-full border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[8px] font-black uppercase tracking-widest text-sky-300 hover:bg-sky-500/20 transition-colors"
                    title={`Open terminal ${terminalIdFromMetadata}`}
                  >
                    <Terminal size={9} />
                    T:{terminalIdFromMetadata}
                  </button>
                ) : null}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {active && <Loader2 size={12} className={`animate-spin ${pendingApproval ? 'text-rose-300' : 'text-sky-500'}`} />}
            {expanded || pendingApproval ? (
              <ChevronDown size={14} strokeWidth={3} className={`${pendingApproval ? 'text-rose-300' : 'text-sky-400'} transition-transform duration-500 ${expanded ? 'rotate-180 opacity-40' : 'opacity-100'}`} />
            ) : (
              <div className="inline-flex items-center gap-1 rounded-full border border-sky-500/15 bg-sky-500/[0.05] px-2 py-1 text-[8px] font-bold uppercase tracking-[0.14em] text-sky-400/80 opacity-0 transition-all duration-200 group-hover/card:opacity-100 group-hover/card:border-sky-500/30 group-hover/card:bg-sky-500/[0.08] group-hover/card:text-sky-300">
                <ChevronDown size={10} strokeWidth={3} />
                Click to expand
              </div>
            )}
          </div>
        </button>

        {expanded ? (
          <div className="mt-0 pt-3 animate-in fade-in slide-in-from-top-1 duration-200">
            <div className="space-y-6">
              {!isScreenshotCall ? (
                <div>
                  <div className="mb-2.5 flex items-center gap-2">
                    <div className="flex items-center justify-center w-7 h-7 rounded-full bg-[color:var(--surface-1)] border border-sky-500/30 text-sky-500/60 shadow-sm shrink-0">
                      <Terminal size={14} />
                    </div>
                    <p className="text-[10px] font-black uppercase tracking-[0.2em] text-sky-600 dark:text-sky-300">Arguments</p>
                    <div className="h-px flex-1 bg-gradient-to-r from-sky-500/30 to-transparent" />
                  </div>
                  <div className="pl-9">
                    <ToolPayloadView
                      raw={call.argumentsJson}
                      emptyLabel="No input."
                      toolName={call.name}
                      payloadKind="input"
                    />
                  </div>
                </div>
              ) : null}
              <div className="pb-2">
                <div className="mb-2.5 flex items-center gap-2">
                  <div className={`flex items-center justify-center w-7 h-7 rounded-full border shrink-0 ${call.isError ? 'bg-rose-500/10 border-rose-500/20 text-rose-500/60' : 'bg-[color:var(--surface-1)] border-emerald-500/20 text-emerald-500/60'} shadow-sm`}>
                    {active ? <Loader2 size={14} className="animate-spin" /> : call.isError ? <X size={14} strokeWidth={3} /> : <Check size={14} strokeWidth={3} />}
                  </div>
                  <p className={`text-[10px] font-black uppercase tracking-[0.2em] ${call.isError ? 'text-rose-500/60' : 'text-emerald-500/60'}`}>Result</p>
                  <div className={`h-px flex-1 bg-gradient-to-r ${call.isError ? 'from-rose-500/20' : 'from-emerald-500/20'} to-transparent`} />
                  {call.isError && (
                    <span className="inline-flex items-center rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[7px] font-black uppercase tracking-widest text-rose-500">
                      Error
                    </span>
                  )}
                </div>
                <div className="pl-9 space-y-4">
                  <ToolPayloadView
                    raw={call.outputJson}
                    emptyLabel={active ? 'Running tool...' : 'No output payload.'}
                    showRawJson={!isScreenshotCall}
                    toolName={call.name}
                    payloadKind="output"
                  />
                  {canResolveApproval && approvalRef ? (
                    <div className="flex items-center gap-3 pt-1">
                      <button
                        type="button"
                        onClick={() => onResolveApproval(approvalRef, 'reject')}
                        disabled={approvalActionBusy}
                        className="inline-flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-1.5 text-[9px] font-black uppercase tracking-widest text-rose-400 hover:bg-rose-500/15 transition-all"
                      >
                        {approvalActionBusy ? <Loader2 size={10} className="animate-spin" /> : <X size={10} strokeWidth={3} />}
                        Deny
                      </button>
                      <button
                        type="button"
                        onClick={() => onResolveApproval(approvalRef, 'approve')}
                        disabled={approvalActionBusy}
                        className="inline-flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-3 py-1.5 text-[9px] font-black uppercase tracking-widest text-emerald-400 hover:bg-emerald-500/15 transition-all"
                      >
                        {approvalActionBusy ? <Loader2 size={10} className="animate-spin" /> : <CheckCircle2 size={10} strokeWidth={3} />}
                        Confirm
                      </button>
                    </div>
                  ) : null}
                  {approvalLinkMissing ? (
                    <div className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/5 border border-amber-500/20">
                      <AlertCircle size={12} className="text-amber-500 shrink-0 mt-0.5" />
                      <p className="text-[9px] leading-relaxed text-amber-400/80 font-medium">
                        Action required but controls are detached.
                      </p>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <ToolPayloadCompactSummary
            toolName={call.name}
            inputRaw={call.argumentsJson}
            outputRaw={call.outputJson}
            outputEmptyLabel={active ? 'Running tool...' : 'No output payload.'}
            outputError={call.isError}
            hideInput={isScreenshotCall}
          />
        )}
      </div>
    </div>
  );
}

// --- Main Page Component ---

export function SessionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id: routeSessionId } = useParams<{ id: string }>();
  const sessionRoute = useCallback(
    (sessionId?: string | null) => instanceRouteFromPath(location.pathname, sessionId ? `sessions/${sessionId}` : 'sessions'),
    [location.pathname],
  );
  const activeInstanceName = useMemo(() => {
    const match = location.pathname.match(/^\/instances\/([^/]+)/);
    return match?.[1] ? decodeURIComponent(match[1]) : null;
  }, [location.pathname]);

  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [defaultSessionId, setDefaultSessionId] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [settingMainSessionId, setSettingMainSessionId] = useState<string | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingSessionTitle, setEditingSessionTitle] = useState('');
  const [isMultiSelectMode, setIsMultiSelectMode] = useState(false);
  const [selectedSessionIds, setSelectedSessionIds] = useState<string[]>([]);
  const [historyTab, setHistoryTab] = useState<'sessions' | 'sub_agents'>('sessions');
  const [sessionFilter, setSessionFilter] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(routeSessionId ?? null);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [agentModes, setAgentModes] = useState<AgentModeOption[]>([]);
  const [selectedAgentMode, setSelectedAgentMode] = useState<string | null>(() => {
    const raw = localStorage.getItem(AGENT_MODE_STORAGE_KEY);
    return raw && raw.trim() ? raw.trim() : null;
  });
  const [isSessionDropdownOpen, setIsSessionDropdownOpen] = useState(false);
  const [sessionDropdownRect, setSessionDropdownRect] = useState<{ left: number; top: number; width: number } | null>(null);
  const [selectedTier, setSelectedTier] = useState(
    () => parseTier(localStorage.getItem('sentinel-selected-tier')) ?? 'normal',
  );
  const [isAgentModeDropdownOpen, setIsAgentModeDropdownOpen] = useState(false);
  const [isEffortDropdownOpen, setIsEffortDropdownOpen] = useState(false);
  const [maxIterations, setMaxIterations] = useState(50);

  const [messages, setMessages] = useState<Message[]>([]);
  const [contextTokenBudget, setContextTokenBudget] = useState<number | null>(null);
  const [contextTokenEstimate, setContextTokenEstimate] = useState<number | null>(null);
  const [contextTokenPercent, setContextTokenPercent] = useState<number | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);
  const [composer, setComposer] = useState('');
  const [composerAttachments, setComposerAttachments] = useState<MessageAttachment[]>([]);
  const [retryCandidate, setRetryCandidate] = useState<{ messageId: string; error: string } | null>(null);
  const [retryingMessageId, setRetryingMessageId] = useState<string | null>(null);

  const [streaming, setStreaming] = useState<StreamingState>(defaultStreamingState);
  const [resolvingApprovalKey, setResolvingApprovalKey] = useState<string | null>(null);

  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [rightRailTab, setRightRailTab] = useState<RightRailTab>('runtime');
  // Selected sub-view inside the Runtime tab. Defaults to Desktop (the most
  // recognizable "what is the agent doing" surface). Auto-promoted to
  // `terminals` the first time a terminal opens — see the terminal_opened
  // WS handler below for the rule.
  const [runtimeView, setRuntimeView] = useState<RuntimeView>('desktop');
  // Tracks the moment the user last manually picked a runtimeView. Used to
  // suppress auto-focus when the user has just expressed an intent — we
  // don't want the agent's activity stealing focus a half-second later.
  const lastRuntimeViewIntentRef = useRef<number>(0);
  // Refs to the three runtime sub-tab buttons + the strip container, so the
  // sliding indicator can size to the *actual* active button instead of a
  // calc'd third. When the rail is narrow and a button's content (icon +
  // label + badge) overflows its grid cell, the cell-based indicator falls
  // out of alignment — measuring offsetLeft/offsetWidth dodges that whole
  // class of bug.
  const runtimeTabRefs = useRef<Record<RuntimeView, HTMLButtonElement | null>>({
    desktop: null,
    terminals: null,
    files: null,
  });
  const runtimeStripRef = useRef<HTMLDivElement | null>(null);
  const [runtimeIndicator, setRuntimeIndicator] = useState<{ left: number; width: number } | null>(null);
  // Centralised predicates so every conditional in the file reads the same
  // way and a future rename only touches one line. The Runtime tab is a
  // composite, so individual sub-views are gated on both rightRailTab AND
  // runtimeView.
  const isRuntimeTab = rightRailTab === 'runtime';
  const showDesktopView = isRuntimeTab && runtimeView === 'desktop';
  const showTerminalsView = isRuntimeTab && runtimeView === 'terminals';
  const showFilesView = isRuntimeTab && runtimeView === 'files';
  // Pills above the chat composer surface every tmux-backed terminal the
  // agent (or the user) has opened in the current chat session. State is
  // driven by `terminal_opened/closed/busy` WS events plus the initial
  // `connected` payload that lists already-live terminals on page load.
  const [activeTerminals, setActiveTerminals] = useState<ActiveTerminal[]>([]);
  const [focusedTerminalId, setFocusedTerminalId] = useState<string | null>(null);

  // Position the runtime-tabs sliding indicator under the active button.
  // useLayoutEffect runs before paint, so the indicator never flashes at the
  // wrong width. We re-measure on view change, on badge changes (since they
  // mutate button widths), and on rail resize via ResizeObserver below.
  useLayoutEffect(() => {
    if (!isRuntimeTab) return;
    const active = runtimeTabRefs.current[runtimeView];
    if (!active) return;
    setRuntimeIndicator({ left: active.offsetLeft, width: active.offsetWidth });
  }, [isRuntimeTab, runtimeView, activeTerminals.length]);

  useEffect(() => {
    const strip = runtimeStripRef.current;
    if (!strip || !isRuntimeTab) return;
    // Strip width changes when the user resizes the right rail. Reread the
    // active button's box and reapply. ResizeObserver fires once on attach
    // too, so this also handles the initial mount cleanly.
    const observer = new ResizeObserver(() => {
      const active = runtimeTabRefs.current[runtimeView];
      if (!active) return;
      setRuntimeIndicator({ left: active.offsetLeft, width: active.offsetWidth });
    });
    observer.observe(strip);
    return () => observer.disconnect();
  }, [isRuntimeTab, runtimeView]);
  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);

  const [runtimePath, setRuntimePath] = useState('');
  const [runtimeRepoChangesByRoot, setRuntimeRepoChangesByRoot] = useState<Record<string, SessionRuntimeGitChangedFilesResponse | null>>({});
  const [runtimeRepoChangesLoadingByRoot, setRuntimeRepoChangesLoadingByRoot] = useState<Record<string, boolean>>({});
  const [runtimeExpandedGitDirs, setRuntimeExpandedGitDirs] = useState<Record<string, boolean>>({});
  const [workbenchTabs, setWorkbenchTabs] = useState<WorkbenchTab[]>([]);
  const [activeWorkbenchPath, setActiveWorkbenchPath] = useState<string | null>(null);
  const [workbenchLoadingPath, setWorkbenchLoadingPath] = useState<string | null>(null);
  const [workbenchWidth, setWorkbenchWidth] = useState(442);
  const [isWorkbenchResizing, setIsWorkbenchResizing] = useState(false);
  const [workbenchShowDiffByPath, setWorkbenchShowDiffByPath] = useState<Record<string, boolean>>({});
  const [workbenchDiffBaseRefByPath, setWorkbenchDiffBaseRefByPath] = useState<Record<string, string>>({});
  const [workbenchDiffByPath, setWorkbenchDiffByPath] = useState<Record<string, SessionRuntimeGitDiffResponse | null>>({});
  const [workbenchDiffErrorByPath, setWorkbenchDiffErrorByPath] = useState<Record<string, string | null>>({});
  const [workbenchDiffLoadingPath, setWorkbenchDiffLoadingPath] = useState<string | null>(null);
  const [workbenchGitRootsByPath, setWorkbenchGitRootsByPath] = useState<Record<string, SessionRuntimeGitRoot[]>>({});
  const [spawnObjective, setSpawnObjective] = useState('');
  const [spawnScope, setSpawnScope] = useState('');
  const [spawnMaxSteps, setSpawnMaxSteps] = useState(5);
  const [isSpawning, setIsSpawning] = useState(false);

  const [selectedTask, setSelectedTask] = useState<SubAgentTask | null>(null);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState(false);
  const [isSpawnModalOpen, setIsSpawnModalOpen] = useState(false);
  const [isTerminatingTask, setIsTerminatingTask] = useState(false);
  const [confirmTerminateTaskId, setConfirmTerminateTaskId] = useState<string | null>(null);

  const [liveView, setLiveView] = useState<RuntimeLiveView | null>(null);
  const [runtimeBooting, setRuntimeBooting] = useState(false);
  const isDesktopRuntimeStarting = Boolean(liveView?.enabled && !liveView.available) || runtimeBooting;
  const [debugMenuOpen, setDebugMenuOpen] = useState(false);
  const [debugEvents, setDebugEvents] = useState<SessionDebugEvent[]>([]);
  const [mode, setMode] = useState<'solo' | 'advanced'>(
      () => (localStorage.getItem('sentinel-mode') as 'solo' | 'advanced') ?? 'advanced',
  );

  const hasActiveSubAgentTasks = tasks.some((task) => task.status === 'running' || task.status === 'pending');
  const runtimeRepoChangeSections = useMemo(
    () =>
      Object.entries(runtimeRepoChangesByRoot).map(([rootPath, payload]) => ({
        id: rootPath,
        title:
          (payload?.git_root || rootPath)
            .split('/')
            .filter(Boolean)
            .pop() || rootPath || 'repo',
        tree: buildRuntimeGitChangedTree(payload),
        loading: Boolean(runtimeRepoChangesLoadingByRoot[rootPath]),
      })),
    [runtimeRepoChangesByRoot, runtimeRepoChangesLoadingByRoot],
  );

  useEffect(() => {
    localStorage.setItem('sentinel-mode', mode);
  }, [mode]);

  useEffect(() => {
    localStorage.setItem('sentinel-selected-tier', selectedTier);
  }, [selectedTier]);

  useEffect(() => {
    if (!selectedAgentMode) return;
    localStorage.setItem(AGENT_MODE_STORAGE_KEY, selectedAgentMode);
  }, [selectedAgentMode]);

  useEffect(() => {
    if (!isSessionDropdownOpen && !isEffortDropdownOpen && !isAgentModeDropdownOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (sessionDropdownRef.current?.contains(target)) return;
      if (sessionDropdownMenuRef.current?.contains(target)) return;
      if (effortDropdownRef.current?.contains(target)) return;
      if (agentModeDropdownRef.current?.contains(target)) return;
      setIsSessionDropdownOpen(false);
      setIsEffortDropdownOpen(false);
      setIsAgentModeDropdownOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [isSessionDropdownOpen, isEffortDropdownOpen, isAgentModeDropdownOpen]);

  const updateSessionDropdownRect = useCallback(() => {
    const button = sessionDropdownButtonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    setSessionDropdownRect({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - 368)),
      top: rect.bottom + 8,
      width: rect.width,
    });
  }, []);

  useEffect(() => {
    if (!isSessionDropdownOpen) return;
    updateSessionDropdownRect();
    window.addEventListener('resize', updateSessionDropdownRect);
    window.addEventListener('scroll', updateSessionDropdownRect, true);
    return () => {
      window.removeEventListener('resize', updateSessionDropdownRect);
      window.removeEventListener('scroll', updateSessionDropdownRect, true);
    };
  }, [isSessionDropdownOpen, updateSessionDropdownRect]);

  const [isCompacting, setIsCompacting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isResettingRuntime, setIsResettingRuntime] = useState(false);
  const [isRestartingContainer, setIsRestartingContainer] = useState(false);
  const [resetMenuOpen, setResetMenuOpen] = useState(false);
  const resetMenuRef = useRef<HTMLDivElement>(null);
  const [isDesktopFullscreen, setIsDesktopFullscreen] = useState(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    if (isDesktopFullscreen) {
      setResetMenuOpen(false);
      setIsSessionDropdownOpen(false);
      setIsEffortDropdownOpen(false);
      setIsAgentModeDropdownOpen(false);
    }
  }, [isDesktopFullscreen]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const shouldAutoScrollRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const autoScrollRafRef = useRef<number | null>(null);
  const autoScrollTimerShortRef = useRef<number | null>(null);
  const autoScrollTimerLongRef = useRef<number | null>(null);
  const prependScrollAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const oldestServerMessageIdRef = useRef<string | null>(null);
  const loadingOlderRef = useRef(false);
  const wsRef = useRef<WebSocket | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const sessionDropdownRef = useRef<HTMLDivElement | null>(null);
  const sessionDropdownButtonRef = useRef<HTMLButtonElement | null>(null);
  const sessionDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const effortDropdownRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownRef = useRef<HTMLDivElement | null>(null);
  const fullscreenFrameRef = useRef<HTMLIFrameElement | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const activeSessionIdRef = useRef<string | null>(routeSessionId ?? null);
  const contextUsageRequestRef = useRef(0);
  const wsInstanceRef = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const shouldRestoreComposerFocusRef = useRef(true);
  const composerFocusTimerRefs = useRef<number[]>([]);

  // Keep refs in sync so WS callbacks can read current values
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => {
    return () => {
      composerFocusTimerRefs.current.forEach((timer) => window.clearTimeout(timer));
      composerFocusTimerRefs.current = [];
    };
  }, []);
  useEffect(() => {
    setRetryCandidate(null);
    setRetryingMessageId(null);
    composerFocusTimerRefs.current.forEach((timer) => window.clearTimeout(timer));
    composerFocusTimerRefs.current = [];
    shouldRestoreComposerFocusRef.current = true;
  }, [activeSessionId]);

  const handleDesktopInteract = useCallback(() => {
    shouldRestoreComposerFocusRef.current = false;
    composerFocusTimerRefs.current.forEach((timer) => window.clearTimeout(timer));
    composerFocusTimerRefs.current = [];
  }, []);

  const handleDesktopFrameLoad = useCallback(() => {
    if (!shouldRestoreComposerFocusRef.current) return;
    if (!showDesktopView || isDesktopFullscreen) return;

    composerFocusTimerRefs.current.forEach((timer) => window.clearTimeout(timer));
    composerFocusTimerRefs.current = [];

    const restoreFocus = () => {
      if (!shouldRestoreComposerFocusRef.current) return;
      const activeElement = document.activeElement as HTMLElement | null;
      const activeTag = activeElement?.tagName;
      const canRestoreFocus =
        !activeElement ||
        activeElement === document.body ||
        activeElement === document.documentElement ||
        activeTag === 'IFRAME' ||
        activeElement === composerRef.current;

      if (!canRestoreFocus) return;
      composerRef.current?.focus({ preventScroll: true });
    };

    [0, 100, 400, 900, 1600, 2600].forEach((delay) => {
      const timer = window.setTimeout(restoreFocus, delay);
      composerFocusTimerRefs.current.push(timer);
    });
  }, [isDesktopFullscreen, showDesktopView]);

  const streamBusy =
    streaming.isThinking ||
    streaming.isStreaming ||
    streaming.isCompactingContext ||
    streaming.activeToolCalls.length > 0 ||
    streaming.agentIteration > 0 ||
    isCompacting;
  const hasPendingStreamingApproval =
    streaming.activeToolCalls.some((call) => isWaitingApproval(call.metadata)) ||
    streaming.completedToolCalls.some((call) => isWaitingApproval(call.metadata));
  const showThinkingIndicator = shouldShowThinkingIndicator(streaming, {
    streamBusy,
    hasPendingApproval: hasPendingStreamingApproval,
  });

  const activeToolPayloadChars = useMemo(
      () =>
      streaming.activeToolCalls.reduce(
          (sum, call) => sum + call.argumentsJson.length + call.outputJson.length,
          0
      ),
    [streaming.activeToolCalls]
  );
  const completedToolPayloadChars = useMemo(
      () =>
      streaming.completedToolCalls.reduce(
          (sum, call) => sum + call.argumentsJson.length + call.outputJson.length,
          0
      ),
      [streaming.completedToolCalls]
  );

  const toolCallByKey = useMemo(() => {
    const map = new Map<string, StreamingToolCall>();
    for (const call of streaming.completedToolCalls) {
      map.set(streamingCallKey(call), call);
    }
    for (const call of streaming.activeToolCalls) {
      map.set(streamingCallKey(call), call);
    }
    return map;
  }, [streaming.completedToolCalls, streaming.activeToolCalls]);

  const activeToolCallKeys = useMemo(
    () => new Set(streaming.activeToolCalls.map((call) => streamingCallKey(call))),
    [streaming.activeToolCalls]
  );

  const timelineToolKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const item of streaming.timeline) {
      if (item.kind === 'tool') keys.add(item.callKey);
    }
    return keys;
  }, [streaming.timeline]);

  const activeSession = useMemo(
      () => sessions.find((session) => session.id === activeSessionId) ?? null,
      [sessions, activeSessionId],
  );
  const activeInstance = useMemo(
    () => instances.find((instance) => instance.name === activeInstanceName) ?? null,
    [instances, activeInstanceName],
  );
  const instancePickerLabel = activeInstance?.display_name || activeInstance?.name || activeInstanceName || 'Choose instance';
  const rightRailTabs = useMemo<Array<{ id: RightRailTab; label: string }>>(
    () => {
      // Three primary tabs: Runtime (composite — Desktop/Terminals/Files),
      // Agents (sub-agent tasks), Sessions (history). The previous five-tab
      // strip is unflattened here: Desktop/Terminal/Files moved into the
      // Runtime sub-segmented control rendered below the tab strip.
      const tabs: Array<{ id: RightRailTab; label: string }> = [
        { id: 'runtime', label: 'Runtime' },
        { id: 'sub_agents', label: 'Agents' },
        { id: 'sessions', label: 'Sessions' },
      ];
      if (SESSION_DEBUG_PANEL_ENABLED) {
        tabs.push({ id: 'debug', label: 'Debug' });
      }
      return tabs;
    },
    [],
  );
  const rightRailActiveIndex = Math.max(0, rightRailTabs.findIndex((tab) => tab.id === rightRailTab));

  const workbenchVisible = workbenchTabs.length > 0;
  const activeWorkbenchTab = useMemo(() => {
    if (!workbenchTabs.length) return null;
    if (!activeWorkbenchPath) return workbenchTabs[0];
    return workbenchTabs.find((tab) => tab.path === activeWorkbenchPath) ?? workbenchTabs[0];
  }, [workbenchTabs, activeWorkbenchPath]);
  const activeWorkbenchDiff = activeWorkbenchTab ? workbenchDiffByPath[activeWorkbenchTab.path] ?? null : null;
  const activeWorkbenchDiffError = activeWorkbenchTab ? workbenchDiffErrorByPath[activeWorkbenchTab.path] ?? null : null;
  const activeWorkbenchGitRoots = activeWorkbenchTab ? workbenchGitRootsByPath[activeWorkbenchTab.path] ?? [] : [];
  const activeWorkbenchViewMode = activeWorkbenchTab && workbenchShowDiffByPath[activeWorkbenchTab.path] ? 'diff' : 'content';
  const activeWorkbenchViewerKey = activeWorkbenchTab
    ? `${activeWorkbenchTab.path}:${activeWorkbenchViewMode}`
    : 'none';
  const activeWorkbenchBaseRef = activeWorkbenchTab
    ? workbenchDiffBaseRefByPath[activeWorkbenchTab.path] ?? 'HEAD'
    : 'HEAD';
  const activeWorkbenchBaseRefOptions = useMemo(
    () => (activeWorkbenchTab ? buildRuntimeDiffBaseRefOptions(activeWorkbenchGitRoots, activeWorkbenchBaseRef) : ['HEAD']),
    [activeWorkbenchTab, activeWorkbenchGitRoots, activeWorkbenchBaseRef],
  );

  const browserToolResults = useMemo(
    () =>
      messages
        .filter(
          (message) =>
            message.role === 'tool_result' &&
            typeof message.tool_name === 'string' &&
            message.tool_name === 'browser'
        )
        .slice(-25)
        .reverse(),
    [messages],
  );

  const toolArgumentsByCallId = useMemo(() => buildToolArgumentsByCallId(messages), [messages]);

  const detectBottom = useCallback((el: HTMLDivElement) => {
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distance <= 20;
  }, []);

  const cancelScheduledAutoScroll = useCallback(() => {
    if (autoScrollRafRef.current !== null) {
      window.cancelAnimationFrame(autoScrollRafRef.current);
      autoScrollRafRef.current = null;
    }
    if (autoScrollTimerShortRef.current !== null) {
      window.clearTimeout(autoScrollTimerShortRef.current);
      autoScrollTimerShortRef.current = null;
    }
    if (autoScrollTimerLongRef.current !== null) {
      window.clearTimeout(autoScrollTimerLongRef.current);
      autoScrollTimerLongRef.current = null;
    }
  }, []);

  const stickToBottomNow = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight + 99999;
    lastScrollTopRef.current = el.scrollTop;
    shouldAutoScrollRef.current = true;
    setIsPinnedToBottom(true);
  }, []);

  const scheduleStickToBottom = useCallback(() => {
    stickToBottomNow();
    cancelScheduledAutoScroll();
    autoScrollRafRef.current = window.requestAnimationFrame(() => {
      stickToBottomNow();
      autoScrollRafRef.current = null;
    });
    // Tool cards expand with transitions; run delayed pins to land at true bottom.
    autoScrollTimerShortRef.current = window.setTimeout(() => {
      stickToBottomNow();
      autoScrollTimerShortRef.current = null;
    }, 140);
    autoScrollTimerLongRef.current = window.setTimeout(() => {
      stickToBottomNow();
      autoScrollTimerLongRef.current = null;
    }, 420);
  }, [cancelScheduledAutoScroll, stickToBottomNow]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = scrollRef.current;
    if (!el) return;
    if (behavior === 'smooth') {
      el.scrollTo({ top: el.scrollHeight + 99999, behavior: 'smooth' });
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
      lastScrollTopRef.current = el.scrollTop;
      return;
    }
    scheduleStickToBottom();
  }, [scheduleStickToBottom]);

  const onMessagesScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    const prevTop = lastScrollTopRef.current;
    const currentTop = el.scrollTop;
    const userScrolledUp = currentTop < prevTop - 2;
    lastScrollTopRef.current = currentTop;

    const atBottom = detectBottom(el);
    if (atBottom) {
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
    } else if (userScrolledUp) {
      // Only disable auto-scroll when user explicitly scrolls up.
      cancelScheduledAutoScroll();
      shouldAutoScrollRef.current = false;
      setIsPinnedToBottom(false);
    } else {
      // Keep current state for non-user drift (e.g. streaming content growth).
      setIsPinnedToBottom(shouldAutoScrollRef.current);
    }
    if (!atBottom && el.scrollTop <= 120 && hasMoreMessages && !loadingOlderRef.current) {
      void loadOlderMessages();
    }
  }, [cancelScheduledAutoScroll, detectBottom, hasMoreMessages, activeSessionId, messages.length]);

  const sessionsInTab = useMemo(() => {
    if (historyTab === 'sub_agents') {
      return sessions.filter((session) => Boolean(session.parent_session_id));
    }
    return sessions.filter((session) => !session.parent_session_id);
  }, [sessions, historyTab]);

  const filteredSessions = useMemo(() => {
    const q = sessionFilter.trim().toLowerCase();
    if (!q) return sessionsInTab;
    return sessionsInTab.filter((s) => `${s.title ?? ''}`.toLowerCase().includes(q));
  }, [sessionsInTab, sessionFilter]);

  const selectedSessionIdSet = useMemo(() => new Set(selectedSessionIds), [selectedSessionIds]);
  const selectableVisibleSessionIds = useMemo(
    () => filteredSessions
      .filter((session) => Boolean(defaultSessionId) && session.id !== defaultSessionId)
      .map((session) => session.id),
    [filteredSessions, defaultSessionId],
  );
  const allVisibleSelected = useMemo(
    () =>
      selectableVisibleSessionIds.length > 0 &&
      selectableVisibleSessionIds.every((id) => selectedSessionIdSet.has(id)),
    [selectableVisibleSessionIds, selectedSessionIdSet],
  );

  const markSessionRead = useCallback((sessionId: string) => {
    setSessions((current) =>
      current.map((s) => s.id === sessionId ? { ...s, has_unread: false } : s),
    );
    api.post(`/sessions/${sessionId}/read`, {}).catch(() => {/* best-effort */});
  }, []);

  const onInstanceClick = useCallback((instanceName: string) => {
    if (!instanceName || instanceName === activeInstanceName) return;
    navigate(`/instances/${encodeURIComponent(instanceName)}/sessions`);
  }, [activeInstanceName, navigate]);

  const onSessionClick = useCallback((id: string) => {
    const previousId = activeSessionIdRef.current;
    if (previousId) {
      markSessionRead(previousId);
    }
    setRuntimeBooting(true);
    setActiveSessionId(id);
    navigate(sessionRoute(id));
    markSessionRead(id);
  }, [markSessionRead, navigate, sessionRoute]);

  const startResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  const startWorkbenchResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsWorkbenchResizing(true);
  }, []);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
    setIsWorkbenchResizing(false);
  }, []);

  const resize = useCallback((e: MouseEvent) => {
    if (!isResizing) return;
    const workbenchReserve = workbenchVisible ? workbenchWidth : 0;
    const maxWidth = Math.max(300, window.innerWidth - workbenchReserve - 360);
    const newWidth = e.clientX;
    const clamped = Math.max(300, Math.min(maxWidth, newWidth));
    setRightPanelWidth(clamped);
  }, [isResizing, workbenchVisible, workbenchWidth]);

  const resizeWorkbench = useCallback((e: MouseEvent) => {
    if (!isWorkbenchResizing) return;
    const maxWidth = Math.max(420, window.innerWidth - rightPanelWidth - 360);
    const newWidth = e.clientX - rightPanelWidth;
    const clamped = Math.max(360, Math.min(maxWidth, newWidth));
    setWorkbenchWidth(clamped);
  }, [isWorkbenchResizing, rightPanelWidth]);

  useEffect(() => {
    if (isResizing || isWorkbenchResizing) {
      window.addEventListener('mousemove', resize);
      window.addEventListener('mousemove', resizeWorkbench);
      window.addEventListener('mouseup', stopResizing);
    }
    return () => {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mousemove', resizeWorkbench);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isResizing, isWorkbenchResizing, resize, resizeWorkbench, stopResizing]);

  // Effects
  useEffect(() => {
    if (routeSessionId && routeSessionId !== activeSessionId) {
      setRuntimeBooting(true);
      setActiveSessionId(routeSessionId);
    }
  }, [routeSessionId, activeSessionId]);

  useEffect(() => {
    setSelectedSessionIds((current) => current.filter((id) => sessions.some((session) => session.id === id)));
  }, [sessions]);

  useEffect(() => {
    if (!editingSessionId) return;
    const stillExists = sessions.some((session) => session.id === editingSessionId);
    if (!stillExists) {
      setEditingSessionId(null);
      setEditingSessionTitle('');
    }
  }, [editingSessionId, sessions]);

  useEffect(() => {
    void fetchInstances();
    void fetchSessions({ autoSelectIfEmpty: true });
    void fetchModels();
    void fetchAgentModes();
    void fetchLiveView();
  }, [activeInstanceName]);

  // Re-fetch live view when the active session changes
  useEffect(() => {
    setRuntimeBooting(true);
    void fetchLiveView();
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId || !showDesktopView) return;
    if (!runtimeBooting && liveView?.enabled && liveView.available) return;

    let cancelled = false;
    const refresh = async () => {
      if (cancelled) return;
      await fetchLiveView();
    };
    const interval = window.setInterval(() => {
      void refresh();
    }, 2000);

    void refresh();

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [activeSessionId, showDesktopView, runtimeBooting, liveView?.enabled, liveView?.available]);

  // Poll sessions every 30s to pick up unread changes
  useEffect(() => {
    const interval = setInterval(() => { void fetchSessions({ autoSelectIfEmpty: false }); }, 30_000);
    return () => clearInterval(interval);
  }, []);

  // Populate composer with first message from onboarding
  useEffect(() => {
    const state = location.state as { firstMessage?: string } | null;
    if (state?.firstMessage) {
      setComposer(state.firstMessage);
      // Clear from history so a refresh doesn't re-populate
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, []);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      setContextTokenEstimate(null);
      setContextTokenPercent(null);
      setTasks([]);
      setRuntimeFiles(null);
      setRuntimePath('');
      setRuntimeRepoChangesByRoot({});
      setRuntimeRepoChangesLoadingByRoot({});
      setRuntimeExpandedGitDirs({});
      setWorkbenchTabs([]);
      setActiveWorkbenchPath(null);
      setWorkbenchShowDiffByPath({});
      setWorkbenchDiffByPath({});
      setWorkbenchDiffErrorByPath({});
      setWorkbenchDiffBaseRefByPath({});
      setWorkbenchGitRootsByPath({});
      setStreaming(defaultStreamingState);
      shouldAutoScrollRef.current = true;
      lastScrollTopRef.current = 0;
      setIsPinnedToBottom(true);
      oldestServerMessageIdRef.current = null;
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
      disconnectWs();
      return;
    }

    // Clear messages immediately to avoid showing stale content
    setMessages([]);
    setContextTokenEstimate(null);
    setContextTokenPercent(null);
    setTasks([]);
    setRuntimeFiles(null);
    setRuntimePath('');
    setRuntimeRepoChangesByRoot({});
    setRuntimeRepoChangesLoadingByRoot({});
    setRuntimeExpandedGitDirs({});
    setWorkbenchTabs([]);
    setActiveWorkbenchPath(null);
    setWorkbenchShowDiffByPath({});
    setWorkbenchDiffByPath({});
    setWorkbenchDiffErrorByPath({});
    setWorkbenchDiffBaseRefByPath({});
    setWorkbenchGitRootsByPath({});
    setStreaming(defaultStreamingState);
    setActiveTerminals([]);
    setFocusedTerminalId(null);
    setHasMoreMessages(false);
    oldestServerMessageIdRef.current = null;
    loadingOlderRef.current = false;
    setIsLoadingOlderMessages(false);

    shouldAutoScrollRef.current = true;
    lastScrollTopRef.current = 0;
    setIsPinnedToBottom(true);
    void loadMessages(activeSessionId);
    void fetchContextUsage(activeSessionId);
    void fetchTasks(activeSessionId);
    void fetchRuntimeFiles(activeSessionId, '');
    void connectWs(activeSessionId);

    return () => {
      disconnectWs();
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId || !hasActiveSubAgentTasks) return;
    const timer = window.setInterval(() => {
      void fetchTasks(activeSessionId);
    }, 1500);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, hasActiveSubAgentTasks]);

  useEffect(() => {
    if (!activeSessionId || !showFilesView) return;
    void fetchRuntimeFiles(activeSessionId, runtimePath, {
      refreshGit: true,
      silent: false,
    });
  }, [activeSessionId, showFilesView]);

  useEffect(() => {
    if (!activeSessionId || !showFilesView) return;
    if (streaming.connection !== 'connected') return;
    const timer = window.setInterval(() => {
      void fetchRuntimeFiles(activeSessionId, runtimePath, {
        refreshGit: true,
        silent: true,
      });
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, showFilesView, runtimePath, streaming.connection]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (prependScrollAnchorRef.current) {
      const anchor = prependScrollAnchorRef.current;
      const heightDelta = el.scrollHeight - anchor.scrollHeight;
      el.scrollTop = anchor.scrollTop + heightDelta;
      prependScrollAnchorRef.current = null;
      return;
    }
    if (shouldAutoScrollRef.current) {
      scheduleStickToBottom();
    }
  }, [messages, streaming.text, streaming.timeline.length, streaming.activeToolCalls.length, streaming.completedToolCalls.length, activeToolPayloadChars, completedToolPayloadChars, scheduleStickToBottom]);

  useEffect(() => {
    return () => {
      cancelScheduledAutoScroll();
    };
  }, [cancelScheduledAutoScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!activeSessionId || messagesLoading || isLoadingOlderMessages) return;
    if (!hasMoreMessages || messages.length === 0) return;
    if (el.scrollHeight <= el.clientHeight + 12) {
      void loadOlderMessages();
    }
  }, [activeSessionId, messages, hasMoreMessages, messagesLoading, isLoadingOlderMessages]);

  // API Actions
  async function fetchSessions(options?: { autoSelectIfEmpty?: boolean }) {
    const autoSelectIfEmpty = options?.autoSelectIfEmpty ?? false;
    try {
      const [payload, defaultSession] = await Promise.all([
        api.get<SessionListResponse>('/sessions?limit=100&offset=0&include_sub_agents=true'),
        api.get<Session>('/sessions/default'),
      ]);
      setDefaultSessionId(defaultSession.id);
      const payloadItems = Array.isArray(payload?.items) ? payload.items : [];
      const exists = payloadItems.find((s) => s.id === defaultSession.id);
      const merged = (exists ? payloadItems : [defaultSession, ...payloadItems]).map((item) => ({
        ...item,
        is_main: item.id === defaultSession.id,
      }));
      setSessions(merged);
      if (autoSelectIfEmpty && !activeSessionIdRef.current) {
        setActiveSessionId(defaultSession.id);
        activeSessionIdRef.current = defaultSession.id;
        navigate(sessionRoute(defaultSession.id), { replace: true });
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load sessions');
    }
  }

  async function fetchInstances() {
    try {
      setInstances(await api.get<SentinelInstance[]>('/instances'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load instances');
    }
  }

  async function deleteSession(session: Session) {
    if (deletingSessionId) return;
    if (session.id === defaultSessionId) {
      toast.error('Main session cannot be deleted');
      return;
    }
    const label = (session.title || 'Session').trim() || 'Session';
    const confirmed = window.confirm(`Delete "${label}" and all its messages? This cannot be undone.`);
    if (!confirmed) return;

    setDeletingSessionId(session.id);
    try {
      await api.delete<{ status: string }>(`/sessions/${session.id}`);
      let remaining: Session[] = [];
      setSessions((current) => {
        remaining = current.filter((item) => item.id !== session.id);
        return remaining;
      });
      setSelectedSessionIds((current) => current.filter((id) => id !== session.id));

      if (activeSessionId === session.id) {
        const fallbackId =
          (defaultSessionId && defaultSessionId !== session.id
            ? remaining.find((item) => item.id === defaultSessionId)?.id ?? null
            : null) ?? remaining[0]?.id ?? null;
        setActiveSessionId(fallbackId);
        if (fallbackId) {
          navigate(sessionRoute(fallbackId), { replace: true });
        } else {
          navigate(sessionRoute(), { replace: true });
        }
      }
      toast.success('Session deleted');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete session');
    } finally {
      setDeletingSessionId(null);
    }
  }

  async function setMainSession(session: Session) {
    if (settingMainSessionId) return;
    setSettingMainSessionId(session.id);
    try {
      const updated = await api.post<Session>(`/sessions/${session.id}/main`, {});
      setDefaultSessionId(updated.id);
      setSessions((current) =>
        current.map((item) =>
          item.id === updated.id
            ? { ...item, ...updated, is_main: true }
            : { ...item, is_main: false },
        ),
      );
      setActiveSessionId(updated.id);
      navigate(sessionRoute(updated.id));
      toast.success('Main session updated');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to set main session');
    } finally {
      setSettingMainSessionId(null);
    }
  }

  function startRenameSession(session: Session) {
    if (renamingSessionId) return;
    setEditingSessionId(session.id);
    setEditingSessionTitle((session.title || '').trim());
  }

  function cancelRenameSession() {
    if (renamingSessionId) return;
    setEditingSessionId(null);
    setEditingSessionTitle('');
  }

  async function submitRenameSession(session: Session) {
    if (renamingSessionId) return;
    const title = editingSessionTitle.trim();
    const current = (session.title || '').trim();
    if (title === current) {
      setEditingSessionId(null);
      setEditingSessionTitle('');
      return;
    }

    setRenamingSessionId(session.id);
    try {
      const updated = await api.patch<Session>(`/sessions/${session.id}`, {
        title: title.length > 0 ? title : null,
      });
      setSessions((currentSessions) =>
        currentSessions.map((item) =>
          item.id === updated.id ? { ...item, ...updated } : item,
        ),
      );
      setEditingSessionId(null);
      setEditingSessionTitle('');
      toast.success('Session renamed');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to rename session');
    } finally {
      setRenamingSessionId(null);
    }
  }

  async function deleteSelectedSessions() {
    if (deletingSessionId) return;
    const targetIds = selectedSessionIds.filter((id) => id !== defaultSessionId);
    if (targetIds.length === 0) return;
    const confirmed = window.confirm(`Delete ${targetIds.length} selected sessions? This cannot be undone.`);
    if (!confirmed) return;

    setDeletingSessionId('bulk');
    try {
      const results = await Promise.allSettled(
        targetIds.map((id) => api.delete<{ status: string }>(`/sessions/${id}`)),
      );
      const deletedIds = targetIds.filter((_, index) => results[index]?.status === 'fulfilled');
      const failedCount = targetIds.length - deletedIds.length;

      if (deletedIds.length > 0) {
        let remaining: Session[] = [];
        setSessions((current) => {
          remaining = current.filter((session) => !deletedIds.includes(session.id));
          return remaining;
        });
        setSelectedSessionIds((current) => current.filter((id) => !deletedIds.includes(id)));

        if (activeSessionId && deletedIds.includes(activeSessionId)) {
          const fallbackId =
            (defaultSessionId && !deletedIds.includes(defaultSessionId)
              ? remaining.find((session) => session.id === defaultSessionId)?.id ?? null
              : null) ?? remaining[0]?.id ?? null;
          setActiveSessionId(fallbackId);
          if (fallbackId) {
            navigate(sessionRoute(fallbackId), { replace: true });
          } else {
            navigate(sessionRoute(), { replace: true });
          }
        }
      }

      if (failedCount === 0) {
        toast.success(`${deletedIds.length} session${deletedIds.length === 1 ? '' : 's'} deleted`);
      } else if (deletedIds.length > 0) {
        toast.error(`${failedCount} session${failedCount === 1 ? '' : 's'} could not be deleted`);
      } else {
        toast.error('Failed to delete selected sessions');
      }
    } finally {
      setDeletingSessionId(null);
    }
  }

  async function fetchModels() {
    try {
      const payload = await api.get<ModelsResponse>('/models');
      setModels(payload.models);
      if (payload.models.length === 0) return;
      const availableTiers = new Set(payload.models.map((m) => m.tier));
      const saved = parseTier(localStorage.getItem('sentinel-selected-tier'));
      if (!saved) {
        if (payload.default_tier) {
          setSelectedTier(payload.default_tier);
        }
      } else if (!availableTiers.has(saved)) {
        if (payload.default_tier) {
          setSelectedTier(payload.default_tier);
        }
      }
    } catch {
      setModels([]);
    }
  }

  async function fetchAgentModes() {
    try {
      const payload = await api.get<AgentModesResponse>('/agent-modes');
      const items = Array.isArray(payload.items) ? payload.items : [];
      setAgentModes(items);
      if (items.length === 0) {
        setSelectedAgentMode(null);
        return;
      }
      const available = new Set(items.map((item) => item.id));
      const saved = localStorage.getItem(AGENT_MODE_STORAGE_KEY);
      const defaultMode = typeof payload.default_mode === 'string' ? payload.default_mode.trim() : '';
      const selected =
        (saved && available.has(saved) ? saved : null) ??
        (defaultMode && available.has(defaultMode) ? defaultMode : null) ??
        items[0].id;
      setSelectedAgentMode(selected);
    } catch {
      setAgentModes([]);
      setSelectedAgentMode(null);
    }
  }

  async function fetchLiveView() {
    const sid = activeSessionIdRef.current;
    if (!sid) {
      setLiveView(null);
      setRuntimeBooting(false);
      return;
    }
    try {
      const payload = await api.get<RuntimeLiveView>(`/runtime/live-view?session_id=${sid}`);
      setLiveView(payload);
      if (payload.enabled && payload.available) {
        setRuntimeBooting(false);
      } else if (payload.enabled) {
        setRuntimeBooting(true);
      } else {
        setRuntimeBooting(false);
      }
    } catch {
      setLiveView(null);
    }
  }

  async function resetRuntime() {
    if (isResettingRuntime) return;
    const sid = activeSessionIdRef.current;
    if (!sid) return;
    setIsResettingRuntime(true);
    try {
      await api.post(`/runtime/reset?session_id=${sid}`, {});
      toast.success('Runtime reset successful');
      await fetchLiveView();
    } catch {
      toast.error('Failed to reset runtime');
    } finally {
      setIsResettingRuntime(false);
    }
  }

  async function restartContainer() {
    if (isRestartingContainer) return;
    const sid = activeSessionIdRef.current;
    if (!sid) return;
    setIsRestartingContainer(true);
    setRuntimeBooting(true);
    setLiveView(null);
    try {
      await api.post(`/runtime/restart-container?session_id=${sid}`, {});
      toast.success('Container restarting...');
    } catch {
      toast.error('Failed to restart container');
      setRuntimeBooting(false);
    } finally {
      setIsRestartingContainer(false);
    }
  }

  async function resetSession() {
    try {
      const previousId = activeSessionId;
      // Disconnect WS and wipe messages before the API call to prevent stale events polluting the new session
      disconnectWs();
      activeSessionIdRef.current = null;
      setMessages([]);
      setStreaming(defaultStreamingState);
      const fresh = await api.post<Session>('/sessions/default/reset', {});
      setSessions((current) => {
        const merged = [fresh, ...current.filter((s) => s.id !== fresh.id)];
        return merged.map((item) => ({ ...item, is_main: item.id === fresh.id }));
      });
      setActiveSessionId(fresh.id);
      setRuntimeBooting(true);
      setLiveView(null);
      navigate(sessionRoute(fresh.id), { replace: true });
      toast.success('New session started. Memories preserved.');
    } catch {
      toast.error('Failed to reset session');
    }
  }

  async function fetchContextUsage(sessionId: string) {
    const requestId = ++contextUsageRequestRef.current;
    try {
      const payload = await api.get<SessionContextUsage>(`/sessions/${sessionId}/context-usage`);
      if (sessionId !== activeSessionIdRef.current || requestId !== contextUsageRequestRef.current) {
        return;
      }
      if (typeof payload.context_token_budget === 'number' && Number.isFinite(payload.context_token_budget) && payload.context_token_budget > 0) {
        setContextTokenBudget(Math.floor(payload.context_token_budget));
      }
      if (typeof payload.estimated_context_tokens === 'number' && Number.isFinite(payload.estimated_context_tokens) && payload.estimated_context_tokens >= 0) {
        setContextTokenEstimate(Math.floor(payload.estimated_context_tokens));
      } else {
        setContextTokenEstimate(null);
      }
      if (typeof payload.estimated_context_percent === 'number' && Number.isFinite(payload.estimated_context_percent) && payload.estimated_context_percent >= 0) {
        setContextTokenPercent(Math.max(0, Math.min(100, Math.floor(payload.estimated_context_percent))));
      } else {
        setContextTokenPercent(null);
      }
    } catch {
      if (sessionId !== activeSessionIdRef.current || requestId !== contextUsageRequestRef.current) {
        return;
      }
      // Keep last-known values if this lightweight endpoint fails.
    }
  }

  async function loadMessages(sessionId: string, beforeMessageId?: string) {
    if (!beforeMessageId) setMessagesLoading(true);
    try {
      const path = beforeMessageId
          ? `/sessions/${sessionId}/messages?limit=50&before=${encodeURIComponent(beforeMessageId)}`
          : `/sessions/${sessionId}/messages?limit=50`;
      const payload = await api.get<MessageListResponse>(path);
      const payloadItems = Array.isArray(payload?.items) ? payload.items : [];
      const fetched = sortMessages(payloadItems);
      const serverHasUnresolvedToolCalls = hasUnresolvedToolCalls(fetched);
      setHasMoreMessages(Boolean(payload?.has_more));
      if (fetched.length > 0) {
        oldestServerMessageIdRef.current = fetched[0].id;
      } else if (!beforeMessageId) {
        oldestServerMessageIdRef.current = null;
      }

      setMessages((current) => {
        let next;
        if (!beforeMessageId) {
          next = fetched;
        } else {
          const merged = new Map<string, Message>();
          [...fetched, ...current].forEach((item) => merged.set(item.id, item));
          next = sortMessages([...merged.values()]);
        }
        return next;
      });

      // CRITICAL: Only clear streaming UI state AFTER the official messages are loaded.
      // This prevents the "flash" where tool calls disappear before the API responds.
      if (!beforeMessageId) {
        setStreaming((prev) => {
          const hasPendingCallCard = [...prev.activeToolCalls, ...prev.completedToolCalls].some(
            (call) => isWaitingApproval(call.metadata),
          );
          if (hasPendingCallCard && serverHasUnresolvedToolCalls) {
            return {
              ...prev,
              isThinking: false,
              isStreaming: false,
              isCompactingContext: false,
              agentIteration: 0,
              agentMaxIterations: 0,
            };
          }
          return {
            ...prev,
            text: '',
            timeline: [],
            interimTextSeq: 0,
            activeToolCalls: [],
            completedToolCalls: [],
            isThinking: false,
            isStreaming: false,
            isCompactingContext: false,
            agentIteration: 0,
            agentMaxIterations: 0,
          };
        });
      }
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : 'Failed to load messages';
      if (beforeMessageId && errMsg.toLowerCase().includes('message not found')) {
        setHasMoreMessages(false);
        oldestServerMessageIdRef.current = null;
        return;
      }
      toast.error(error instanceof Error ? error.message : 'Failed to load messages');
    } finally {
      if (!beforeMessageId) setMessagesLoading(false);
    }
  }

  async function loadOlderMessages() {
    if (!activeSessionId || !hasMoreMessages || messages.length === 0 || messagesLoading || loadingOlderRef.current) return;
    const beforeId = oldestServerMessageIdRef.current;
    if (!beforeId) return;
    loadingOlderRef.current = true;
    setIsLoadingOlderMessages(true);
    const el = scrollRef.current;
    const preservePinnedToBottom = el ? detectBottom(el) : shouldAutoScrollRef.current;
    if (el) {
      prependScrollAnchorRef.current = { scrollHeight: el.scrollHeight, scrollTop: el.scrollTop };
    }
    shouldAutoScrollRef.current = preservePinnedToBottom;
    setIsPinnedToBottom(preservePinnedToBottom);
    try {
      await loadMessages(activeSessionId, beforeId);
    } finally {
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
    }
  }

  async function fetchTasks(sessionId: string) {
    setTasksLoading(true);
    try {
      const payload = await api.get<SubAgentTaskListResponse>(`/sessions/${sessionId}/sub-agents`);
      setTasks(Array.isArray(payload?.items) ? payload.items : []);
    } catch { /* ignore polling errors */ }
    finally { setTasksLoading(false); }
  }

  async function fetchRuntimeFiles(
    sessionId: string,
    path = '',
    options?: { refreshGit?: boolean; silent?: boolean },
  ) {
    const silent = Boolean(options?.silent);
    if (!silent) {
      setRuntimeFilesLoading(true);
    }
    try {
      const query = new URLSearchParams();
      if (path.trim().length > 0) query.set('path', path.trim());
      query.set('limit', '400');
      const suffix = query.toString();
      const payload = await api.get<SessionRuntimeFilesResponse>(`/sessions/${sessionId}/runtime/files${suffix ? `?${suffix}` : ''}`);
      if (sessionId !== activeSessionIdRef.current) return;
      setRuntimeFiles(payload);
      setRuntimePath(payload.path || '');
      if (options?.refreshGit ?? showFilesView) {
        Object.keys(runtimeRepoChangesByRoot).forEach((rootPath) => {
          void fetchRuntimeChangedFilesForRepo(sessionId, rootPath);
        });
      }
    } catch (err) {
      if (sessionId !== activeSessionIdRef.current) return;
      // Directory no longer exists — walk up to the nearest valid parent
      if ((err as { status?: number }).status === 404 && path) {
        const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
        void fetchRuntimeFiles(sessionId, parent, options);
        return;
      }
      // Preserve the last successful explorer tree on transient refresh failures.
    } finally {
      if (sessionId === activeSessionIdRef.current && !silent) {
        setRuntimeFilesLoading(false);
      }
    }
  }

  async function loadRuntimeDirectoryEntries(path: string): Promise<SessionRuntimeFileEntry[]> {
    if (!activeSessionId) return [];
    const query = new URLSearchParams();
    if (path.trim().length > 0) query.set('path', path.trim());
    query.set('limit', '400');
    const suffix = query.toString();
    const payload = await api.get<SessionRuntimeFilesResponse>(
      `/sessions/${activeSessionId}/runtime/files${suffix ? `?${suffix}` : ''}`,
    );
    if (activeSessionId !== activeSessionIdRef.current) return [];
    return Array.isArray(payload?.entries) ? payload.entries : [];
  }

  async function downloadRuntimeEntry(entry: SessionRuntimeFileEntry) {
    if (!activeSessionId) return;
    try {
      const { blob, filename } = await api.download(
        `/sessions/${activeSessionId}/runtime/download?path=${encodeURIComponent(entry.path)}`,
        { timeoutMs: 120_000 },
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename || (entry.kind === 'directory' ? `${entry.name}.zip` : entry.name);
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch {
      toast.error(entry.kind === 'directory' ? 'Failed to download folder zip' : 'Failed to download file');
    }
  }

  async function fetchRuntimeChangedFilesForRepo(
    sessionId: string,
    path: string,
  ): Promise<SessionRuntimeGitChangedFilesResponse | null> {
    setRuntimeRepoChangesLoadingByRoot((current) => ({ ...current, [path]: true }));
    try {
      const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
        `/sessions/${sessionId}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (sessionId !== activeSessionIdRef.current) return null;
      setRuntimeRepoChangesByRoot((current) => ({ ...current, [path]: payload }));
      return payload;
    } catch {
      if (sessionId !== activeSessionIdRef.current) return null;
      setRuntimeRepoChangesByRoot((current) => ({ ...current, [path]: null }));
      return null;
    } finally {
      if (sessionId === activeSessionIdRef.current) {
        setRuntimeRepoChangesLoadingByRoot((current) => ({ ...current, [path]: false }));
      }
    }
  }

  async function openRuntimeDirectory(
    path: string,
    options?: { autoOpenFirstDiff?: boolean },
  ) {
    if (!activeSessionId) return;
    const shouldAutoOpenFirstDiff = Boolean(options?.autoOpenFirstDiff);
    await fetchRuntimeFiles(activeSessionId, path, {
      refreshGit: !shouldAutoOpenFirstDiff,
    });
    if (!shouldAutoOpenFirstDiff) return;
    const changed = await fetchRuntimeChangedFilesForRepo(activeSessionId, path);
    const firstPath = changed?.entries?.[0]?.path;
    if (!firstPath) return;
    await openRuntimeFileDiff(firstPath);
  }

  async function openRuntimeFile(
    path: string,
    options?: { suppressErrorToast?: boolean },
  ): Promise<boolean> {
    if (!activeSessionId) return false;
    setWorkbenchLoadingPath(path);
    try {
      const payload = await api.get<SessionRuntimeFilePreviewResponse>(
        `/sessions/${activeSessionId}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
      );
      if (activeSessionId !== activeSessionIdRef.current) return false;
      const nextTab: WorkbenchTab = {
        path: payload.path,
        name: payload.name,
        size_bytes: payload.size_bytes,
        modified_at: payload.modified_at,
        content: payload.content,
        truncated: payload.truncated,
        max_bytes: payload.max_bytes,
      };
      setWorkbenchTabs((current) => {
        const existing = current.find((tab) => tab.path === nextTab.path);
        if (existing) {
          return current.map((tab) => (tab.path === nextTab.path ? nextTab : tab));
        }
        return [...current, nextTab];
      });
      setActiveWorkbenchPath(nextTab.path);
      setWorkbenchDiffBaseRefByPath((current) =>
        current[nextTab.path] ? current : { ...current, [nextTab.path]: 'HEAD' },
      );
      setWorkbenchDiffErrorByPath((current) => ({ ...current, [nextTab.path]: null }));
      setWorkbenchShowDiffByPath((current) =>
        Object.prototype.hasOwnProperty.call(current, nextTab.path)
          ? current
          : { ...current, [nextTab.path]: false },
      );
      void fetchRuntimeGitRoots(activeSessionId, nextTab.path);
      return true;
    } catch {
      if (!options?.suppressErrorToast) {
        toast.error('Failed to open runtime file');
      }
      return false;
    } finally {
      if (activeSessionId === activeSessionIdRef.current) {
        setWorkbenchLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  function ensureWorkbenchTab(path: string) {
    const name = path.split('/').pop() || path;
    setWorkbenchTabs((current) => {
      if (current.some((tab) => tab.path === path)) return current;
      return [
        ...current,
        {
          path,
          name,
          size_bytes: 0,
          modified_at: null,
          content: '',
          truncated: false,
          max_bytes: 0,
        },
      ];
    });
    setActiveWorkbenchPath(path);
    setWorkbenchDiffBaseRefByPath((current) =>
      current[path] ? current : { ...current, [path]: 'HEAD' },
    );
    setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: null }));
  }

  async function openRuntimeFileDiff(path: string) {
    if (!activeSessionId) return;
    const opened = await openRuntimeFile(path, { suppressErrorToast: true });
    if (!opened) {
      ensureWorkbenchTab(path);
    }
    setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
    void fetchRuntimeGitDiff(activeSessionId, path);
  }

  function closeWorkbenchTab(path: string) {
    setWorkbenchTabs((current) => {
      const targetIndex = current.findIndex((tab) => tab.path === path);
      const next = current.filter((tab) => tab.path !== path);
      setActiveWorkbenchPath((previous) => {
        if (previous !== path) return previous;
        if (!next.length) return null;
        const fallbackIndex = Math.min(Math.max(targetIndex - 1, 0), next.length - 1);
        return next[fallbackIndex]?.path ?? next[next.length - 1].path;
      });
      return next;
    });
    setWorkbenchShowDiffByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffErrorByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffBaseRefByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchGitRootsByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchLoadingPath((current) => (current === path ? null : current));
    setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
  }

  async function fetchRuntimeGitRoots(sessionId: string, path: string) {
    try {
      const payload = await api.get<SessionRuntimeGitRootsResponse>(
        `/sessions/${sessionId}/runtime/git/roots?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (sessionId !== activeSessionIdRef.current) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: payload.roots || [] }));
    } catch {
      if (sessionId !== activeSessionIdRef.current) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: [] }));
    }
  }

  async function fetchRuntimeGitDiff(
    sessionId: string,
    path: string,
    options?: { baseRef?: string },
  ) {
    const baseRefRaw = options?.baseRef ?? workbenchDiffBaseRefByPath[path];
    const baseRef = (typeof baseRefRaw === 'string' && baseRefRaw.trim().length > 0) ? baseRefRaw.trim() : 'HEAD';
    setWorkbenchDiffLoadingPath(path);
    setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: null }));
    try {
      const query = new URLSearchParams();
      query.set('path', path);
      query.set('base_ref', baseRef);
      query.set('staged', 'false');
      query.set('context_lines', '3');
      query.set('max_bytes', '120000');
      const payload = await api.get<SessionRuntimeGitDiffResponse>(
        `/sessions/${sessionId}/runtime/git/diff?${query.toString()}`,
      );
      if (sessionId !== activeSessionIdRef.current) return;
      setWorkbenchDiffByPath((current) => ({ ...current, [path]: payload }));
      setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
      if (!workbenchGitRootsByPath[path]?.length) {
        void fetchRuntimeGitRoots(sessionId, path);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Failed to load git diff';
      setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: detail }));
    } finally {
      if (sessionId === activeSessionIdRef.current) {
        setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  async function terminateTask(taskId: string) {
    if (!activeSessionId || isTerminatingTask) return;
    setConfirmTerminateTaskId(taskId);
  }

  async function confirmTerminate() {
    const taskId = confirmTerminateTaskId;
    if (!taskId || !activeSessionId) return;
    setConfirmTerminateTaskId(null);

    setIsTerminatingTask(true);
    try {
      await api.delete(`/sessions/${activeSessionId}/sub-agents/${taskId}`);
      toast.success('Sub-agent task terminated');
      setIsTaskModalOpen(false);
      void fetchTasks(activeSessionId);
    } catch {
      toast.error('Failed to terminate sub-agent task');
    } finally {
      setIsTerminatingTask(false);
    }
  }

  async function spawnSubAgent(name: string, scope: string, maxSteps: number) {
    if (!activeSessionId || isSpawning) return;

    setIsSpawning(true);
    try {
      await api.post(`/sessions/${activeSessionId}/sub-agents`, {
        name,
        scope,
        max_steps: maxSteps,
        allowed_tools: [], // empty allowlist = full tool access for sub-agent
      });
      toast.success('Sub-agent node initialized');
      setIsSpawnModalOpen(false);
      void fetchTasks(activeSessionId);
    } catch {
      toast.error('Failed to spawn sub-agent');
    } finally {
      setIsSpawning(false);
    }
  }

  const updateStreamingCallApproval = useCallback((
    approval: ApprovalRef,
    updates: { pending?: boolean; approval_status?: string; decision_note?: string },
  ) => {
    approvalDebugLog('ui.approval.update_streaming_call', {
      provider: approval.provider,
      approval_id: approval.approvalId,
      pending: updates.pending,
      approval_status: updates.approval_status,
    });
    const targetKey = approvalKey(approval);
    setStreaming((current) => {
      const patchCall = (call: StreamingToolCall): StreamingToolCall => {
        const callApproval = approvalRefFromMetadata(call.metadata);
        if (!callApproval || approvalKey(callApproval) !== targetKey) return call;
        const nextPending = updates.pending ?? callApproval.pending;
        const nextStatus = updates.approval_status ?? callApproval.status;
        const currentApproval = isObjectRecord(call.metadata.approval) ? call.metadata.approval : {};
        return {
          ...call,
          metadata: {
            ...call.metadata,
            pending: nextPending,
            approval_status: nextStatus,
            ...updates,
            approval: {
              ...currentApproval,
              provider: approval.provider,
              approval_id: approval.approvalId,
              pending: nextPending,
              status: nextStatus,
              can_resolve: nextPending,
              decision_note: updates.decision_note ?? currentApproval.decision_note,
            },
          },
        };
      };
      return {
        ...current,
        activeToolCalls: current.activeToolCalls.map(patchCall),
        completedToolCalls: current.completedToolCalls.map(patchCall),
      };
    });
  }, []);

  const updatePersistedMessageApproval = useCallback((
    approval: ApprovalRef,
    updates: { pending?: boolean; approval_status?: string; decision_note?: string },
  ) => {
    const targetKey = approvalKey(approval);
    setMessages((current) =>
      current.map((message) => {
        if (message.role !== 'tool_result') return message;
        const metadata = isObjectRecord(message.metadata) ? message.metadata : {};
        const messageApproval = approvalRefFromMetadata(metadata);
        if (!messageApproval || approvalKey(messageApproval) !== targetKey) return message;
        const nextPending = updates.pending ?? messageApproval.pending;
        const nextStatus = updates.approval_status ?? messageApproval.status;
        const currentApproval = isObjectRecord(metadata.approval) ? metadata.approval : {};
        return {
          ...message,
          metadata: {
            ...metadata,
            pending: nextPending,
            approval_status: nextStatus,
            ...updates,
            approval: {
              ...currentApproval,
              provider: approval.provider,
              approval_id: approval.approvalId,
              pending: nextPending,
              status: nextStatus,
              can_resolve: nextPending,
              decision_note: updates.decision_note ?? currentApproval.decision_note,
            },
          },
        };
      }),
    );
  }, []);

  const resolveApprovalInline = useCallback(async (approval: ApprovalRef, decision: 'approve' | 'reject') => {
    const targetKey = approvalKey(approval);
    setResolvingApprovalKey(targetKey);
    approvalDebugLog('ui.approval.resolve.request', {
      provider: approval.provider,
      approval_id: approval.approvalId,
      decision,
    });
    try {
      await api.post(`/approvals/${encodeURIComponent(approval.provider)}/${encodeURIComponent(approval.approvalId)}/${decision}`, {
        note: decision === 'approve' ? 'User approved action.' : 'User rejected action.',
      });
      updateStreamingCallApproval(approval, {
        pending: false,
        approval_status: decision === 'approve' ? 'approved' : 'rejected',
      });
      updatePersistedMessageApproval(approval, {
        pending: false,
        approval_status: decision === 'approve' ? 'approved' : 'rejected',
      });
      toast.success(decision === 'approve' ? 'Approval approved' : 'Approval rejected');
      approvalDebugLog('ui.approval.resolve.success', {
        provider: approval.provider,
        approval_id: approval.approvalId,
        decision,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const resolvedMatch = message.match(/already resolved with status '([^']+)'/i);
      if (resolvedMatch) {
        const resolvedStatus = resolvedMatch[1].trim().toLowerCase();
        updateStreamingCallApproval(approval, {
          pending: false,
          approval_status: resolvedStatus,
        });
        updatePersistedMessageApproval(approval, {
          pending: false,
          approval_status: resolvedStatus,
        });
      }
      approvalDebugLog('ui.approval.resolve.error', {
        provider: approval.provider,
        approval_id: approval.approvalId,
        decision,
        error: message,
      });
      toast.error(message || 'Failed to resolve approval');
    } finally {
      setResolvingApprovalKey(null);
    }
  }, [updatePersistedMessageApproval, updateStreamingCallApproval]);

  // WebSocket Logic
  function disconnectWs() {
    intentionalCloseRef.current = true;
    wsInstanceRef.current += 1;
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setStreaming((current) => ({ ...current, connection: 'disconnected' }));
  }

  async function connectWs(sessionId: string) {
    disconnectWs();
    intentionalCloseRef.current = false;

    setStreaming((current) => ({
      ...current,
      connection: reconnectAttemptsRef.current > 0 ? 'reconnecting' : 'connecting',
    }));

    const instanceId = ++wsInstanceRef.current;
    if (!activeInstanceName) {
      return;
    }
    const ws = new WebSocket(`${wsSessionsBaseUrl(activeInstanceName)}/${sessionId}/stream`);
    wsRef.current = ws;

    ws.onopen = () => {
      if (instanceId !== wsInstanceRef.current || ws !== wsRef.current) return;
      reconnectAttemptsRef.current = 0;
      setStreaming((current) => ({ ...current, connection: 'connected' }));
    };

    ws.onmessage = (event) => {
      if (instanceId !== wsInstanceRef.current || ws !== wsRef.current) return;
      try {
        const payload = JSON.parse(event.data) as WsEvent;
        onStreamEvent(sessionId, payload);
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      if (instanceId !== wsInstanceRef.current || ws !== wsRef.current) return;
      wsRef.current = null;
      if (!intentionalCloseRef.current) scheduleReconnect(sessionId);
    };
  }

  function scheduleReconnect(sessionId: string) {
    reconnectAttemptsRef.current += 1;
    if (reconnectAttemptsRef.current > 8) {
      toast.error('Realtime stream disconnected');
      return;
    }
    const delay = Math.min(2 ** reconnectAttemptsRef.current, 20);
    reconnectTimerRef.current = window.setTimeout(() => connectWs(sessionId), delay * 1000);
  }

  function markLatestUserMessageRetryable(rawError: string) {
    const latestUserMessage = [...messagesRef.current].reverse().find((message) => message.role === 'user');
    if (!latestUserMessage) return;
    setMessages((current) =>
      current.map((message) => (
        message.id === latestUserMessage.id
          ? {
              ...message,
              metadata: {
                ...(isObjectRecord(message.metadata) ? message.metadata : {}),
                retryable_error: humanizeAgentError(rawError),
              },
            }
          : message
      ))
    );
    setRetryCandidate({
      messageId: latestUserMessage.id,
      error: humanizeAgentError(rawError),
    });
  }

  async function retryFailedMessage(message: Message) {
    if (!activeSessionId) return;
    const fallbackError = retryCandidate?.messageId === message.id ? retryCandidate.error : 'Retry failed';
    setRetryingMessageId(message.id);
    setRetryCandidate(null);
    setMessages((current) =>
      current.map((item) => (
        item.id === message.id
          ? {
              ...item,
              metadata: {
                ...(isObjectRecord(item.metadata) ? item.metadata : {}),
              },
            }
          : item
      )).map((item) => {
        if (item.id !== message.id) return item;
        const metadata = { ...(isObjectRecord(item.metadata) ? item.metadata : {}) };
        delete metadata.retryable_error;
        return { ...item, metadata };
      })
    );
    try {
      await api.post<{ status: string }>(`/sessions/${activeSessionId}/messages/${message.id}/retry`, {});
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
    } catch (error) {
      const detail = error instanceof Error ? error.message : fallbackError;
      setRetryCandidate({
        messageId: message.id,
        error: detail || fallbackError,
      });
      toast.error(detail || fallbackError);
    } finally {
      setRetryingMessageId((current) => (current === message.id ? null : current));
    }
  }

  function pushDebugEvent(event: WsEvent) {
    if (!SESSION_DEBUG_PANEL_ENABLED) return;
    const summaryParts: string[] = [];
    if (typeof event.iteration === 'number') summaryParts.push(`iteration=${event.iteration}`);
    if (typeof event.max_iterations === 'number') summaryParts.push(`max=${event.max_iterations}`);
    if (typeof event.stop_reason === 'string' && event.stop_reason.trim()) summaryParts.push(`stop=${event.stop_reason}`);
    if (typeof event.delta === 'string' && event.delta.trim()) summaryParts.push(`delta=${JSON.stringify(truncate(event.delta.trim(), 80))}`);

    const toolCall = (event.tool_call && typeof event.tool_call === 'object')
      ? event.tool_call as Record<string, unknown>
      : null;
    if (toolCall && typeof toolCall.name === 'string') {
      const toolId = typeof toolCall.id === 'string' ? toolCall.id : '';
      summaryParts.push(`tool=${toolCall.name}${toolId ? `:${toolId}` : ''}`);
    }

    const toolResult = (event.tool_result && typeof event.tool_result === 'object')
      ? event.tool_result as Record<string, unknown>
      : null;
    if (toolResult && typeof toolResult.tool_name === 'string') {
      const toolId = typeof toolResult.tool_call_id === 'string' ? toolResult.tool_call_id : '';
      const content = typeof toolResult.content === 'string' ? truncate(toolResult.content.trim(), 80) : '';
      summaryParts.push(`result=${toolResult.tool_name}${toolId ? `:${toolId}` : ''}`);
      if (content) summaryParts.push(`content=${JSON.stringify(content)}`);
      if (toolResult.is_error === true) summaryParts.push('error=true');
    }

    if (typeof event.content_index === 'number') summaryParts.push(`content_index=${event.content_index}`);

    setDebugEvents((current) => [
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        at: new Date().toISOString(),
        type: event.type,
        summary: summaryParts.join(' '),
      },
      ...current,
    ].slice(0, 100));
  }

  function onStreamEvent(sessionId: string, event: WsEvent) {
    // Drop events from stale WS connections (e.g. fired after session reset)
    if (sessionId !== activeSessionIdRef.current) return;
    pushDebugEvent(event);

    switch (event.type) {
      case 'connected':
        if (typeof event.context_token_budget === 'number' && Number.isFinite(event.context_token_budget) && event.context_token_budget > 0) {
          setContextTokenBudget(Math.floor(event.context_token_budget));
        }
        void fetchContextUsage(sessionId);
        setMessages((current) => {
          const incoming = sortMessages((event.history as Message[]) ?? []);
          const merged = new Map<string, Message>();
          [...current, ...incoming].forEach((item) => merged.set(item.id, item));
          const next = sortMessages([...merged.values()]);
          oldestServerMessageIdRef.current = next.length > 0 ? next[0].id : null;
          return next;
        });
        if (Array.isArray(event.terminals)) {
          // Initial pill population: server may have terminals alive across a
          // reload or backend restart. We replace state entirely rather than
          // merge so stale terminals from the previous session don't linger.
          const incomingTerminals = event.terminals
            .map((raw) => {
              if (!raw || typeof raw !== 'object') return null;
              const value = raw as Record<string, unknown>;
              const id = typeof value.terminal_id === 'string' ? value.terminal_id : null;
              if (!id) return null;
              return {
                id,
                label: typeof value.label === 'string' ? value.label : null,
                createdBy: value.created_by === 'user' ? 'user' : 'agent',
                createdAt: typeof value.created_at === 'number' ? value.created_at : Date.now() / 1000,
                auto: Boolean(value.auto),
                busy: false,
                lastCommand: typeof value.last_command === 'string' ? value.last_command : null,
              } as ActiveTerminal;
            })
            .filter((item): item is ActiveTerminal => item !== null);
          setActiveTerminals(incomingTerminals);
        }
        break;
      case 'terminal_opened': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        const label = typeof event.label === 'string' ? event.label : null;
        const createdBy = event.created_by === 'user' ? 'user' : 'agent';
        const auto = Boolean(event.auto);
        // Capture "was the pill row empty before this event?" before we mutate
        // it. We auto-focus the Terminals sub-view only on the FIRST terminal
        // of a session, so the user isn't yanked off Files/Desktop every time
        // the agent spins up another shell.
        let wasFirstTerminal = false;
        setActiveTerminals((current) => {
          if (current.some((t) => t.id === id)) {
            return current.map((t) => (t.id === id ? { ...t, label: t.label || label } : t));
          }
          wasFirstTerminal = current.length === 0;
          return [
            ...current,
            { id, label, createdBy, createdAt: Date.now() / 1000, auto, busy: false, lastCommand: null },
          ];
        });
        // Auto-focus rule: only flip to the Terminals sub-view if we're
        // already on the Runtime tab AND this is the first terminal AND the
        // user hasn't manually picked a sub-view in the last 4s. Anything
        // else would be surprise UI motion. Cross-tab activity (Agents /
        // Sessions) never steals focus.
        if (
          wasFirstTerminal
          && rightRailTab === 'runtime'
          && Date.now() - lastRuntimeViewIntentRef.current > 4_000
        ) {
          setRuntimeView('terminals');
        }
        break;
      }
      case 'terminal_closed': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        setActiveTerminals((current) => current.filter((t) => t.id !== id));
        setFocusedTerminalId((current) => (current === id ? null : current));
        break;
      }
      case 'terminal_busy': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        const busy = Boolean(event.busy);
        // The same event carries the most-recent command/cwd whenever the
        // backend has them — that's how the pill tooltip + rail header stay
        // in sync with what the terminal is actually doing.
        const lastCommand = typeof event.last_command === 'string' ? event.last_command : undefined;
        setActiveTerminals((current) =>
          current.map((t) =>
            t.id === id
              ? { ...t, busy, lastCommand: lastCommand !== undefined ? lastCommand : t.lastCommand }
              : t,
          ),
        );
        break;
      }
      case 'message_ack':
        setMessages((current) => {
          const messageId = (event.message_id as string | undefined)?.trim();
          if (!messageId) return current;
          if (current.some((item) => item.id === messageId)) return current;
          const createdAt = (event.created_at as string | undefined) || new Date().toISOString();
          const metadata = isObjectRecord(event.metadata) ? event.metadata : { source: 'web' };
          const ackMessage: Message = {
            id: messageId,
            session_id: sessionId,
            role: 'user',
            content: (event.content as string) || '',
            metadata,
            token_count: null,
            tool_call_id: null,
            tool_name: null,
            created_at: createdAt,
          };
          return sortMessages([...current, ackMessage]);
        });
        break;
      case 'agent_thinking':
        setRetryCandidate(null);
        setStreaming((current) => ({
          ...current,
          isThinking: true,
          text: '',
          timeline: [],
          interimTextSeq: 0,
          activeToolCalls: [],
          completedToolCalls: [],
          agentIteration: 0,
          agentMaxIterations: 0,
        }));
        break;
      case 'agent_progress':
        setStreaming((current) => {
          const shouldShowThinking =
            current.activeToolCalls.length === 0 &&
            !current.isStreaming &&
            current.text.trim().length === 0;
          return {
            ...current,
            agentIteration: (event.iteration as number) ?? current.agentIteration,
            agentMaxIterations: (event.max_iterations as number) ?? current.agentMaxIterations,
            isThinking: shouldShowThinking ? true : current.isThinking,
          };
        });
        break;
      case 'start':
        setStreaming((current) => ({
          ...current,
          isThinking:
            current.activeToolCalls.length === 0 &&
            !current.isStreaming &&
            current.text.trim().length === 0
              ? true
              : current.isThinking,
        }));
        break;
      case 'thinking_start':
        setRetryCandidate(null);
        setStreaming((current) => ({
          ...current,
          isThinking: true,
          isStreaming: false,
        }));
        break;
      case 'thinking_delta':
        setRetryCandidate(null);
        setStreaming((current) => ({
          ...current,
          isThinking: true,
          isStreaming: false,
        }));
        break;
      case 'thinking_end':
        setStreaming((current) => ({
          ...current,
          isThinking: false,
        }));
        break;
      case 'text_delta':
        setRetryCandidate(null);
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: true, text: current.text + (event.delta ?? '') }));
        break;
      case 'toolcall_start':
        setRetryCandidate(null);
        {
          const callId = String((event.tool_call as any)?.id ?? `tool-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
          const contentIndex = typeof event.content_index === 'number' ? event.content_index : null;
          const rawInitialArguments = serializeToolArguments((event.tool_call as any)?.arguments);
          const initialArguments = hasMeaningfulToolArguments(rawInitialArguments)
            ? rawInitialArguments
            : '';
          const toolName = String((event.tool_call as any)?.name ?? 'unknown');
          approvalDebugLog('ws.toolcall_start', {
            session_id: sessionId,
            tool_call_id: callId,
            tool_name: toolName,
            content_index: contentIndex,
          });
          setStreaming((current) => {
            const call = {
              id: callId,
              name: toolName,
              argumentsJson: initialArguments,
              outputJson: '',
              isError: false,
              metadata: {},
              complete: false,
              contentIndex,
            };
            const existingByExactId = current.activeToolCalls.findIndex(
              (item) => item.id === callId && item.contentIndex === contentIndex,
            );
            if (existingByExactId >= 0) {
              return { ...current, isThinking: false };
            }
            const existingByContentIndex = contentIndex === null
              ? -1
              : current.activeToolCalls.findIndex((item) => item.contentIndex === contentIndex);
            if (existingByContentIndex >= 0) {
              const nextActive = [...current.activeToolCalls];
              const existing = nextActive[existingByContentIndex];
              const adoptIncomingId = isSyntheticToolCallId(existing.id) && !isSyntheticToolCallId(call.id);
              const mergedCall: StreamingToolCall = {
                ...existing,
                id: adoptIncomingId ? call.id : existing.id,
                name: existing.name === 'unknown' && call.name !== 'unknown' ? call.name : existing.name,
                argumentsJson: hasMeaningfulToolArguments(existing.argumentsJson)
                  ? existing.argumentsJson
                  : call.argumentsJson,
              };
              let nextTimeline = current.timeline;
              const oldKey = streamingCallKey(existing);
              const newKey = streamingCallKey(mergedCall);
              if (oldKey !== newKey) {
                nextTimeline = current.timeline.map((item) => (
                  item.kind === 'tool' && item.callKey === oldKey
                    ? { kind: 'tool', key: `tool-${newKey}`, callKey: newKey }
                    : item
                ));
              }
              nextActive[existingByContentIndex] = mergedCall;
              return {
                ...current,
                isThinking: false,
                activeToolCalls: nextActive,
                timeline: nextTimeline,
              };
            }
            const callKey = streamingCallKeyFromParts(callId, contentIndex);
            const hasTimelineItem = current.timeline.some(
              (item) => item.kind === 'tool' && item.callKey === callKey
            );
            return {
              ...current,
              isThinking: false,
              activeToolCalls: [...current.activeToolCalls, call],
              timeline: hasTimelineItem
                ? current.timeline
                : [...current.timeline, { kind: 'tool', key: `tool-${callKey}`, callKey }],
            };
          });
        }
        break;
      case 'toolcall_delta':
        {
          setStreaming((current) => {
            const delta = typeof event.delta === 'string' ? event.delta : '';
            if (!delta) return current;
            const next = [...current.activeToolCalls];
            if (!next.length) return current;
            const contentIndex = typeof event.content_index === 'number' ? event.content_index : null;
            let targetIndex = -1;
            if (contentIndex !== null) {
              targetIndex = next.findIndex((item) => item.contentIndex === contentIndex);
            }
            if (targetIndex < 0) targetIndex = next.length - 1;
            const call = next[targetIndex];
            const mergedArguments = mergeStreamingToolArguments(call.argumentsJson, delta);
            next[targetIndex] = {
              ...call,
              argumentsJson: mergedArguments,
            };
            return { ...current, activeToolCalls: next };
          });
        }
        break;
      case 'toolcall_end':
        {
          const eventCallId = String((event.tool_call as any)?.id ?? '');
          const eventContentIndex = typeof event.content_index === 'number' ? event.content_index : null;
          const eventToolName = String((event.tool_call as any)?.name ?? '');
          approvalDebugLog('ws.toolcall_end', {
            session_id: sessionId,
            tool_call_id: eventCallId,
            tool_name: eventToolName,
            content_index: eventContentIndex,
          });
          setStreaming((current) => {
            const next = applyToolcallEnd(current, eventCallId, eventContentIndex);
            const doneCall = next.activeToolCalls.find((item) => (
              (eventCallId && item.id === eventCallId) ||
              (eventContentIndex !== null && item.contentIndex === eventContentIndex)
            )) ?? next.activeToolCalls[next.activeToolCalls.length - 1];
            const callApprovalRef = doneCall ? approvalRefFromMetadata(doneCall.metadata) : null;
            approvalDebugLog('ws.toolcall_end.classify', {
              session_id: sessionId,
              tool_call_id: doneCall?.id ?? eventCallId,
              tool_name: doneCall?.name ?? eventToolName,
              pending_from_metadata: Boolean(callApprovalRef?.pending),
              pending_final: Boolean(callApprovalRef?.pending),
              metadata_approval_id: callApprovalRef?.approvalId ?? null,
              metadata_provider: callApprovalRef?.provider ?? null,
            });
            return next;
          });
        }
        break;
      case 'tool_result':
        {
          const payload = (event.tool_result as Record<string, unknown> | undefined) ?? {};
          const metadata = isObjectRecord(payload.metadata) ? payload.metadata : {};
          const metadataApproval = approvalRefFromMetadata(metadata);
          approvalDebugLog('ws.tool_result', {
            session_id: sessionId,
            tool_call_id: String(payload.tool_call_id ?? (event.tool_call as any)?.id ?? ''),
            tool_name: String(payload.tool_name ?? (event.tool_call as any)?.name ?? 'unknown'),
            is_error: Boolean(payload.is_error),
            metadata_pending: Boolean(metadata.pending),
            metadata_approval_id: metadataApproval?.approvalId ?? null,
            metadata_provider: metadataApproval?.provider ?? null,
            metadata_status: metadataApproval?.status ?? null,
          });
          const toolNameForRefresh = String(payload.tool_name ?? (event.tool_call as any)?.name ?? '').trim();
          if (toolNameForRefresh === 'delegate') {
            void fetchTasks(sessionId);
          }
          if (
            toolNameForRefresh === 'runtime' ||
            toolNameForRefresh === 'python' ||
            toolNameForRefresh === 'git'
          ) {
            if (showFilesView) {
              void fetchRuntimeFiles(sessionId, runtimePath);
            }
          }
        }
        setStreaming((current) => {
          const payload = (event.tool_result as Record<string, unknown> | undefined) ?? {};
          const callId = String(payload.tool_call_id ?? (event.tool_call as any)?.id ?? '');
          const toolName = String(payload.tool_name ?? (event.tool_call as any)?.name ?? 'unknown');
          const rawContent = payload.content;
          const outputJson =
            typeof rawContent === 'string'
              ? rawContent
              : serializeToolArguments(rawContent);
          const fallbackArguments = toolArgumentsFromToolResultPayload(payload);
          const isError = Boolean(payload.is_error);
          const metadata = isObjectRecord(payload.metadata) ? payload.metadata : {};
          const approvalRef = approvalRefFromMetadata(metadata);
          const keepsWaitingState = Boolean(
            metadata.pending === true ||
            approvalRef?.pending === true,
          );
          return applyToolResult(current, {
            callId,
            toolName,
            fallbackArguments,
            outputJson,
            isError,
            metadata,
            keepsWaitingState,
          });
        });
        break;
      case 'session_named':
        setSessions((current) =>
            current.map((s) => s.id === sessionId ? { ...s, title: event.title as string } : s)
        );
        break;
      case 'sub_agent_started':
      case 'sub_agent_completed':
        void fetchTasks(sessionId);
        void fetchSessions();
        break;
      case 'compaction_started':
        setStreaming((current) => ({ ...current, isCompactingContext: true }));
        break;
      case 'compaction_completed': {
        const raw = Number(event.raw_token_count ?? 0);
        const compressed = Number(event.compressed_token_count ?? 0);
        setStreaming((current) => ({ ...current, isCompactingContext: false }));
        if (compressed > 0) {
          setContextTokenEstimate(Math.floor(compressed));
          if (typeof contextTokenBudget === 'number' && contextTokenBudget > 0) {
            setContextTokenPercent(Math.max(0, Math.min(100, Math.round((compressed / contextTokenBudget) * 100))));
          }
        }
        if (raw > 0) {
          toast.success(`Context compacted ${raw} -> ${compressed} tokens`);
          void loadMessages(sessionId);
        }
        void fetchContextUsage(sessionId);
        break;
      }
      case 'compaction_failed':
        setStreaming((current) => ({ ...current, isCompactingContext: false }));
        toast.error((event.error as string) || 'Auto-compaction failed');
        break;
      case 'runtime_ready':
        // noVNC may need a moment after the container reports ready — retry a few times
        void (async () => {
          for (let attempt = 0; attempt < 6; attempt++) {
            await new Promise((r) => setTimeout(r, 2000));
            const sid = activeSessionIdRef.current;
            if (!sid) break;
            try {
              const payload = await api.get<RuntimeLiveView>(`/runtime/live-view?session_id=${sid}`);
              setLiveView(payload);
              if (payload.enabled && payload.available) {
                setRuntimeBooting(false);
                return;
              }
            } catch { /* retry */ }
          }
          const sid = activeSessionIdRef.current;
          if (!sid) {
            setRuntimeBooting(false);
          }
        })();
        break;
      case 'done': {
        const stopReason = event.stop_reason as string | undefined;
        if (stopReason === 'tool_use') {
          // Commit per-iteration streamed text into the run timeline.
          setStreaming((current) => {
            const chunk = current.text.trim();
            if (!chunk) {
              return { ...current, isThinking: false, isStreaming: false, text: '' };
            }
            const nextSeq = current.interimTextSeq + 1;
            return {
              ...current,
              isThinking: false,
              isStreaming: false,
              text: '',
              interimTextSeq: nextSeq,
              timeline: [...current.timeline, { kind: 'text', key: `interim-${nextSeq}`, text: chunk }],
            };
          });
        } else {
          // Final done — agent turn complete, reset everything
          setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, isCompactingContext: false, agentIteration: 0, agentMaxIterations: 0 }));
          markSessionRead(sessionId);
          void loadMessages(sessionId);
          void fetchContextUsage(sessionId);
        }
        break;
      }
      case 'error': {
        const raw = (event.error as string) || (event.message as string) || 'Stream error';
        markLatestUserMessageRetryable(raw);
        toast.error(humanizeAgentError(raw), { duration: 8000 });
        // Reset streaming state so UI doesn't stay stuck in "thinking" mode
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, isCompactingContext: false }));
        break;
      }
      case 'agent_error': {
        const raw = (event.error as string) || (event.message as string) || 'Agent failed';
        markLatestUserMessageRetryable(raw);
        toast.error(humanizeAgentError(raw), { duration: 8000 });
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, isCompactingContext: false }));
        break;
      }
    }
  }

  async function appendImageFiles(files: File[]) {
    if (!files.length) return;
    const slotsLeft = MAX_IMAGE_ATTACHMENTS - composerAttachments.length;
    if (slotsLeft <= 0) {
      toast.error(`Maximum ${MAX_IMAGE_ATTACHMENTS} images per message`);
      return;
    }

    const nextFiles = files.slice(0, slotsLeft);
    if (files.length > slotsLeft) {
      toast.error(`Only ${slotsLeft} more image${slotsLeft === 1 ? '' : 's'} can be added`);
    }

    const parsed: MessageAttachment[] = [];
    for (const file of nextFiles) {
      const mimeType = file.type.toLowerCase();
      if (!ALLOWED_IMAGE_MIME_TYPES.has(mimeType)) {
        toast.error(`Unsupported file type: ${file.name}`);
        continue;
      }
      if (file.size > MAX_IMAGE_ATTACHMENT_BYTES) {
        toast.error(`Image too large (max 5MB): ${file.name}`);
        continue;
      }
      try {
        const base64 = await fileToBase64(file);
        parsed.push({
          mime_type: mimeType,
          base64,
          filename: file.name,
          size_bytes: file.size,
        });
      } catch {
        toast.error(`Failed to read image: ${file.name}`);
      }
    }
    if (parsed.length) {
      setComposerAttachments((current) => [...current, ...parsed].slice(0, MAX_IMAGE_ATTACHMENTS));
    }
  }

  async function onSelectImages(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    await appendImageFiles(files);
    event.target.value = '';
  }

  function onComposerPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const clipboard = event.clipboardData;
    if (!clipboard) return;
    const imageFiles: File[] = [];
    for (const item of Array.from(clipboard.items)) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (!file) continue;
      if (!ALLOWED_IMAGE_MIME_TYPES.has(file.type.toLowerCase())) continue;
      imageFiles.push(file);
    }
    if (!imageFiles.length) return;

    const pastedText = clipboard.getData('text/plain');
    event.preventDefault();
    if (pastedText) {
      const target = event.currentTarget;
      const start = target.selectionStart ?? composer.length;
      const end = target.selectionEnd ?? composer.length;
      setComposer((current) => `${current.slice(0, start)}${pastedText}${current.slice(end)}`);
      const caret = start + pastedText.length;
      window.requestAnimationFrame(() => {
        target.selectionStart = caret;
        target.selectionEnd = caret;
      });
    }
    void appendImageFiles(imageFiles);
  }

  function removeComposerAttachment(index: number) {
    setComposerAttachments((current) => current.filter((_, i) => i !== index));
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = composer.trim();
    if ((!content && composerAttachments.length === 0) || streamBusy || !activeSessionId) return;

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      setRetryCandidate(null);
      // Prepend time context if conversation has been idle for >30 minutes
      const lastMsg = messages.at(-1);
      const idleMs = lastMsg ? Date.now() - new Date(lastMsg.created_at).getTime() : 0;
      const now = new Date().toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
      const idleNote = idleMs > 30 * 60 * 1000
        ? `[Resuming after ${Math.round(idleMs / 60000)} min — current time: ${now}]\n\n`
        : '';
      wsRef.current.send(
        JSON.stringify({
          type: 'message',
          content: idleNote + content,
          attachments: composerAttachments,
          tier: selectedTier,
          max_iterations: maxIterations,
          agent_mode: selectedAgentMode ?? undefined,
        })
      );
      setComposer('');
      setComposerAttachments([]);
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
    } else {
      toast.error('Connection lost. Reconnecting...');
    }
  }

  async function stopCurrent() {
    if (!activeSessionId) return;
    const sessionId = activeSessionId;
    setIsStopping(true);
    try {
      await api.post(`/sessions/${sessionId}/stop`, {});
      await loadMessages(sessionId);
      await Promise.allSettled([
        fetchContextUsage(sessionId),
        fetchSessions({ autoSelectIfEmpty: false }),
      ]);
      toast.success('Response stopped');
    } catch {
      toast.error('Failed to stop');
    }
    finally { setIsStopping(false); }
  }

  // Switch to the Runtime tab and pick a sub-view in one call. Used by
  // anything that wants to focus a specific runtime surface — terminal
  // pill clicks, "Open terminal" links inside tool cards, the segmented
  // control buttons themselves. Recording the intent timestamp lets the
  // auto-focus rule (see terminal_opened WS handler) know to back off
  // when the user has just steered the view manually.
  function openRuntimeView(view: RuntimeView) {
    lastRuntimeViewIntentRef.current = Date.now();
    setRightRailTab('runtime');
    setRuntimeView(view);
  }

  // Optimistically drops the pill so the UI reacts immediately, then asks the
  // backend to kill the tmux session. The authoritative `terminal_closed` WS
  // event will also fire and is idempotent against the optimistic update.
  async function closeTerminal(terminalId: string) {
    if (!activeSessionId) return;
    if (terminalId === '0') return; // protected by both ends, but belt-and-braces
    setActiveTerminals((current) => current.filter((t) => t.id !== terminalId));
    setFocusedTerminalId((current) => (current === terminalId ? null : current));
    try {
      await api.delete(
        `/sessions/${activeSessionId}/terminals/${encodeURIComponent(terminalId)}`,
      );
    } catch {
      toast.error(`Failed to close ${terminalId}`);
    }
  }

  async function compactContext() {
    if (!activeSessionId) return;
    if (streamBusy) {
      toast.error('Cannot compact while agent is running');
      return;
    }
    setIsCompacting(true);
    try {
      const result = await api.post<{ raw_token_count: number; compressed_token_count: number; summary_preview: string }>(
        `/sessions/${activeSessionId}/compact`, {}
      );
      if (result.raw_token_count === 0) {
        toast.success('Nothing to compact yet (context too small)');
      } else {
        toast.success(
          `Compacted ${result.raw_token_count} → ${result.compressed_token_count} tokens`
        );
        if (result.compressed_token_count > 0) {
          setContextTokenEstimate(Math.floor(result.compressed_token_count));
          if (typeof contextTokenBudget === 'number' && contextTokenBudget > 0) {
            setContextTokenPercent(
              Math.max(0, Math.min(100, Math.round((result.compressed_token_count / contextTokenBudget) * 100)))
            );
          }
        }
        // Re-fetch messages so the UI reflects deleted old messages
        setMessages([]);
        void loadMessages(activeSessionId);
      }
      void fetchContextUsage(activeSessionId);
    } catch { toast.error('Compaction failed'); }
    finally { setIsCompacting(false); }
  }

  return (
      <AppShell
          title={activeSession?.title || 'Untitled Session'}
          subtitle={activeSession ? `ID: ${activeSession.id}` : 'Operator Workspace'}
          contentClassName="h-full !p-0 overflow-hidden"
          hideSidebar={mode === 'solo'}
          hideHeader={mode === 'solo' || isDesktopFullscreen}
          actions={
            mode === 'advanced' && !isDesktopFullscreen ? (
              <div className="flex min-w-0 items-center gap-2">
                <div ref={sessionDropdownRef} className="order-5 relative z-[200] hidden min-w-0 sm:block">
                  <button
                    ref={sessionDropdownButtonRef}
                    type="button"
                    aria-haspopup="listbox"
                    aria-expanded={isSessionDropdownOpen}
                    onClick={() => {
                      setIsEffortDropdownOpen(false);
                      setIsAgentModeDropdownOpen(false);
                      updateSessionDropdownRect();
                      setIsSessionDropdownOpen((open) => !open);
                    }}
                    className="flex h-9 w-[min(390px,34vw)] items-center justify-start gap-4 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] pl-4 pr-3 text-left shadow-sm outline-none transition-all hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-2)] focus:border-[color:var(--accent-solid)] focus:ring-2 focus:ring-[color:var(--accent-solid)]/20"
                  >
                    <span className="w-28 shrink-0 text-left text-[9px] font-bold uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
                      Instance Picker
                    </span>
                    <span aria-hidden="true" className="h-4 w-px shrink-0 bg-[color:var(--border-subtle)]" />
                    <span className="block min-w-0 flex-1 truncate text-left text-xs font-semibold text-[color:var(--text-primary)]">
                      {instancePickerLabel}
                    </span>
                    <ChevronDown
                      size={13}
                      aria-hidden="true"
                      className={`shrink-0 text-[color:var(--text-muted)] transition-transform duration-300 ${isSessionDropdownOpen ? 'rotate-180' : ''}`}
                    />
                  </button>

                  {isSessionDropdownOpen && sessionDropdownRect && createPortal(
                    <div
                      ref={sessionDropdownMenuRef}
                      role="listbox"
                      aria-label="Switch session"
                      style={{
                        left: sessionDropdownRect.left,
                        top: sessionDropdownRect.top,
                        width: Math.max(sessionDropdownRect.width, 320),
                      }}
                      className="fixed z-[10000] max-h-80 overflow-y-auto rounded-2xl border border-[color:var(--border-strong)] bg-[color:var(--surface-0)] py-1.5 shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-200 origin-top-left"
                    >
                      {instances.length === 0 ? (
                        <div className="px-3 py-3 text-xs text-[color:var(--text-muted)]">No instances</div>
                      ) : (
                        instances.map((instance) => {
                          const title = (instance.display_name || instance.name).trim() || instance.name;
                          const active = instance.name === activeInstanceName;
                          return (
                            <button
                              key={instance.name}
                              type="button"
                              role="option"
                              aria-selected={active}
                              onClick={() => {
                                setIsSessionDropdownOpen(false);
                                if (!active) onInstanceClick(instance.name);
                              }}
                              className={`group flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                                active
                                  ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                                  : 'text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
                              }`}
                            >
                              <div className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'bg-[color:var(--accent-solid)]' : 'bg-[color:var(--text-muted)]/35 group-hover:bg-[color:var(--text-secondary)]'}`} />
                              <span className="min-w-0 flex-1 truncate text-xs font-semibold">{title}</span>
                              {active && <Check size={13} className="shrink-0 text-[color:var(--accent-solid)]" />}
                            </button>
                          );
                        })
                      )}
                    </div>,
                    document.body,
                  )}
                </div>

                <button
                  type="button"
                  title="Instance menu"
                  aria-label="Instance menu"
                  onClick={() => navigate('/')}
                  className="order-6 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[color:var(--text-secondary)] shadow-sm transition-all hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] active:scale-95"
                >
                  <Home size={14} />
                </button>

                <button
                    onClick={() => setMode('solo')}
                    className="order-1 inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm animate-[focusModePillIn_180ms_cubic-bezier(0.22,1,0.36,1)]"
                >
                  <Expand size={14} className="text-emerald-500/80" />
                  Focus
                </button>

                <div className="order-2 h-4 w-px bg-[color:var(--border-subtle)] mx-1" />

                <button
                    onClick={resetSession}
                    title="Start fresh (memories preserved)"
                    className="order-3 inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm"
                >
                  <RefreshCw size={14} className="text-sky-500/80" />
                  New Chat
                </button>

                <button
                    onClick={compactContext}
                    disabled={isCompacting}
                    className="order-4 inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 shadow-sm"
                >
                  <Wand2 size={14} className={`${isCompacting ? 'animate-spin' : ''} text-amber-500/80`} />
                  Compact
                </button>
              </div>
            ) : null
          }
      >
        <div className="relative flex h-full w-full overflow-hidden">
          {mode === 'solo' ? (
            <div className="absolute top-4 right-4 z-20">
              <button
                type="button"
                onClick={() => setMode('advanced')}
                className="inline-flex h-9 items-center gap-2 rounded-full border border-[color:var(--border-strong)] bg-[color:var(--surface-0)]/90 backdrop-blur px-4 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)] hover:bg-[color:var(--surface-1)] transition-all active:scale-95 shadow-xl animate-[focusModePillIn_180ms_cubic-bezier(0.22,1,0.36,1)]"
              >
                <X size={14} className="text-[color:var(--text-muted)]" />
                Exit Focus
              </button>
            </div>
          ) : null}
          {/* Chat Area */}
          <main className="order-5 relative z-0 flex-1 flex flex-col min-w-0 bg-[color:var(--surface-0)] overflow-hidden">
            {/* Unified Session Toolbar */}
            {mode === 'advanced' ? (
            <div className="flex items-center justify-between px-4 h-12 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]/80 backdrop-blur-md sticky top-0 z-30 shrink-0 select-none">
              {/* Left: Connection & Progress */}
              <div className="flex items-center gap-2.5">
                <div className="group relative flex items-center gap-2 px-2.5 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default">
                  <div className={`h-1.5 w-1.5 rounded-full transition-all duration-500 ${streaming.connection === 'connected' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]'}`} />
                  <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-[color:var(--text-secondary)]">{streaming.connection === 'connected' ? 'Live' : 'Offline'}</span>

                  {/* Connection Tooltip */}
                  <div className="absolute top-full left-0 mt-2 px-3 py-2 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-all pointer-events-none shadow-2xl z-50 translate-y-1 group-hover:translate-y-0">
                    <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)] mb-1">Telemetry Link</div>
                    <div className="flex items-center gap-2">
                      <div className={`h-1.5 w-1.5 rounded-full ${streaming.connection === 'connected' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                      <span className={streaming.connection === 'connected' ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'}>{streaming.connection.toUpperCase()}</span>
                    </div>
                  </div>
                </div>

                {streaming.agentMaxIterations > 0 && (
                  <div className="group relative flex items-center gap-3 px-3 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default">
                    <div className="w-16 h-1 rounded-full bg-[color:var(--surface-3)] overflow-hidden">
                      <div
                        className="h-full rounded-full bg-[color:var(--accent-solid)] transition-all duration-700 ease-out"
                        style={{ width: `${Math.min((streaming.agentIteration / streaming.agentMaxIterations) * 100, 100)}%` }}
                      />
                    </div>
                    <span className="text-[10px] font-mono font-bold text-[color:var(--text-primary)]">
                      {streaming.agentIteration}<span className="text-[color:var(--text-muted)] mx-0.5">/</span>{streaming.agentMaxIterations}
                    </span>

                    {/* Progress Tooltip */}
                    <div className="absolute top-full left-0 mt-2 px-3 py-2 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-all pointer-events-none shadow-2xl z-50 translate-y-1 group-hover:translate-y-0">
                      <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)] mb-1">Execution Pipeline</div>
                      <div><span className="font-bold text-[color:var(--accent-solid)]">{streaming.agentIteration}</span><span className="text-[color:var(--text-muted)]"> of {streaming.agentMaxIterations} steps completed</span></div>
                      <div className="mt-1 h-1 w-full bg-[color:var(--surface-2)] rounded-full overflow-hidden">
                        <div className="h-full bg-[color:var(--accent-solid)]" style={{ width: `${(streaming.agentIteration / streaming.agentMaxIterations) * 100}%` }} />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Center: Toolbar metadata removed */}

              {/* Right: Controls & Context */}
              <div className="flex items-center gap-3">
                {/* Context Indicator */}
                <div className="group relative flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default">
                  {(() => {
                    const estimatedTokens = typeof contextTokenEstimate === 'number' && Number.isFinite(contextTokenEstimate) ? contextTokenEstimate : null;
                    const hasBudget = typeof contextTokenBudget === 'number' && contextTokenBudget > 0;
                    const hasEstimate = typeof estimatedTokens === 'number';
                    const CTX_CEILING = hasBudget ? contextTokenBudget : 1;
                    const pct = typeof contextTokenPercent === 'number' && Number.isFinite(contextTokenPercent)
                        ? Math.max(0, Math.min(100, Math.floor(contextTokenPercent)))
                        : Math.round(((estimatedTokens || 0) / CTX_CEILING) * 100);
                    const toneColor = pct < 50 ? '#22c55e' : pct < 80 ? '#f59e0b' : '#f43f5e';
                    const warn = hasBudget && hasEstimate && estimatedTokens > CTX_CEILING;
                    const kTokens = hasEstimate
                      ? (estimatedTokens >= 1000 ? `${(estimatedTokens / 1000).toFixed(1)}k` : `${estimatedTokens}`)
                      : '—';
                    const ceilingLabel = hasBudget ? `${Math.round(CTX_CEILING / 1000)}k` : '…';

                    return (
                      <>
                        <div
                          className={`h-1.5 w-1.5 rounded-full ${warn ? 'animate-pulse shadow-[0_0_8px_rgba(245,158,11,0.6)]' : ''}`}
                          style={{ backgroundColor: toneColor }}
                        />
                        <span className="text-[10px] font-mono font-bold text-[color:var(--text-primary)]">
                          {pct}<span className="text-[color:var(--text-muted)] opacity-60 ml-0.5">%</span>
                        </span>

                        {/* Context Tooltip */}
                        <div className="absolute top-full right-0 mt-2 px-3 py-2.5 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-all pointer-events-none shadow-2xl z-50 translate-y-1 group-hover:translate-y-0">
                          <div className="font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-1.5 pb-1 border-b border-[color:var(--border-subtle)]">Context Window</div>
                          <div className="flex items-center justify-between gap-8 mb-1">
                            <span className="text-[color:var(--text-secondary)]">Utilization</span>
                            <span className="font-bold" style={{ color: toneColor }}>{pct}%</span>
                          </div>
                          <div className="flex items-center justify-between gap-8 mb-2">
                            <span className="text-[color:var(--text-secondary)]">Token Load</span>
                            <span className="text-[color:var(--text-muted)]"><span className="font-bold text-[color:var(--text-primary)]">{kTokens}</span> / {ceilingLabel}</span>
                          </div>
                          <div className="h-1 w-full bg-[color:var(--surface-2)] rounded-full overflow-hidden">
                            <div
                              className="h-full transition-all duration-500"
                              style={{ width: `${pct}%`, backgroundColor: toneColor }}
                            />
                          </div>
                          {warn && (
                            <div className="mt-2 py-1 px-2 rounded bg-amber-500/10 text-amber-500 font-bold text-[9px] uppercase tracking-wider animate-pulse">
                              Auto-compaction pending
                            </div>
                          )}
                          {!hasEstimate && <div className="mt-1.5 text-[color:var(--text-muted)] italic">Awaiting telemetry...</div>}
                        </div>
                      </>
                    );
                  })()}
                </div>

                <div className="w-px h-4 bg-[color:var(--border-subtle)] mx-1" />

                {/* Effort / Tier Selector */}
                <div ref={effortDropdownRef} className="relative z-20">
                  {(() => {
                      const active = models.find(m => m.tier === selectedTier) || models[0];
                      const tier = active?.tier ?? 'normal';
                      const icons: Record<string, any> = {
                        fast: <Zap size={11} />,
                        normal: <Sparkles size={11} />,
                        hard: <Brain size={11} />,
                      };
                      return (
                        <button
                          onClick={() => {
                            setIsAgentModeDropdownOpen(false);
                            setIsEffortDropdownOpen(!isEffortDropdownOpen);
                          }}
                          className="flex items-center gap-2.5 px-3 h-8 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)] transition-all shadow-sm"
                        >
                          <span className="text-[color:var(--text-secondary)]">{icons[tier] || <Activity size={11} />}</span>
                          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)]">{active?.label || 'Mode'}</span>
                          <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${isEffortDropdownOpen ? 'rotate-180' : ''}`} />
                        </button>
                      );
                    })()}

                  {isEffortDropdownOpen && (
                      <div className="absolute top-full right-0 mt-2 w-72 rounded-2xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-2xl z-50 overflow-hidden py-1.5 animate-in fade-in zoom-in-95 duration-200 origin-top-right backdrop-blur-xl">
                        {models.map(m => {
                          const active = selectedTier === m.tier;
                          const tier = m.tier ?? 'normal';
                          const TierIcon = {
                            fast: Zap,
                            normal: Sparkles,
                            hard: Brain,
                          }[tier as string] || Activity;

                          const tierColor = {
                            fast: 'text-emerald-500',
                            normal: 'text-sky-500',
                            hard: 'text-rose-500',
                          }[tier as string] || 'text-[color:var(--text-muted)]';

                          return (
                            <button
                              key={m.tier}
                              onClick={() => {
                                setSelectedTier(m.tier);
                                setIsEffortDropdownOpen(false);
                              }}
                              className={`w-full flex items-start gap-3.5 px-4 py-3 transition-all text-left group ${
                                active
                                  ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                                  : 'hover:bg-[color:var(--surface-1)]'
                              }`}
                            >
                              <div className={`mt-0.5 shrink-0 transition-transform group-hover:scale-110 duration-200 ${active ? 'text-[color:var(--app-bg)] opacity-90' : tierColor}`}>
                                <TierIcon size={14} />
                              </div>
                              <div className="flex flex-col gap-0.5 min-w-0">
                                <div className={`text-[10px] font-bold uppercase tracking-widest ${active ? 'text-[color:var(--app-bg)]' : 'text-[color:var(--text-primary)]'}`}>
                                  {m.label}
                                </div>
                                <div className={`text-[9px] font-medium leading-tight ${active ? 'text-[color:var(--app-bg)] opacity-70' : 'text-[color:var(--text-muted)]'}`}>
                                  {m.description}
                                </div>
                                {m.primary_provider_id && (
                                  <div className="mt-2 flex items-center gap-1.5">
                                    <span className={`text-[8px] font-mono px-1 rounded uppercase tracking-wider ${active ? 'bg-[color:var(--app-bg)]/10 text-[color:var(--app-bg)] border border-[color:var(--app-bg)]/20' : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] border border-[color:var(--border-subtle)] opacity-80'}`}>
                                      {m.primary_provider_id}
                                    </span>
                                    <span className={`text-[8px] font-mono truncate tracking-tight ${active ? 'text-[color:var(--app-bg)] opacity-40' : 'text-[color:var(--text-muted)] opacity-50'}`}>
                                      {m.primary_model_id}
                                    </span>
                                  </div>
                                )}
                              </div>
                              {active && (
                                <div className="ml-auto w-1 h-6 rounded-full bg-[color:var(--app-bg)]/20 my-auto shadow-sm" />
                              )}
                            </button>
                          );
                        })}
                      </div>
                  )}
                </div>

                {/* Agent Mode Selector */}
                <div ref={agentModeDropdownRef} className="relative z-20 pl-2 border-l border-[color:var(--border-subtle)]">
                  {(() => {
                    const active = agentModes.find((item) => item.id === selectedAgentMode) ?? agentModes[0];
                    return (
                      <button
                        onClick={() => {
                          if (agentModes.length === 0) return;
                          setIsEffortDropdownOpen(false);
                          setIsAgentModeDropdownOpen((prev) => !prev);
                        }}
                        disabled={agentModes.length === 0}
                        className="flex items-center gap-2.5 px-3 h-8 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)] transition-all shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
                        title={active?.description ?? 'Agent mode'}
                      >
                        <span className="text-[color:var(--text-secondary)]"><Bot size={11} /></span>
                        <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)]">
                          {active?.label ?? 'Agent Mode'}
                        </span>
                        <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${isAgentModeDropdownOpen ? 'rotate-180' : ''}`} />
                      </button>
                    );
                  })()}

                  {isAgentModeDropdownOpen && agentModes.length > 0 && (
                    <div className="absolute top-full right-0 mt-2 w-72 rounded-2xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-2xl z-50 overflow-hidden py-1.5 animate-in fade-in zoom-in-95 duration-200 origin-top-right backdrop-blur-xl">
                      {agentModes.map((modeOption) => {
                        const active = selectedAgentMode === modeOption.id;
                        return (
                          <button
                            key={modeOption.id}
                            onClick={() => {
                              setSelectedAgentMode(modeOption.id);
                              setIsAgentModeDropdownOpen(false);
                            }}
                            className={`w-full flex items-start gap-3.5 px-4 py-3 transition-all text-left group ${
                              active
                                ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                                : 'hover:bg-[color:var(--surface-1)]'
                            }`}
                          >
                            <div className={`mt-0.5 shrink-0 transition-transform group-hover:scale-110 duration-200 ${active ? 'text-[color:var(--app-bg)] opacity-90' : 'text-[color:var(--text-muted)]'}`}>
                              <Bot size={14} />
                            </div>
                            <div className="flex flex-col gap-0.5 min-w-0">
                              <div className={`text-[10px] font-bold uppercase tracking-widest ${active ? 'text-[color:var(--app-bg)]' : 'text-[color:var(--text-primary)]'}`}>
                                {modeOption.label}
                              </div>
                              <div className={`text-[9px] font-medium leading-tight ${active ? 'text-[color:var(--app-bg)] opacity-70' : 'text-[color:var(--text-muted)]'}`}>
                                {modeOption.description}
                              </div>
                            </div>
                            {active && (
                              <div className="ml-auto w-1 h-6 rounded-full bg-[color:var(--app-bg)]/20 my-auto shadow-sm" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Steps Selector */}
                <div className="flex items-center gap-2 pl-2 border-l border-[color:var(--border-subtle)]">
                  <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Max</span>
                  <select
                      value={maxIterations}
                      onChange={(e) => setMaxIterations(Number(e.target.value))}
                      className="bg-transparent text-[11px] font-bold outline-none cursor-pointer hover:text-[color:var(--accent-solid)] transition-colors pr-1"
                  >
                    {[5, 10, 20, 30, 50, 100].map(n => (
                      <option key={n} value={n} className="bg-[color:var(--surface-0)]">{n}</option>
                    ))}
                  </select>
                </div>

              </div>
            </div>
            ) : null}

            {/* Messages */}
            <div
                ref={scrollRef}
                onScroll={onMessagesScroll}
            className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6"
            >
              {messagesLoading && messages.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center gap-4 text-[color:var(--text-muted)] animate-in">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--surface-2)] text-[color:var(--text-primary)]">
                      <Loader2 size={24} className="animate-spin" />
                    </div>
                    <p className="text-[10px] font-bold uppercase tracking-widest">Synchronizing workspace...</p>
                  </div>
              ) : (
                  <>
                    {messages.length === 0 && (
                        <div className="h-full flex flex-col items-center justify-center text-center p-8 opacity-40">
                          <Bot size={48} className="mb-4" />
                          <p className="text-sm font-medium">No messages in this session yet.</p>
                          <p className="text-xs">Start a conversation to see it here.</p>
                        </div>
                    )}

                    {messages.length > 0 && isLoadingOlderMessages && (
                      <div className="flex justify-center">
                        <div className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                          <Loader2 size={11} className="animate-spin" />
                          Loading older messages
                        </div>
                      </div>
                    )}

                    {messages
                      .filter(m => m.role !== 'system')
                      .filter(m => !(m.role === 'assistant' && !m.content?.trim() && !m.tool_name))
                      .map(m => (
                        (() => {
                          const persistedRetryError = isObjectRecord(m.metadata) && typeof m.metadata.retryable_error === 'string'
                            ? m.metadata.retryable_error
                            : null;
                          const retryError = retryCandidate?.messageId === m.id
                            ? retryCandidate.error
                            : persistedRetryError;
                          return (
                        <SessionMessageCard
                          key={m.id}
                          message={m}
                          toolArgumentsByCallId={toolArgumentsByCallId}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                          onRetryMessage={retryError ? retryFailedMessage : undefined}
                          retryError={retryError}
                          retrying={retryingMessageId === m.id}
                        />
                          );
                        })()
                      ))}

                    {streaming.timeline.map((item) => {
                      if (item.kind === 'text') {
                        return (
                          <div key={item.key} className="flex flex-col gap-1.5 animate-in items-start w-full">
                            <div className="flex items-center gap-2 px-1">
                              <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
                                assistant • interim
                              </span>
                            </div>
                            <div className="max-w-[90%] rounded-2xl rounded-tl-none px-4 py-1.5 text-xs font-medium shadow-sm border bg-[color:var(--surface-1)] border-[color:var(--border-subtle)]">
                              <Markdown content={item.text} />
                            </div>
                          </div>
                        );
                      }
                      const call = toolCallByKey.get(item.callKey);
                      if (!call) return null;
                      return (
                        <StreamToolCard
                          key={item.key}
                          call={call}
                          active={activeToolCallKeys.has(item.callKey)}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                          onOpenTerminal={(tid) => {
                            openRuntimeView('terminals');
                            setFocusedTerminalId(tid);
                          }}
                        />
                      );
                    })}

                    {streaming.completedToolCalls
                      .filter((call) => !timelineToolKeys.has(streamingCallKey(call)))
                      .map((c, idx) => (
                        <StreamToolCard
                          key={`${c.id}-${c.contentIndex ?? 'na'}-fallback-complete-${idx}`}
                          call={c}
                          active={false}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                          onOpenTerminal={(tid) => {
                            openRuntimeView('terminals');
                            setFocusedTerminalId(tid);
                          }}
                        />
                      ))}
                    {streaming.activeToolCalls
                      .filter((call) => !timelineToolKeys.has(streamingCallKey(call)))
                      .map((c, idx) => (
                        <StreamToolCard
                          key={`${c.id}-${c.contentIndex ?? 'na'}-fallback-active-${idx}`}
                          call={c}
                          active={true}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                          onOpenTerminal={(tid) => {
                            openRuntimeView('terminals');
                            setFocusedTerminalId(tid);
                          }}
                        />
                      ))}

                    {hasVisibleStreamingText(streaming.text) && (
                        <div className="flex flex-col gap-1.5 animate-in items-start w-full">
                          <div className="flex items-center gap-2 px-1">
                        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
                          assistant • streaming
                        </span>
                          </div>
                          <div className="max-w-[90%] rounded-2xl rounded-tl-none px-4 py-1.5 text-xs font-medium shadow-sm border bg-[color:var(--surface-1)] border-[color:var(--border-subtle)]">
                            <Markdown content={streaming.text} />
                          </div>
                        </div>
                    )}

                    {showThinkingIndicator && (
                        <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] animate-pulse">
                          <Bot size={14} />
                          Sentinel is thinking...
                        </div>
                    )}

                    {streaming.isCompactingContext && (
                        <div className="flex items-center gap-3 px-4 py-3 rounded-2xl bg-amber-500/5 border border-amber-500/20 w-fit animate-pulse">
                          <Loader2 size={14} className="animate-spin text-amber-500" />
                          <span className="text-[10px] font-bold uppercase tracking-widest text-amber-500">
                            Compacting context
                          </span>
                        </div>
                    )}

                    {(() => {
                      // Single sticky cluster: terminal pills stack on top,
                      // Stop button sits just under them (closer to the composer
                      // so it's the easier target when interrupting). When Stop
                      // isn't visible the pills sit at the same `bottom-2`
                      // anchor — no floating gap above the composer.
                      const stopVisible =
                        streaming.agentIteration > 0
                        || streaming.isThinking
                        || streaming.isStreaming
                        || streaming.activeToolCalls.length > 0;
                      if (!stopVisible && activeTerminals.length === 0) return null;
                      return (
                        <div className="sticky bottom-2 z-20 flex flex-col items-center gap-1.5 pointer-events-none px-2">
                          {activeTerminals.length > 0 ? (
                            <div className="flex flex-wrap items-center justify-center gap-1.5">
                              {activeTerminals.map((terminal) => {
                                const isFocused = focusedTerminalId === terminal.id && showTerminalsView;
                                // Label is derived from terminal_id: '0' → "main",
                                // 'auto-xxx' / 'bg-…' → first command summary, anything
                                // else → the agent's chosen name verbatim. Stable
                                // across backend restarts.
                                const display = getTerminalLabel(terminal.id, terminal.lastCommand ?? terminal.label);
                                const tooltip = terminal.lastCommand
                                  ? `${display} — last: ${summarizeCommand(terminal.lastCommand)}`
                                  : display;
                                // Terminal '0' is the user's primary shared
                                // shell — never offer to close it. Everything
                                // else gets an ✕ on hover.
                                const closable = terminal.id !== '0';
                                const focusedClasses = isFocused
                                  ? 'border-sky-500 bg-sky-500/20 text-sky-300'
                                  : 'border-sky-500/30 bg-[color:var(--surface-0)]/90 text-sky-500 hover:bg-sky-500 hover:text-white';
                                return (
                                  <div
                                    key={terminal.id}
                                    className={`group pointer-events-auto inline-flex items-stretch rounded-full border backdrop-blur transition-all ${focusedClasses}`}
                                  >
                                    <button
                                      type="button"
                                      onClick={() => {
                                        openRuntimeView('terminals');
                                        setFocusedTerminalId(terminal.id);
                                      }}
                                      className={`inline-flex items-center gap-1.5 ${closable ? 'pl-3 pr-2' : 'px-3'} h-7 text-[10px] font-bold uppercase tracking-wider active:scale-95`}
                                      title={tooltip}
                                    >
                                      <Terminal size={11} />
                                      <span className="max-w-[140px] truncate normal-case tracking-normal font-medium">
                                        {display}
                                      </span>
                                      {terminal.busy ? <Loader2 size={9} className="animate-spin" /> : null}
                                    </button>
                                    {closable ? (
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          void closeTerminal(terminal.id);
                                        }}
                                        className="inline-flex items-center justify-center pr-2.5 pl-1 h-7 opacity-60 hover:opacity-100 active:scale-90 rounded-r-full"
                                        title={`Close ${display}`}
                                        aria-label={`Close terminal ${display}`}
                                      >
                                        <X size={11} />
                                      </button>
                                    ) : null}
                                  </div>
                                );
                              })}
                            </div>
                          ) : null}
                          {stopVisible ? (
                            <button
                              type="button"
                              onClick={stopCurrent}
                              disabled={isStopping}
                              className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-rose-500/40 bg-[color:var(--surface-0)]/90 backdrop-blur px-4 h-9 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500 hover:text-white transition-all active:scale-95 shadow-xl disabled:opacity-50"
                            >
                              <Square size={12} fill="currentColor" />
                              {isStopping ? 'Stopping...' : 'Stop Execution'}
                            </button>
                          ) : null}
                        </div>
                      );
                    })()}

                    {!isPinnedToBottom && (
                      <div className="sticky bottom-4 z-20 flex justify-end pointer-events-none px-4">
                        <button
                          type="button"
                          onClick={() => scrollToBottom('smooth')}
                          className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-[color:var(--border-strong)] bg-[color:var(--surface-0)]/90 backdrop-blur px-4 h-9 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)] shadow-xl hover:border-[color:var(--accent-solid)] hover:text-[color:var(--accent-solid)] transition-all active:scale-95 translate-y-0"
                        >
                          <ArrowDown size={12} />
                          Back to bottom
                        </button>
                      </div>
                    )}
                  </>
              )}
            </div>

            {/* Composer */}
            <div className="p-4 border-t border-[color:var(--border-subtle)]">
              <>
                <form onSubmit={sendMessage} className="relative group">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/gif"
                        multiple
                        onChange={onSelectImages}
                        className="hidden"
                    />
                    <textarea
                        ref={composerRef}
                        value={composer}
                        onChange={(e) => setComposer(e.target.value)}
                        onPaste={onComposerPaste}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendMessage(e as any);
                          }
                        }}
                        disabled={isCompacting || streaming.isCompactingContext}
                        placeholder={isCompacting || streaming.isCompactingContext ? 'Compacting context…' : 'Ask Sentinel anything...'}
                        className="input-field min-h-[100px] py-4 pr-24 resize-none text-[14px] leading-relaxed shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                      <div className="absolute right-3 bottom-3 flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={streamBusy || composerAttachments.length >= MAX_IMAGE_ATTACHMENTS}
                          className="p-2.5 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] disabled:opacity-40 disabled:cursor-not-allowed transition-all active:scale-95 shadow-sm"
                          title="Attach image"
                        >
                          <Paperclip size={16} />
                        </button>
                        <button
                            type="submit"
                            disabled={(composer.trim().length === 0 && composerAttachments.length === 0) || streamBusy}
                            className={`p-2.5 rounded-xl transition-all active:scale-95 shadow-md ${
                                (composer.trim().length > 0 || composerAttachments.length > 0) && !streamBusy
                                    ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-[0_4px_12px_rgba(0,0,0,0.1)] dark:shadow-[0_4px_12px_rgba(255,255,255,0.05)]'
                                    : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] cursor-not-allowed opacity-40'
                            }`}
                        >
                          <Send size={18} />
                        </button>
                      </div>
                </form>
                {composerAttachments.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {composerAttachments.map((item, index) => (
                          <div key={`${item.base64.slice(0, 24)}-${index}`} className="relative rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-1.5">
                            <img
                              src={`data:${item.mime_type};base64,${item.base64}`}
                              alt={item.filename || `upload-${index + 1}`}
                              className="h-14 w-20 rounded object-cover"
                            />
                            <button
                              type="button"
                              onClick={() => removeComposerAttachment(index)}
                              className="absolute -top-2 -right-2 rounded-full border border-rose-500/40 bg-black/80 p-0.5 text-rose-400 hover:text-rose-300"
                              title="Remove image"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                ) : null}
                <div className="mt-2 flex items-center justify-between text-[10px] text-[color:var(--text-muted)] font-medium">
                  <p>Press Enter to send, Shift+Enter for new line</p>
                  <p>Realtime streaming enabled · up to 4 images (5MB each)</p>
                </div>
              </>
            </div>
          </main>

          {workbenchVisible && !isDesktopFullscreen ? (
            // While the desktop fullscreen overlay is up, the Workbench panel
            // would otherwise sit on top of it (its `relative z-30` ends up in
            // a different stacking context than DesktopPreview's `fixed
            // z-[1000]`, so the simple z compare doesn't win). Unmounting it
            // for the duration of the fullscreen state is cleaner than
            // wrestling with stacking contexts and avoids paint thrash.
            <>
              <div
                className="order-4 relative hidden xl:block w-0 shrink-0"
              />
              <div
                className={`absolute inset-y-0 z-40 hidden xl:block w-3 -translate-x-1/2 cursor-col-resize transition-colors ${isWorkbenchResizing ? 'bg-[color:var(--accent-solid)]/20' : 'hover:bg-[color:var(--accent-solid)]/10'}`}
                onMouseDown={startWorkbenchResizing}
                style={{ left: `${rightPanelWidth + workbenchWidth}px` }}
              />
                <Workbench
                className="order-3 border-l-0"
                tabs={workbenchTabs}
                activeTabPath={activeWorkbenchPath}
                onTabClick={(path) => setActiveWorkbenchPath(path)}
                onTabClose={closeWorkbenchTab}
                onCloseAll={() => {
                  setWorkbenchTabs([]);
                  setActiveWorkbenchPath(null);
                  setWorkbenchShowDiffByPath({});
                  setWorkbenchDiffByPath({});
                  setWorkbenchDiffErrorByPath({});
                  setWorkbenchDiffBaseRefByPath({});
                  setWorkbenchGitRootsByPath({});
                  setWorkbenchLoadingPath(null);
                  setWorkbenchDiffLoadingPath(null);
                }}
                showExplorer={false}
                explorerEntries={runtimeFiles?.entries || []}
                currentExplorerPath={runtimePath}
                  explorerLoading={runtimeFilesLoading}
                  onExplorerFileClick={(entry) => void openRuntimeFile(entry.path)}
                  onExplorerDownload={(entry) => void downloadRuntimeEntry(entry)}
                  loadExplorerDirectory={loadRuntimeDirectoryEntries}
                onExplorerDirectoryToggle={(entry, expanded) => {
                  if (!activeSessionId || !entry.is_git_root) return;
                  if (!expanded) {
                    setRuntimeRepoChangesByRoot((current) => {
                      const next = { ...current };
                      delete next[entry.path];
                      return next;
                    });
                    setRuntimeRepoChangesLoadingByRoot((current) => {
                      const next = { ...current };
                      delete next[entry.path];
                      return next;
                    });
                    setRuntimeExpandedGitDirs((current) => {
                      const next = { ...current };
                      Object.keys(next).forEach((key) => {
                        if (key === entry.path || key.startsWith(`${entry.path}/`)) delete next[key];
                      });
                      return next;
                    });
                    return;
                  }
                  void fetchRuntimeChangedFilesForRepo(activeSessionId, entry.path);
                }}
                repoChangesSections={runtimeRepoChangeSections}
                expandedGitDirs={runtimeExpandedGitDirs}
                onToggleGitDir={(path) => {
                  setRuntimeExpandedGitDirs((current) => ({ ...current, [path]: !(current[path] ?? false) }));
                }}
                onGitFileClick={(path) => void openRuntimeFileDiff(path)}
                diffMode={activeWorkbenchTab ? workbenchShowDiffByPath[activeWorkbenchTab.path] ?? false : false}
                setDiffMode={(enabled) => {
                  if (!activeWorkbenchTab) return;
                  setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: enabled }));
                  if (enabled && activeSessionId) {
                    void fetchRuntimeGitDiff(activeSessionId, activeWorkbenchTab.path);
                  }
                }}
                diffContent={activeWorkbenchDiff}
                diffLoading={workbenchDiffLoadingPath === activeWorkbenchTab?.path}
                diffError={activeWorkbenchDiffError}
                diffBaseRef={activeWorkbenchBaseRef}
                onDiffBaseRefChange={(ref) => {
                  if (!activeWorkbenchTab) return;
                  setWorkbenchDiffBaseRefByPath((current) => ({
                    ...current,
                    [activeWorkbenchTab.path]: ref,
                  }));
                  if (activeSessionId && workbenchShowDiffByPath[activeWorkbenchTab.path]) {
                    void fetchRuntimeGitDiff(activeSessionId, activeWorkbenchTab.path, {
                      baseRef: ref,
                    });
                  }
                }}
                diffBaseRefOptions={activeWorkbenchBaseRefOptions}
                width={workbenchWidth}
              />
            </>
          ) : null}

          {/* Left Rail Resize Handle */}
          <div
              className="order-2 relative hidden xl:block w-0 shrink-0"
          />
          <div
              className={`absolute inset-y-0 z-40 hidden xl:block w-3 -translate-x-1/2 cursor-col-resize transition-colors before:absolute before:left-1 before:right-1 before:top-[47px] before:h-px before:bg-[color:var(--border-subtle)] ${isResizing ? 'bg-[color:var(--accent-solid)]/20' : 'hover:bg-[color:var(--accent-solid)]/10'}`}
              onMouseDown={startResizing}
              style={{ left: `${rightPanelWidth}px` }}
          />

          {/* Tool Rail */}
          <aside
              style={{ width: `${rightPanelWidth}px` }}
              className="order-1 relative z-30 hidden xl:flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden"
          >
            <div className="relative">
              <div className="flex h-12 items-center px-3">
                <div
                  className="relative grid w-full gap-0 rounded-full border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)] overflow-hidden"
                  style={{ gridTemplateColumns: `repeat(${rightRailTabs.length}, minmax(0, 1fr))` }}
                >
                  {/* Sliding Indicator */}
                  <div
                    className="absolute top-0.5 bottom-0.5 rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out"
                    style={{
                      width: `calc(${100 / rightRailTabs.length}% - 1px)`,
                      left: rightRailActiveIndex === 0 ? '2px' : `calc(${(100 / rightRailTabs.length) * rightRailActiveIndex}% + 1px)`,
                    }}
                  />

                  {rightRailTabs.map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setRightRailTab(tab.id)}
                      className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                        rightRailTab === tab.id
                          ? 'text-[color:var(--text-primary)]'
                          : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Runtime sub-segmented control: Desktop / Terminals / Files.
                Only rendered when the Runtime tab is active. Matches the
                primary tab strip's pill styling so the two reads as a
                hierarchy without screaming for attention. */}
            {isRuntimeTab ? (
              <div className="px-3 pb-2">
                {/* Flex (not grid) layout: each pill sizes to content, so a
                    label + icon + badge never overflow its cell. The sliding
                    indicator is positioned by measured offsetLeft/Width via
                    a useLayoutEffect above — that's what keeps it locked to
                    the active button regardless of rail width or badge count.
                    Buttons get `flex-1 min-w-0` so they share remaining
                    space evenly when the strip is wider than the natural
                    content; on narrow widths they shrink toward content. */}
                <div
                  ref={runtimeStripRef}
                  className="relative flex items-stretch gap-1 rounded-full border border-[color:var(--border-subtle)] p-1 bg-[color:var(--surface-2)]"
                >
                  {runtimeIndicator ? (
                    <div
                      aria-hidden
                      className="pointer-events-none absolute top-1 bottom-1 rounded-full bg-[color:var(--surface-0)] shadow-sm transition-[left,width] duration-300 ease-out"
                      style={{ left: runtimeIndicator.left, width: runtimeIndicator.width }}
                    />
                  ) : null}
                  {([
                    { id: 'desktop' as const, label: 'Desktop', Icon: Globe },
                    { id: 'terminals' as const, label: 'Terminals', Icon: Terminal },
                    { id: 'files' as const, label: 'Files', Icon: Folder },
                  ]).map(({ id, label, Icon }) => {
                    const active = runtimeView === id;
                    const badge = id === 'terminals' && activeTerminals.length > 0
                      ? activeTerminals.length
                      : null;
                    return (
                      <button
                        key={id}
                        ref={(el) => { runtimeTabRefs.current[id] = el; }}
                        type="button"
                        onClick={() => openRuntimeView(id)}
                        className={`relative z-10 flex-1 min-w-0 inline-flex items-center justify-center gap-1.5 h-7 px-2.5 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                          active
                            ? 'text-[color:var(--text-primary)]'
                            : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                        }`}
                        title={label}
                      >
                        <Icon size={11} className="shrink-0" />
                        <span className="truncate">{label}</span>
                        {badge !== null ? (
                          <span className="shrink-0 inline-flex items-center justify-center min-w-[16px] h-[15px] px-1 rounded-full bg-sky-500/20 text-sky-300 text-[9px] font-bold tracking-normal leading-none">
                            {badge}
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}

            {showDesktopView ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className={`relative flex items-center justify-between p-3 border-b border-[color:var(--border-subtle)] ${
                  isDesktopFullscreen ? 'z-10' : 'z-[110]'
                }`}>
                  <div className="flex items-center gap-2">
                    <Globe size={15} className="text-sky-500" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                      Interactive View
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* Reset dropdown */}
                    <div className={`relative ${isDesktopFullscreen ? 'z-10' : 'z-[120]'}`} ref={resetMenuRef}>
                      <button
                        onClick={() => setResetMenuOpen((o) => !o)}
                        disabled={isResettingRuntime || isRestartingContainer}
                        className="p-1.5 rounded-md hover:bg-[color:var(--surface-2)] transition-colors text-[color:var(--text-muted)] disabled:opacity-50 flex items-center gap-0.5"
                        title="Reset options"
                      >
                        {isResettingRuntime || isRestartingContainer
                          ? <RotateCcw size={14} className="animate-spin" />
                          : <RotateCcw size={14} />}
                        <ChevronDown size={10} />
                      </button>
                      {resetMenuOpen && (
                        <div
                          className={`absolute right-0 top-full mt-1 w-48 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shadow-xl overflow-hidden ${
                            isDesktopFullscreen ? 'z-10' : 'z-[130]'
                          }`}
                          onMouseLeave={() => setResetMenuOpen(false)}
                        >
                          <button
                            className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left text-[11px] font-medium text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors"
                            onClick={() => { setResetMenuOpen(false); void resetRuntime(); }}
                          >
                            <RotateCcw size={13} className="text-rose-400 shrink-0" />
                            <div>
                              <div className="font-semibold">Reset Chromium</div>
                              <div className="text-[9px] text-[color:var(--text-muted)]">Restart browser only</div>
                            </div>
                          </button>
                          <div className="h-px bg-[color:var(--border-subtle)]" />
                          <button
                            className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left text-[11px] font-medium text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors"
                            onClick={() => { setResetMenuOpen(false); void restartContainer(); }}
                          >
                            <RefreshCw size={13} className="text-amber-400 shrink-0" />
                            <div>
                              <div className="font-semibold">Restart Runtime</div>
                              <div className="text-[9px] text-[color:var(--text-muted)]">Hard reset and reprovision</div>
                            </div>
                          </button>
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => setIsDesktopFullscreen(true)}
                      className="p-1.5 rounded-md hover:bg-[color:var(--surface-2)] transition-colors text-sky-500"
                      title="Open fullscreen"
                    >
                      <Expand size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  <div className="relative w-full aspect-video overflow-hidden border-b border-[color:var(--border-subtle)] bg-black">
                    <DesktopPreview
                      url={liveView?.enabled && liveView?.available ? liveView.url : null}
                      isFullscreen={isDesktopFullscreen}
                      onClose={() => setIsDesktopFullscreen(false)}
                      isBooting={isDesktopRuntimeStarting && !(liveView?.enabled && liveView?.available)}
                      layoutKey={mode}
                      onFrameLoad={handleDesktopFrameLoad}
                      onInteract={handleDesktopInteract}
                    />
                  </div>

                  <div className="p-3 space-y-4">
                    <section>
                      <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-2.5 px-1">Desktop Status</div>
                      <div className="space-y-1.5">
                        <div className="flex items-center justify-between p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 transition-all hover:bg-[color:var(--surface-1)]">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)]">Connection</span>
                          <div className="flex items-center gap-2">
                            {liveView?.enabled && liveView.available ? (
                              <>
                                <div className="h-1.5 w-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]" />
                                <span className="text-[10px] font-mono font-bold text-emerald-500">CONNECTED</span>
                              </>
                            ) : isDesktopRuntimeStarting ? (
                              <>
                                <div className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-pulse" />
                                <span className="text-[10px] font-mono font-bold text-sky-400">STARTING</span>
                              </>
                            ) : (
                              <>
                                <div className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                                <span className="text-[10px] font-mono font-bold text-rose-500 uppercase">
                                  {liveView?.enabled ? 'UNREACHABLE' : 'DISABLED'}
                                </span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 transition-all hover:bg-[color:var(--surface-1)]">
                          <div className="flex items-center justify-between gap-3">
                            <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] shrink-0">Stream URL</span>
                            <div className="flex-1 min-w-0 font-mono text-[10px] text-[color:var(--text-secondary)] truncate text-right">
                              {liveView?.url || '—'}
                            </div>
                          </div>
                          {liveView?.reason && (
                            <div className="mt-1 text-[9px] font-medium text-amber-500/80 leading-relaxed italic text-right truncate">
                              {liveView.reason}
                            </div>
                          )}
                        </div>
                      </div>
                    </section>

                    <section>
                      <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-2.5 px-1">Runtime Provider</div>
                      <div className="space-y-1.5">
                        <div className="p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 transition-all hover:bg-[color:var(--surface-1)]">
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <div className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)]">
                                {liveView?.provider?.label || 'Runtime'}
                              </div>
                              <div className="mt-1 text-[10px] text-[color:var(--text-muted)] leading-relaxed">
                                {liveView?.provider?.summary || 'Provider details unavailable.'}
                              </div>
                            </div>
                            <span className="shrink-0 text-[10px] font-mono font-bold uppercase text-[color:var(--text-primary)]">
                              {liveView?.provider?.status || 'UNKNOWN'}
                            </span>
                          </div>
                        </div>
                        {liveView?.provider?.items?.length ? (
                          <div className="space-y-1.5">
                            {liveView.provider.items.map((item) => (
                              <div
                                key={item.key}
                                className="p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 transition-all hover:bg-[color:var(--surface-1)]"
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] shrink-0">
                                    {item.label}
                                  </span>
                                  <div className="flex-1 min-w-0 font-mono text-[10px] text-[color:var(--text-secondary)] truncate text-right">
                                    {item.value || '—'}
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </section>

                    <section>
                      <div className="flex items-center justify-between mb-2.5 px-1">
                        <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Recent Actions</div>
                      </div>
                      {browserToolResults.length > 0 ? (
                        <div className="space-y-1">
                          {browserToolResults.slice(0, 12).map((item) => (
                            <div
                              key={item.id}
                              className="flex items-center justify-between p-2.5 rounded-lg border border-transparent hover:border-[color:var(--border-subtle)] hover:bg-[color:var(--surface-1)] transition-all group active:scale-[0.99]"
                            >
                              <div className="flex items-center gap-3 min-0">
                                <div className="w-1.5 h-1.5 rounded-full bg-[color:var(--accent-solid)] opacity-20 group-hover:opacity-100 transition-opacity" />
                                <span className="text-[11px] font-bold text-[color:var(--text-primary)] truncate capitalize">
                                  {browserCommandLabelFromArguments(toolArgumentsByCallId.get(item.tool_call_id || ''))}
                                </span>
                              </div>
                              <span className="text-[9px] font-mono text-[color:var(--text-muted)] shrink-0 opacity-60 group-hover:opacity-100">
                                {formatCompactDate(item.created_at)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="py-8 text-center text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] opacity-40">
                          No activity yet.
                        </div>
                      )}
                    </section>
                  </div>
                </div>
              </div>
            ) : null}

            {showTerminalsView ? (
              <div className="flex-1 min-h-0 flex flex-col">
                {activeTerminals.length > 1 ? (
                  <div className="flex items-center gap-1 overflow-x-auto custom-scrollbar px-3 py-2 border-b border-[color:var(--border-subtle)]">
                    {activeTerminals.map((terminal) => {
                      const display = getTerminalLabel(terminal.id, terminal.lastCommand ?? terminal.label);
                      const isFocused = (focusedTerminalId ?? activeTerminals[0]?.id) === terminal.id;
                      return (
                        <button
                          key={terminal.id}
                          type="button"
                          onClick={() => setFocusedTerminalId(terminal.id)}
                          className={`shrink-0 inline-flex items-center gap-1 rounded-md px-2 h-6 text-[10px] font-medium transition-colors ${
                            isFocused
                              ? 'bg-sky-500/20 text-sky-300'
                              : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                          }`}
                          title={display}
                        >
                          <span className="max-w-[120px] truncate">{display}</span>
                          {terminal.busy ? <Loader2 size={9} className="animate-spin" /> : null}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
                <div className="flex-1 min-h-0">
                  {activeSessionId && (focusedTerminalId ?? activeTerminals[0]?.id) ? (
                    // Keyed on the (session, terminal) pair so React tears down
                    // and re-creates the xterm + WS when the user switches tabs;
                    // sharing state across terminals would mix scrollback.
                    <TerminalPreview
                      key={`${activeSessionId}:${focusedTerminalId ?? activeTerminals[0].id}`}
                      sessionId={activeSessionId}
                      terminalId={focusedTerminalId ?? activeTerminals[0].id}
                      instanceName={activeInstanceName ?? ''}
                    />
                  ) : (
                    // Empty state mirrors the Agents tab's idle treatment —
                    // muted icon block + short subtitle — so the runtime sub
                    // views feel like a family even when empty.
                    <div className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center text-[color:var(--text-muted)] opacity-60">
                      <div className="p-3 rounded-2xl bg-[color:var(--surface-2)]">
                        <Terminal size={24} strokeWidth={1} />
                      </div>
                      <p className="text-[10px] font-medium uppercase tracking-widest">No terminals open</p>
                      <p className="text-[10px] leading-relaxed max-w-[220px]">
                        The agent will open one here when it runs a shell command.
                      </p>
                    </div>
                  )}
                </div>
              </div>
            ) : null}

            {rightRailTab === 'sub_agents' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex-1 overflow-y-auto p-3 space-y-2.5 custom-scrollbar">
                  {tasks.map(t => (
                    <div key={t.id} className="group p-3.5 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--border-strong)] transition-all shadow-sm">
                      <div className="flex items-center justify-between gap-3 mb-2">
                        <span className="text-xs font-bold text-[color:var(--text-primary)] truncate">{t.name}</span>
                        <div className="shrink-0 scale-90 origin-right">
                          <StatusChip label={t.status} tone={taskStatusTone(t.status)} />
                        </div>
                      </div>
                      <p className="text-[10px] text-[color:var(--text-secondary)] line-clamp-2 leading-relaxed mb-3">
                        {t.scope || 'No scope defined.'}
                      </p>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => { setSelectedTask(t); setIsTaskModalOpen(true); }}
                          className="flex-1 inline-flex items-center justify-center h-7 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[10px] font-bold uppercase tracking-wide text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] transition-all active:scale-95"
                        >
                          View Task
                        </button>
                        {(t.status === 'running' || t.status === 'pending') && (
                          <button
                            onClick={() => terminateTask(t.id)}
                            className="inline-flex items-center justify-center h-7 px-3 rounded-full border border-rose-500/20 bg-rose-500/5 text-rose-500 text-[10px] font-bold uppercase tracking-wide hover:bg-rose-500 hover:text-white transition-all active:scale-95"
                          >
                            Terminate
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                  {tasks.length === 0 && (
                    <div className="py-12 flex flex-col items-center justify-center text-[color:var(--text-muted)] opacity-40 gap-3">
                      <div className="p-3 rounded-2xl bg-[color:var(--surface-2)]">
                        <Terminal size={24} strokeWidth={1} />
                      </div>
                      <p className="text-[10px] font-medium uppercase tracking-widest">Idle</p>
                    </div>
                  )}
                </div>
                <div className="p-3 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 backdrop-blur">
                  <button
                    onClick={() => setIsSpawnModalOpen(true)}
                    className="w-full flex items-center justify-center gap-2 h-10 rounded-full bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] text-[11px] font-bold uppercase tracking-[0.1em] hover:opacity-90 transition-all active:scale-[0.98] shadow-md shadow-black/5"
                  >
                    <Plus size={14} />
                    Spawn Sub-Agent
                  </button>
                </div>
              </div>
            ) : null}

            {showFilesView ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <WorkbenchExplorerPane
                  showTitle={false}
                  currentPath={runtimePath}
                  explorerLoading={runtimeFilesLoading}
                  explorerEntries={runtimeFiles?.entries || []}
                  onExplorerFileClick={(entry) => void openRuntimeFile(entry.path)}
                  onExplorerDownload={(entry) => void downloadRuntimeEntry(entry)}
                  loadExplorerDirectory={loadRuntimeDirectoryEntries}
                  onExplorerDirectoryToggle={(entry, expanded) => {
                    if (!activeSessionId || !entry.is_git_root) return;
                    if (!expanded) {
                      setRuntimeRepoChangesByRoot((current) => {
                        const next = { ...current };
                        delete next[entry.path];
                        return next;
                      });
                      setRuntimeRepoChangesLoadingByRoot((current) => {
                        const next = { ...current };
                        delete next[entry.path];
                        return next;
                      });
                      setRuntimeExpandedGitDirs((current) => {
                        const next = { ...current };
                        Object.keys(next).forEach((key) => {
                          if (key === entry.path || key.startsWith(`${entry.path}/`)) delete next[key];
                        });
                        return next;
                      });
                      return;
                    }
                    void fetchRuntimeChangedFilesForRepo(activeSessionId, entry.path);
                  }}
                  repoChangesSections={runtimeRepoChangeSections}
                  expandedGitDirs={runtimeExpandedGitDirs}
                  onToggleGitDir={(path) => {
                    setRuntimeExpandedGitDirs((current) => ({ ...current, [path]: !(current[path] ?? false) }));
                  }}
                  onGitFileClick={(path) => void openRuntimeFileDiff(path)}
                />
              </div>
            ) : null}

            {rightRailTab === 'sessions' ? (
              <div className="flex-1 min-h-0">
                <SessionHistorySidebar
                  showHeaderTitle={false}
                  historyTab={historyTab}
                  setHistoryTab={setHistoryTab}
                  sessionFilter={sessionFilter}
                  setSessionFilter={setSessionFilter}
                  isMultiSelectMode={isMultiSelectMode}
                  setIsMultiSelectMode={setIsMultiSelectMode}
                  selectedSessionIds={selectedSessionIds}
                  setSelectedSessionIds={setSelectedSessionIds}
                  allVisibleSelected={allVisibleSelected}
                  selectableVisibleSessionIds={selectableVisibleSessionIds}
                  deleteSelectedSessions={deleteSelectedSessions}
                  deletingSessionId={deletingSessionId}
                  filteredSessions={filteredSessions}
                  activeSessionId={activeSessionId}
                  onSessionClick={onSessionClick}
                  defaultSessionId={defaultSessionId}
                  editingSessionId={editingSessionId}
                  editingSessionTitle={editingSessionTitle}
                  setEditingSessionTitle={setEditingSessionTitle}
                  submitRenameSession={submitRenameSession}
                  cancelRenameSession={cancelRenameSession}
                  startRenameSession={startRenameSession}
                  setMainSession={setMainSession}
                  deleteSession={deleteSession}
                  renamingSessionId={renamingSessionId}
                  loadingSessions={false}
                />
              </div>
            ) : null}

            {SESSION_DEBUG_PANEL_ENABLED && rightRailTab === 'debug' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-[color:var(--border-subtle)]">
                  <div className="flex items-center gap-2">
                    <Activity size={15} className="text-amber-400" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                      Session Debug
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDebugEvents([])}
                    className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]"
                  >
                    Clear
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
                  <section>
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">
                      Render Gates
                    </div>
                    <pre className="overflow-auto rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-3 text-[11px] leading-relaxed text-[color:var(--text-secondary)]">{JSON.stringify({
                      sessionId: activeSessionId,
                      connection: streaming.connection,
                      streamBusy,
                      showThinkingIndicator,
                      runtimeBooting,
                      rightRailTab,
                      activeToolCalls: streaming.activeToolCalls.length,
                      completedToolCalls: streaming.completedToolCalls.length,
                      timelineItems: streaming.timeline.length,
                      hasVisibleStreamingText: hasVisibleStreamingText(streaming.text),
                      rawStreamingText: streaming.text,
                    }, null, 2)}</pre>
                  </section>

                  <section>
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">
                      Streaming State
                    </div>
                    <pre className="overflow-auto rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-3 text-[11px] leading-relaxed text-[color:var(--text-secondary)]">{JSON.stringify(streaming, null, 2)}</pre>
                  </section>

                  <section>
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">
                      Incoming WS Events
                    </div>
                    <div className="overflow-auto rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]">
                      {debugEvents.length === 0 ? (
                        <div className="px-4 py-3 text-[11px] text-[color:var(--text-muted)]">No events captured yet.</div>
                      ) : (
                        <div className="divide-y divide-[color:var(--border-subtle)]">
                          {debugEvents.map((entry) => (
                            <div key={entry.id} className="px-4 py-2.5 font-mono text-[11px] leading-relaxed">
                              <div className="flex items-center gap-2">
                                <span className="text-amber-400">{entry.type}</span>
                                <span className="text-[color:var(--text-muted)]">{entry.at.split('T')[1]?.replace('Z', '') ?? entry.at}</span>
                              </div>
                              {entry.summary ? (
                                <div className="mt-1 whitespace-pre-wrap break-words text-[color:var(--text-secondary)]">{entry.summary}</div>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </section>
                </div>
              </div>
            ) : null}
          </aside>
        </div>

        {isTaskModalOpen && selectedTask && (
            <SubAgentTaskModal
                task={selectedTask}
                onClose={() => setIsTaskModalOpen(false)}
                onTerminate={terminateTask}
                isTerminating={isTerminatingTask}
            />
        )}

        {isSpawnModalOpen && (
            <SpawnSubAgentModal
                onClose={() => setIsSpawnModalOpen(false)}
                onSpawn={spawnSubAgent}
                isSpawning={isSpawning}
            />
        )}

        {confirmTerminateTaskId && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setConfirmTerminateTaskId(null)} />
            <div className="relative rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] shadow-2xl p-6 w-full max-w-sm animate-in zoom-in-95 duration-150 space-y-4">
              <h2 className="text-sm font-bold uppercase tracking-widest">Terminate Sub-Agent?</h2>
              <p className="text-xs text-[color:var(--text-secondary)] leading-relaxed">
                This will immediately cancel the running task. Any in-progress work will be lost.
              </p>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setConfirmTerminateTaskId(null)} className="btn-secondary h-8 px-4 text-xs">Cancel</button>
                <button onClick={confirmTerminate} disabled={isTerminatingTask} className="btn-primary h-8 px-4 text-xs bg-rose-500 hover:bg-rose-600 border-rose-500">
                  {isTerminatingTask ? 'Terminating…' : 'Terminate'}
                </button>
              </div>
            </div>
          </div>
        )}
      </AppShell>
  );
}
