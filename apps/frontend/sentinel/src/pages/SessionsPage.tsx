import {
  ArrowDown,
  Bot,
  ChevronDown,
  Loader2,
  Plus,
  RefreshCw,
  Send,
  Users,
  Square,
  Wand2,
  Wrench,
  X,
  Terminal,
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
import { useSessionDeleteConfirmation } from '../components/session/SessionDeleteConfirmDialog';
import { getTerminalLabel, summarizeCommand } from '../lib/terminalIdentity';
import { SubAgentTaskModal } from '../components/SubAgentTaskModal';
import { SpawnSubAgentModal } from '../components/SpawnSubAgentModal';
import { Markdown } from '../components/ui/Markdown';
import { Workbench } from '../components/workbench/Workbench';
import { StatusChip } from '../components/ui/StatusChip';
import { SESSION_DEBUG_PANEL_ENABLED } from '../lib/env';
import { toPrettyJson, truncate } from '../lib/format';
import {
  approvalKey,
  approvalRefFromMetadata,
  isWaitingApproval,
  type ApprovalRef,
} from '../lib/approvals';
import { api } from '../lib/api';
import { useInstanceName, useWorkspaceMode } from '../lib/workspace-context';
import { useAnchorRect } from '../lib/portal-menu';
import { instanceRouteFromPath } from '../lib/routes';
import { getSessionDeleteWorkspaceSummary } from '../lib/sessionDeletion';
import { useSetActiveSession } from '../store/active-session-store';
import { useSessionRuntimeStream } from '../hooks/useSessionRuntimeStream';
import { useSessionWorkbench } from '../hooks/useSessionWorkbench';
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
  Session,
  SessionContextUsage,
  SessionListResponse,
  SubAgentTask,
  SubAgentTaskListResponse,
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
  runtime_id: string | null;
}

// Top-level right-rail tabs. The Desktop / Terminals / Files runtime surfaces
// moved out of SessionsPage into standalone workspace tabs (DesktopTab /
// TerminalTab / FilesTab), so the rail keeps only the chat-adjacent panels:
// sub-agent tasks, session history, and the debug panel.
type RightRailTab = 'sub_agents' | 'sessions' | 'debug';

// ActiveTerminal lives in useSessionRuntimeStream; the diff base-ref option
// builder lives in useSessionWorkbench.

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
  const workspaceMode = useWorkspaceMode();
  const { id: routeSessionId } = useParams<{ id: string }>();
  const sessionRoute = useCallback(
    (sessionId?: string | null) => instanceRouteFromPath(location.pathname, sessionId ? `sessions/${sessionId}` : 'sessions'),
    [location.pathname],
  );
  const instanceName = useInstanceName();
  const activeInstanceName = instanceName ?? null;
  // In a tiling pane the page is not the active route, so route-driven session
  // selection would hijack the global URL. Keep selection local instead.
  const selectSession = useCallback(
    (sessionId: string | null, options?: { replace?: boolean }) => {
      if (workspaceMode) return;
      navigate(sessionRoute(sessionId), options);
    },
    [workspaceMode, navigate, sessionRoute],
  );

  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [defaultSessionId, setDefaultSessionId] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const { confirmSessionDelete, sessionDeleteConfirmDialog } = useSessionDeleteConfirmation();
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
  const [rightRailTab, setRightRailTab] = useState<RightRailTab>('sessions');
  // Pills above the chat composer surface every tmux-backed terminal the
  // agent (or the user) has opened in the current chat session. State is
  // driven by `terminal_opened/closed/busy` WS events plus the initial
  // `connected` payload that lists already-live terminals on page load.
  //
  // The terminal list, the noVNC live-view payload, runtime status, and the
  // desktop-resolution actions all live in `useSessionRuntimeStream`, which
  // owns the SHARED, ref-counted session WS stream. The Desktop / Terminals /
  // Files surfaces themselves now live in standalone workspace tabs that
  // consume the same shared hooks; SessionsPage only keeps the chat-adjacent
  // pieces (terminal pills + the chat stream tap). The chat stream consumes the
  // socket through this hook's `onEvent` tap — one socket per session.
  const setActiveSession = useSetActiveSession();

  // Per-session runtime stream. Owns the shared, ref-counted WS connection; the
  // chat stream taps the same socket via `onEvent`. The standalone runtime tabs
  // subscribe to the same (instance, session) pair, so opening them reuses this
  // socket. `desktopViewActive` is false here — SessionsPage no longer hosts the
  // desktop surface, so it must not drive the live-view poll.
  const runtime = useSessionRuntimeStream(activeInstanceName, activeSessionId, {
    desktopViewActive: false,
    onReconnectFailed: () => {
      toast.error('Realtime stream disconnected');
    },
    onEvent: (event) => {
      onStreamEvent(activeSessionIdRef.current ?? '', event);
    },
  });
  const {
    connection: runtimeConnection,
    isStreamOpen,
    sendMessage: sendStreamMessage,
    activeTerminals,
    focusedTerminalId,
    setFocusedTerminalId,
    dropTerminal,
    setLiveView,
    runtimeBooting,
    setRuntimeBooting,
  } = runtime;

  // Files / workbench surface (directory browse, open-file view, diff, repo
  // changes, download). Owned by the shared workbench hook; SessionsPage just
  // wires its props. Stale-session guarding lives inside the hook.
  const workbench = useSessionWorkbench(activeSessionId);
  const {
    runtimeFiles,
    runtimePath,
    runtimeFilesLoading,
    runtimeFilesRefreshKey,
    fetchRuntimeFiles,
    bumpRuntimeFilesRefreshKey,
    loadRuntimeDirectoryEntries,
    downloadRuntimeEntry,
    repoChangeSections: runtimeRepoChangeSections,
    expandedGitDirs: runtimeExpandedGitDirs,
    toggleGitDir,
    fetchChangedFilesForRepo,
    forgetRepoRoot,
    workbenchTabs,
    activeWorkbenchPath,
    setActiveWorkbenchPath,
    activeWorkbenchTab,
    openRuntimeFile,
    openRuntimeFileDiff,
    closeWorkbenchTab,
    closeAllWorkbenchTabs,
    workbenchShowDiffByPath,
    setShowDiffForPath,
    activeWorkbenchDiff,
    activeWorkbenchDiffError,
    activeWorkbenchDiffLoading,
    activeWorkbenchBaseRef,
    setDiffBaseRefForPath,
    activeWorkbenchBaseRefOptions,
  } = workbench;
  const [workbenchWidth, setWorkbenchWidth] = useState(442);
  const [isWorkbenchResizing, setIsWorkbenchResizing] = useState(false);
  const [spawnObjective, setSpawnObjective] = useState('');
  const [spawnScope, setSpawnScope] = useState('');
  const [spawnMaxSteps, setSpawnMaxSteps] = useState(5);
  const [isSpawning, setIsSpawning] = useState(false);

  const [selectedTask, setSelectedTask] = useState<SubAgentTask | null>(null);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState(false);
  const [isSpawnModalOpen, setIsSpawnModalOpen] = useState(false);
  const [isTerminatingTask, setIsTerminatingTask] = useState(false);
  const [confirmTerminateTaskId, setConfirmTerminateTaskId] = useState<string | null>(null);

  const [debugMenuOpen, setDebugMenuOpen] = useState(false);
  const [debugEvents, setDebugEvents] = useState<SessionDebugEvent[]>([]);
  const [mode, setMode] = useState<'solo' | 'advanced'>(
      () => (localStorage.getItem('sentinel-mode') as 'solo' | 'advanced') ?? 'advanced',
  );

  const hasActiveSubAgentTasks = tasks.some((task) => task.status === 'running' || task.status === 'pending');

  // Mirror the shared stream's connection state into the chat `streaming`
  // state so existing UI that reads `streaming.connection` stays accurate now
  // that the WebSocket lives in the runtime hook rather than inline here.
  useEffect(() => {
    setStreaming((current) =>
      current.connection === runtimeConnection ? current : { ...current, connection: runtimeConnection },
    );
  }, [runtimeConnection]);

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
      if (effortDropdownMenuRef.current?.contains(target)) return;
      if (agentModeDropdownRef.current?.contains(target)) return;
      if (agentModeDropdownMenuRef.current?.contains(target)) return;
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
  const [rightPanelWidth, setRightPanelWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);
  const [statusTooltip, setStatusTooltip] = useState<'connection' | 'progress' | 'context' | null>(null);

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
  // The session WebSocket + reconnect now live in the shared stream manager
  // (see `useSessionRuntimeStream` / `session-stream.ts`); no inline ws/reconnect
  // refs remain here.
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const sessionDropdownRef = useRef<HTMLDivElement | null>(null);
  const sessionDropdownButtonRef = useRef<HTMLButtonElement | null>(null);
  const sessionDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const effortDropdownRef = useRef<HTMLDivElement | null>(null);
  const effortDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const connectionPillRef = useRef<HTMLDivElement | null>(null);
  const progressPillRef = useRef<HTMLDivElement | null>(null);
  const contextPillRef = useRef<HTMLDivElement | null>(null);
  const activeSessionIdRef = useRef<string | null>(routeSessionId ?? null);
  const contextUsageRequestRef = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const effortDropdownRect = useAnchorRect(effortDropdownRef, isEffortDropdownOpen);
  const agentModeDropdownRect = useAnchorRect(agentModeDropdownRef, isAgentModeDropdownOpen);
  const connectionTooltipRect = useAnchorRect(connectionPillRef, statusTooltip === 'connection');
  const progressTooltipRect = useAnchorRect(progressPillRef, statusTooltip === 'progress');
  const contextTooltipRect = useAnchorRect(contextPillRef, statusTooltip === 'context');

  // Keep refs in sync so WS callbacks can read current values
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => {
    setRetryCandidate(null);
    setRetryingMessageId(null);
  }, [activeSessionId]);

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
      // Two primary tabs: Agents (sub-agent tasks) and Sessions (history). The
      // Desktop/Terminals/Files runtime surfaces moved out into standalone
      // workspace tabs, so the rail no longer hosts a composite Runtime tab.
      const tabs: Array<{ id: RightRailTab; label: string }> = [
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

  // activeWorkbenchTab / activeWorkbenchDiff / activeWorkbenchDiffError /
  // activeWorkbenchBaseRef / activeWorkbenchBaseRefOptions are provided by
  // useSessionWorkbench (destructured above). Only the visibility flag is local.
  const workbenchVisible = workbenchTabs.length > 0;

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
    const dest = workspaceMode ? 'workspace' : 'sessions';
    navigate(`/instances/${encodeURIComponent(instanceName)}/${dest}`);
  }, [activeInstanceName, navigate, workspaceMode]);

  const onSessionClick = useCallback((id: string) => {
    const previousId = activeSessionIdRef.current;
    if (previousId) {
      markSessionRead(previousId);
    }
    setRuntimeBooting(true);
    setActiveSessionId(id);
    selectSession(id);
    markSessionRead(id);
  }, [markSessionRead, selectSession]);

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
    // In a tiling pane the route id is not the selected session; local state owns it.
    if (workspaceMode) return;
    if (routeSessionId && routeSessionId !== activeSessionId) {
      setRuntimeBooting(true);
      setActiveSessionId(routeSessionId);
    }
  }, [workspaceMode, routeSessionId, activeSessionId]);

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
    // Live-view + runtime-status fetching (incl. the session-change re-fetch, the
    // booting poll, and the desktop-visibility status refresh) is owned by
    // `useSessionRuntimeStream`.
  }, [activeInstanceName]);

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
    // Runtime/workbench state (files, repo changes, workbench tabs, diffs) is
    // reset by `useSessionWorkbench` on session change; terminals + live view by
    // `useSessionRuntimeStream`; the WS connection by the shared stream manager.
    // This effect now only owns chat state (messages / context / tasks /
    // streaming) and re-fetches the initial runtime file tree.
    if (!activeSessionId) {
      setMessages([]);
      setContextTokenEstimate(null);
      setContextTokenPercent(null);
      setTasks([]);
      setStreaming(defaultStreamingState);
      shouldAutoScrollRef.current = true;
      lastScrollTopRef.current = 0;
      setIsPinnedToBottom(true);
      oldestServerMessageIdRef.current = null;
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
      return;
    }

    // Clear messages immediately to avoid showing stale content
    setMessages([]);
    setContextTokenEstimate(null);
    setContextTokenPercent(null);
    setTasks([]);
    setStreaming(defaultStreamingState);
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
    // Seed the workbench file tree for this session. The standalone Files tab
    // owns the live refresh loop; here we only need the initial listing so any
    // open-file / diff actions from the chat workbench resolve against it.
    void fetchRuntimeFiles('', { refreshGit: false });
  }, [activeSessionId]);

  // Publish the page's selected session to the workspace-wide active-session
  // store so the standalone Desktop / Terminal / Files tabs follow the session
  // selected here.
  useEffect(() => {
    setActiveSession(activeSessionId);
  }, [activeSessionId, setActiveSession]);

  useEffect(() => {
    if (!activeSessionId || !hasActiveSubAgentTasks) return;
    const timer = window.setInterval(() => {
      void fetchTasks(activeSessionId);
    }, 1500);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, hasActiveSubAgentTasks]);

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
        selectSession(defaultSession.id, { replace: true });
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
    setDeletingSessionId(session.id);
    try {
      const label = (session.title || 'Session').trim() || 'Session';
      const workspaceSummary = await getSessionDeleteWorkspaceSummary(session.id);
      if (workspaceSummary.needsConfirmation) {
        const confirmed = await confirmSessionDelete({
          kind: 'single',
          label,
          topLevelEntries: workspaceSummary.topLevelEntries,
        });
        if (!confirmed) return;
      }

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
            ? remaining.find((item) => item.id === defaultSessionId)?.id
            : null) ?? remaining[0]?.id ?? null;
        setActiveSessionId(fallbackId);
        selectSession(fallbackId, { replace: true });
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
      selectSession(updated.id);
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

    setDeletingSessionId('bulk');
    try {
      const workspaceSummaries = await Promise.all(
        targetIds.map((id) => getSessionDeleteWorkspaceSummary(id)),
      );
      const nonEmptyWorkspaceCount = workspaceSummaries.filter((summary) => summary.needsConfirmation).length;
      if (nonEmptyWorkspaceCount > 0) {
        const topLevelEntries = Array.from(
          new Set(workspaceSummaries.flatMap((summary) => summary.topLevelEntries)),
        ).slice(0, 10);
        const confirmed = await confirmSessionDelete({
          kind: 'bulk',
          sessionCount: targetIds.length,
          workspaceSessionCount: nonEmptyWorkspaceCount,
          topLevelEntries,
        });
        if (!confirmed) return;
      }

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
              ? remaining.find((session) => session.id === defaultSessionId)?.id
              : null) ?? remaining[0]?.id ?? null;
          setActiveSessionId(fallbackId);
          selectSession(fallbackId, { replace: true });
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
    } catch (error) {
      console.error('fetchAgentModes failed', error);
      setAgentModes([]);
      setSelectedAgentMode(null);
    }
  }

  // The live-view / runtime-status fetches and the desktop-resolution and
  // reset/restart/wipe runtime actions live with the standalone Desktop tab and
  // the shared `useSessionRuntimeStream` hook now. SessionsPage keeps only
  // `setLiveView` / `setRuntimeBooting` (used by resetSession + session-switch)
  // through that hook.

  async function resetSession() {
    try {
      const previousId = activeSessionId;
      // Null the active-session ref + wipe messages before the API call so the
      // shared stream's guarded handler drops any in-flight events from the old
      // session. Switching `activeSessionId` below re-subscribes the WS to the
      // new session (and unsubscribes the old) via the runtime stream hook.
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
      selectSession(fresh.id, { replace: true });
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

  // Files / workbench actions (fetchRuntimeFiles, loadRuntimeDirectoryEntries,
  // downloadRuntimeEntry, fetchChangedFilesForRepo, openRuntimeFile,
  // openRuntimeDirectory, openRuntimeFileDiff, closeWorkbenchTab,
  // fetchRuntimeGitDiff, ...) now live in `useSessionWorkbench`.

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

  // WebSocket connection + reconnect are owned by the shared, ref-counted stream
  // manager (`session-stream.ts`) and consumed via `useSessionRuntimeStream`.
  // `onStreamEvent` below is the chat-side handler, wired in as the hook's
  // `onEvent` tap so chat + runtime share ONE socket per session.

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
        // Terminal population from `event.terminals` is handled by
        // `useSessionRuntimeStream`; here we only own the chat-side payload
        // (context budget + message history).
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
        break;
      // terminal_opened / terminal_closed / terminal_busy and runtime_ready are
      // handled by useSessionRuntimeStream. SessionsPage does not opt into the
      // hook's onFirstTerminalOpened callback, so no terminal auto-focus here.
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
            toolNameForRefresh === 'git' ||
            toolNameForRefresh === 'str_replace_editor'
          ) {
            // Keep the chat workbench's file tree + repo changes fresh when a
            // file-mutating tool completes. The standalone Files tab runs its
            // own poll; this only feeds the workbench panel hosted here.
            bumpRuntimeFilesRefreshKey();
            void fetchRuntimeFiles(runtimePath, { refreshGit: true });
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
      // runtime_ready is handled by useSessionRuntimeStream (it re-fetches the
      // live view a few times until noVNC is reachable, clearing the booting
      // flag).
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

    if (isStreamOpen()) {
      setRetryCandidate(null);
      // Prepend time context if conversation has been idle for >30 minutes
      const lastMsg = messages.at(-1);
      const idleMs = lastMsg ? Date.now() - new Date(lastMsg.created_at).getTime() : 0;
      const now = new Date().toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
      const idleNote = idleMs > 30 * 60 * 1000
        ? `[Resuming after ${Math.round(idleMs / 60000)} min — current time: ${now}]\n\n`
        : '';
      sendStreamMessage({
        type: 'message',
        content: idleNote + content,
        attachments: composerAttachments,
        tier: selectedTier,
        max_iterations: maxIterations,
        agent_mode: selectedAgentMode ?? undefined,
      });
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

  // Optimistically drops the pill so the UI reacts immediately, then asks the
  // backend to kill the tmux session. The authoritative `terminal_closed` WS
  // event will also fire and is idempotent against the optimistic update.
  async function closeTerminal(terminalId: string) {
    if (!activeSessionId) return;
    dropTerminal(terminalId);
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
          hideHeader={mode === 'solo'}
          actions={
            mode === 'advanced' ? (
              <div className="flex w-full min-w-0 items-center gap-2">
                {/* Status */}
                <div className="flex min-w-0 shrink-0 items-center gap-2.5">
                  <div
                    ref={connectionPillRef}
                    onMouseEnter={() => setStatusTooltip('connection')}
                    onMouseLeave={() => setStatusTooltip((current) => current === 'connection' ? null : current)}
                    className="group relative flex items-center gap-2 px-2.5 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default"
                  >
                    <div className={`h-1.5 w-1.5 rounded-full transition-all duration-500 ${streaming.connection === 'connected' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]'}`} />
                    <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-[color:var(--text-secondary)]">{streaming.connection === 'connected' ? 'Live' : 'Offline'}</span>
                  </div>
                  {statusTooltip === 'connection' && connectionTooltipRect && createPortal(
                    <div
                      style={{
                        position: 'fixed',
                        top: connectionTooltipRect.top + 8,
                        left: Math.max(8, Math.min(connectionTooltipRect.left, window.innerWidth - 220)),
                        zIndex: 10000,
                      }}
                      className="px-3 py-2 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap pointer-events-none shadow-2xl animate-in fade-in slide-in-from-top-1 duration-150"
                    >
                      <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)] mb-1">Telemetry Link</div>
                      <div className="flex items-center gap-2">
                        <div className={`h-1.5 w-1.5 rounded-full ${streaming.connection === 'connected' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                        <span className={streaming.connection === 'connected' ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'}>{streaming.connection.toUpperCase()}</span>
                      </div>
                    </div>,
                    document.body,
                  )}

                  {streaming.agentMaxIterations > 0 && (
                    <div
                      ref={progressPillRef}
                      onMouseEnter={() => setStatusTooltip('progress')}
                      onMouseLeave={() => setStatusTooltip((current) => current === 'progress' ? null : current)}
                      className="group relative flex items-center gap-3 px-3 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default"
                    >
                      <div className="w-16 h-1 rounded-full bg-[color:var(--surface-3)] overflow-hidden">
                        <div
                          className="h-full rounded-full bg-[color:var(--accent-solid)] transition-all duration-700 ease-out"
                          style={{ width: `${Math.min((streaming.agentIteration / streaming.agentMaxIterations) * 100, 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono font-bold text-[color:var(--text-primary)]">
                        {streaming.agentIteration}<span className="text-[color:var(--text-muted)] mx-0.5">/</span>{streaming.agentMaxIterations}
                      </span>
                    </div>
                  )}
                  {statusTooltip === 'progress' && progressTooltipRect && streaming.agentMaxIterations > 0 && createPortal(
                    <div
                      style={{
                        position: 'fixed',
                        top: progressTooltipRect.top + 8,
                        left: Math.max(8, Math.min(progressTooltipRect.left, window.innerWidth - 300)),
                        zIndex: 10000,
                      }}
                      className="px-3 py-2 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap pointer-events-none shadow-2xl animate-in fade-in slide-in-from-top-1 duration-150"
                    >
                        <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)] mb-1">Execution Pipeline</div>
                        <div><span className="font-bold text-[color:var(--accent-solid)]">{streaming.agentIteration}</span><span className="text-[color:var(--text-muted)]"> of {streaming.agentMaxIterations} steps completed</span></div>
                        <div className="mt-1 h-1 w-full bg-[color:var(--surface-2)] rounded-full overflow-hidden">
                          <div className="h-full bg-[color:var(--accent-solid)]" style={{ width: `${(streaming.agentIteration / streaming.agentMaxIterations) * 100}%` }} />
                        </div>
                    </div>,
                    document.body,
                  )}

                {/* Context Indicator */}
                <div
                  ref={contextPillRef}
                  onMouseEnter={() => setStatusTooltip('context')}
                  onMouseLeave={() => setStatusTooltip((current) => current === 'context' ? null : current)}
                  className="group relative flex items-center gap-2.5 px-3 py-1.5 rounded-full bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] transition-all hover:bg-[color:var(--surface-2)] cursor-default"
                >
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
                        {statusTooltip === 'context' && contextTooltipRect && createPortal(
                        <div
                          style={{
                            position: 'fixed',
                            top: contextTooltipRect.top + 8,
                            left: Math.max(8, Math.min(contextTooltipRect.left, window.innerWidth - 320)),
                            zIndex: 10000,
                          }}
                          className="px-3 py-2.5 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] text-[10px] font-mono whitespace-nowrap pointer-events-none shadow-2xl animate-in fade-in slide-in-from-top-1 duration-150"
                        >
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
                        </div>,
                        document.body,
                        )}
                      </>
                    );
                  })()}
                </div>
              </div>

              {/* Controls */}
              <div className="ml-auto flex shrink-0 items-center gap-3">
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

                  {isEffortDropdownOpen && effortDropdownRect && createPortal(
                      <div
                        ref={effortDropdownMenuRef}
                        style={{
                          position: 'fixed',
                          top: effortDropdownRect.top + 8,
                          left: Math.max(8, Math.min(effortDropdownRect.left + effortDropdownRect.triggerWidth - 288, window.innerWidth - 296)),
                          zIndex: 10000,
                        }}
                        className="w-72 rounded-2xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-2xl overflow-hidden py-1.5 animate-in fade-in zoom-in-95 duration-200 origin-top-right backdrop-blur-xl"
                      >
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
                      </div>,
                      document.body,
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

                  {isAgentModeDropdownOpen && agentModes.length > 0 && agentModeDropdownRect && createPortal(
                    <div
                      ref={agentModeDropdownMenuRef}
                      style={{
                        position: 'fixed',
                        top: agentModeDropdownRect.top + 8,
                        left: Math.max(8, Math.min(agentModeDropdownRect.left + agentModeDropdownRect.triggerWidth - 288, window.innerWidth - 296)),
                        zIndex: 10000,
                      }}
                      className="w-72 rounded-2xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-2xl overflow-hidden py-1.5 animate-in fade-in zoom-in-95 duration-200 origin-top-right backdrop-blur-xl"
                    >
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
                    </div>,
                    document.body,
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

                <button
                    onClick={resetSession}
                    title="Start fresh (memories preserved)"
                    className="inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm"
                >
                  <RefreshCw size={14} className="text-sky-500/80" />
                  New Chat
                </button>

                <button
                    onClick={compactContext}
                    disabled={isCompacting}
                    className="inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 shadow-sm"
                >
                  <Wand2 size={14} className={`${isCompacting ? 'animate-spin' : ''} text-amber-500/80`} />
                  Compact
                </button>

              </div>
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
            {/* Session toolbar items moved into the pane-header actions */}

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
                            // Focus the terminal in the shared runtime state; the
                            // standalone Terminal tab follows it.
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
                            // Focus the terminal in the shared runtime state; the
                            // standalone Terminal tab follows it.
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
                            // Focus the terminal in the shared runtime state; the
                            // standalone Terminal tab follows it.
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
                                const isFocused = focusedTerminalId === terminal.id;
                                // Label is derived from terminal_id: '0' → "main",
                                // 'auto-xxx' / 'bg-…' → first command summary, anything
                                // else → the agent's chosen name verbatim. Stable
                                // across backend restarts.
                                const display = getTerminalLabel(terminal.id, terminal.lastCommand ?? terminal.label);
                                const tooltip = terminal.lastCommand
                                  ? `${display} — last: ${summarizeCommand(terminal.lastCommand)}`
                                  : display;
                                const closable = true;
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
                                        // Focus this terminal in the shared runtime
                                        // state; the standalone Terminal tab follows.
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

          {workbenchVisible ? (
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
                onCloseAll={closeAllWorkbenchTabs}
                showExplorer={false}
                explorerEntries={runtimeFiles?.entries || []}
                currentExplorerPath={runtimePath}
                  explorerLoading={runtimeFilesLoading}
                  onExplorerFileClick={(entry) => void openRuntimeFile(entry.path)}
                  onExplorerDownload={(entry) => void downloadRuntimeEntry(entry)}
                  loadExplorerDirectory={loadRuntimeDirectoryEntries}
                  explorerRefreshKey={runtimeFilesRefreshKey}
                onExplorerDirectoryToggle={(entry, expanded) => {
                  if (!activeSessionId || !entry.is_git_root) return;
                  if (!expanded) {
                    forgetRepoRoot(entry.path);
                    return;
                  }
                  void fetchChangedFilesForRepo(entry.path);
                }}
                repoChangesSections={runtimeRepoChangeSections}
                expandedGitDirs={runtimeExpandedGitDirs}
                onToggleGitDir={toggleGitDir}
                onGitFileClick={(path) => void openRuntimeFileDiff(path)}
                diffMode={activeWorkbenchTab ? workbenchShowDiffByPath[activeWorkbenchTab.path] ?? false : false}
                setDiffMode={(enabled) => {
                  if (!activeWorkbenchTab) return;
                  setShowDiffForPath(activeWorkbenchTab.path, enabled);
                }}
                diffContent={activeWorkbenchDiff}
                diffLoading={activeWorkbenchDiffLoading}
                diffError={activeWorkbenchDiffError}
                diffBaseRef={activeWorkbenchBaseRef}
                onDiffBaseRefChange={(ref) => {
                  if (!activeWorkbenchTab) return;
                  setDiffBaseRefForPath(activeWorkbenchTab.path, ref);
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

        {sessionDeleteConfirmDialog}

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
