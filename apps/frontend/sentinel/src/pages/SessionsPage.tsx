import { RetainedSessionContext } from '../lib/workspace-context-values';
import { SessionPermissions } from '../components/session/SessionPermissions';
import { SESSION_PERMISSIONS_CHANGED, type ApprovalScope } from '../lib/approvals';
import { ConversationNavigator } from '../components/session/ConversationNavigator';
import { ChatTurnTimeline } from '../components/session/ChatTurnTimeline';
import { buildChatTimeline, presentationOf, messageTime } from './chatTimeline';
import '../components/session/run-settings.css';
import '../components/session/subagent-float.css';
import { useAnimatedTextareaHeight } from '../hooks/useAnimatedTextareaHeight';
import '../components/session/chat-composer.css';
import '../components/session/chat-header.css';
import '../components/session/session-activity.css';
import { ComposerTerminalPills } from '../components/session/ComposerTerminalPills';
import { StreamToolCard } from '../components/session/StreamToolCard';
import { WorkspaceRuntimeStats, WorkspaceMetricsProvider } from '../components/session/WorkspaceRuntimeStats';
import { ModelSwitchDialog } from '../components/session/ModelSwitchDialog';
import { SessionModelControls, type SessionModelChoice } from '../components/session/SessionModelControls';
import { isSessionRunActive } from '../lib/session-stream';
import { WorkspaceAttachment } from '../components/session/WorkspaceAttachment';
import { EmptySessionLogo } from '../components/session/EmptySessionLogo';
import { AgentForm, pendingForm } from '../components/session/AgentForm';
import {
  ArrowDown,
  Settings,
  Bot,
  ChevronDown,
  Loader2,
  Plus,
  ArrowUp,
  Users,
  Square,
  Minimize2,
  Wrench,
  X,
  Terminal,
  ExternalLink,
  Zap,
  Activity,
  Gauge,
  Brain,
  Sparkles,
  Paperclip,
  Check,
  Pencil,
  GitBranch,
} from 'lucide-react';
import { ChangeEvent, ClipboardEvent, FormEvent, useEffect, useMemo, useRef, useState, memo, useCallback, useContext, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useLocation } from 'react-router-dom';
import { notificationPublisher } from '../lib/notifications';

import { useFocusModeStore } from '../store/focus-mode-store';
import { AppShell } from '../components/AppShell';
import { SessionMessageCard, buildToolArgumentsByCallId } from '../components/session/SessionMessageCard';

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
import { api, ApiError } from '../lib/api';
import { useInstanceName, usePaneId } from '../lib/workspace-context';
import { useAnchorRect } from '../lib/portal-menu';
import { useActiveSessionId, useActiveSessionStore, useSetActiveSession } from '../store/active-session-store';
import { useSessionRuntimeStream } from '../hooks/useSessionRuntimeStream';
import { useWorkspaceStore } from '../store/workspace-store';
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

const notify = notificationPublisher('Chat');

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
  return [...items].sort((a, b) => messageTime(a.created_at) - messageTime(b.created_at));
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

// Top-level right-rail tabs. The Desktop / Terminals / Files runtime surfaces
// moved out of SessionsPage into standalone workspace tabs (DesktopTab /
// TerminalTab / FilesTab), so the rail keeps only the chat-adjacent panels:
// sub-agent tasks, session history, and the debug panel.
type RightRailTab = 'sub_agents' | 'debug';

// ActivePane lives in useSessionRuntimeStream; the diff base-ref option
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

// --- Main Page Component ---

// Share initialization across split panes and React's repeated mount effects.
const initialSessionLoads = new Map<string, Promise<SessionListResponse>>();

function loadInitialSessions(instanceName: string): Promise<SessionListResponse> {
  const pending = initialSessionLoads.get(instanceName);
  if (pending) return pending;
  const path = `/instances/${encodeURIComponent(instanceName)}/sessions`;
  const request = (async () => {
    const payload = await api.get<SessionListResponse>(`${path}?limit=100&offset=0&include_sub_agents=true`);
    if (payload.items.length > 0) return payload;
    const first = await api.post<Session>(path, {});
    return { ...payload, items: [first], total: 1 };
  })().finally(() => initialSessionLoads.delete(instanceName));
  initialSessionLoads.set(instanceName, request);
  return request;
}

export function SessionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const instanceName = useInstanceName();
  const activeInstanceName = instanceName ?? null;
  const activeInstanceRef = useRef(activeInstanceName);
  activeInstanceRef.current = activeInstanceName;
  const [sessions, setSessions] = useState<Session[]>([]);
  const viewVisible = useContext(RetainedSessionContext)?.visible ?? true;
  const viewVisibleRef = useRef(viewVisible);
  viewVisibleRef.current = viewVisible;
  const activeSessionId = useActiveSessionId();
  const setActiveSessionId = useSetActiveSession();
  const [creatingChat, setCreatingChat] = useState(false);
  const creatingChatRef = useRef(false);

  const [pendingFirstMessage, setPendingFirstMessage] = useState<{ instance: string; session: string; payload: unknown } | null>(null);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [agentModes, setAgentModes] = useState<AgentModeOption[]>([]);
  const [selectedAgentMode, setSelectedAgentMode] = useState<string | null>(() => {
    const raw = localStorage.getItem(AGENT_MODE_STORAGE_KEY);
    return raw && raw.trim() ? raw.trim() : null;
  });
  const [selectedTier, setSelectedTier] = useState(
    () => parseTier(localStorage.getItem('sentinel-selected-tier')) ?? 'normal',
  );
  const [sessionModelChoices, setSessionModelChoices] = useState<Record<string, Partial<Record<ModelOption['tier'], SessionModelChoice>>>>(() => {
    try { return JSON.parse(localStorage.getItem('sentinel-session-model-choices') ?? '{}'); } catch { return {}; }
  });
  const modelChoiceKey = `${activeInstanceName ?? ''}:${activeSessionId ?? 'new'}`;
  const selectedChoice = sessionModelChoices[modelChoiceKey]?.[selectedTier] ?? {};
  const [modelPreview, setModelPreview] = useState<{ key: string; tier: ModelOption['tier']; choice: SessionModelChoice } | null>(null);
  const modelCheckSequence = useRef(0);
  const visiblePreview = modelPreview?.key === modelChoiceKey ? modelPreview : null;
  const displayedTier = visiblePreview?.tier ?? selectedTier;
  const displayedChoice = visiblePreview?.choice ?? selectedChoice;
  const activeModelOption = models.find(model => model.tier === selectedTier);
  const selectedProviderOption = activeModelOption?.provider_options?.find(option => option.provider_id === selectedChoice.provider_id) ?? activeModelOption?.provider_options?.[0];
  useEffect(() => { localStorage.setItem('sentinel-session-model-choices', JSON.stringify(sessionModelChoices)); }, [sessionModelChoices]);
  const [isAgentModeDropdownOpen, setIsAgentModeDropdownOpen] = useState(false);
  const [isMaxDropdownOpen, setIsMaxDropdownOpen] = useState(false);
  const [isEffortDropdownOpen, setIsEffortDropdownOpen] = useState(false);
  const [runSettingsOpen, setRunSettingsOpen] = useState(false);
  const runSettingsTrigger = useRef<HTMLButtonElement>(null);
  const runSettingsControlRef = useRef<HTMLDivElement>(null);
  const runSettingsMenu = useRef<HTMLDivElement>(null);
  const runSettingsRect = useAnchorRect(runSettingsTrigger, runSettingsOpen);

  const [maxIterations, setMaxIterations] = useState(0);
  const [sendingSteering, setSendingSteering] = useState(false);
  const sendingSteeringRef = useRef(false);
  const steeringSubmissionRef = useRef<{ key: string; id: string } | null>(null);
  const deliveredSteeringRef = useRef(new Set<string>());

  const [messages, setMessages] = useState<Message[]>([]);
  const contextTokenBudget = selectedProviderOption?.context_token_budget ?? activeModelOption?.context_token_budget ?? null;
  const [modelSwitch, setModelSwitch] = useState<{ tier: ModelOption['tier']; choice: SessionModelChoice; model: string; input_tokens: number | null; context_token_budget: number | null; count_source: string; requires_compaction?: boolean | null } | null>(null);
  const [lastRequestUsage, setLastRequestUsage] = useState<SessionContextUsage['last_request_usage']>(null);
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
  const [rightRailTab, setRightRailTab] = useState<RightRailTab>('sub_agents');
  // Pills above the chat composer surface every tmux-backed terminal the
  // agent (or the user) has opened in the current chat session. State is
  // driven by `panes_changed` WS events plus the initial
  // `connected` payload that lists already-live terminals on page load.
  //
  // Terminal pills and chat events share one session WebSocket. Graphical
  // desktop services are managed separately by the workspace Desktop tab.

  // Per-session runtime stream. Owns the shared, ref-counted WS connection; the
  // chat stream taps the same socket via `onEvent`. The standalone runtime tabs
  // subscribe to the same (instance, session) pair, so opening them reuses this
  // socket. Graphical desktop lifecycle is independent of this session stream.
  const runtime = useSessionRuntimeStream(activeInstanceName, activeSessionId, {
    onReconnectFailed: () => {
      notify.error('Realtime stream disconnected');
    },
    onEvent: (event) => {
      onStreamEvent(activeSessionIdRef.current ?? '', event);
    },
  });
  const {
    connection: runtimeConnection,
    runActive,
    isStreamOpen,
    sendMessage: sendStreamMessage,
    activePanes,
    focusedPaneId,
    setFocusedPaneId,
    dropPane,
    runtimeBooting,
  } = runtime;

  useEffect(() => {
    if (!pendingFirstMessage) return;
    if (activeInstanceName !== pendingFirstMessage.instance || activeSessionId !== pendingFirstMessage.session) {
      setPendingFirstMessage(null);
      setCreatingChat(false);
      return;
    }
    if (runtimeConnection !== 'connected' || !isStreamOpen()) return;
    if (sendStreamMessage(pendingFirstMessage.payload)) {
      setPendingFirstMessage(null);
      setCreatingChat(false);
      setComposer('');
      setComposerAttachments([]);
    }
  }, [pendingFirstMessage, activeInstanceName, activeSessionId, runtimeConnection, isStreamOpen, sendStreamMessage]);
  useEffect(() => {
    if (!pendingFirstMessage) return;
    const timer = setTimeout(() => {
      setPendingFirstMessage(null);
      setCreatingChat(false);
      notify.error('Could not connect. Your message is still here—try sending again.');
    }, 15000);
    return () => clearTimeout(timer);
  }, [pendingFirstMessage]);

  // Surface a terminal when its pill (or a tool card's "open terminal") is
  // clicked: select its runtime pane, then reveal the Terminal view in a usable
  // part of the current session's layout without replacing the chat.
  const paneId = usePaneId();
  const openTerminalPane = useWorkspaceStore((s) => s.openTerminalPane);
  const openOrFocusPane = useCallback(
    (targetPaneId: string) => {
      setFocusedPaneId(targetPaneId);
      openTerminalPane(paneId);
    },
    [paneId, openTerminalPane, setFocusedPaneId],
  );

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
  const focusPaneId = useFocusModeStore(state => state.paneId);
  const mode = focusPaneId === paneId ? 'solo' : 'advanced';

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
    localStorage.setItem('sentinel-selected-tier', selectedTier);
  }, [selectedTier]);

  useEffect(() => {
    if (!selectedAgentMode) return;
    localStorage.setItem(AGENT_MODE_STORAGE_KEY, selectedAgentMode);
  }, [selectedAgentMode]);

  useEffect(() => {
    if (!runSettingsOpen && !isEffortDropdownOpen && !isAgentModeDropdownOpen && !isMaxDropdownOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (target instanceof Element && target.closest('.model-switch-dialog')) return;
      if (runSettingsTrigger.current?.contains(target) || runSettingsMenu.current?.contains(target)) return;
      if (effortDropdownRef.current?.contains(target)) return;
      if (effortDropdownMenuRef.current?.contains(target)) return;
      if (agentModeDropdownRef.current?.contains(target)) return;
      if (agentModeDropdownMenuRef.current?.contains(target)) return;
      if (maxDropdownRef.current?.contains(target)) return;
      if (maxDropdownMenuRef.current?.contains(target)) return;
      setRunSettingsOpen(false);
      setIsEffortDropdownOpen(false);
      setIsAgentModeDropdownOpen(false);
      setIsMaxDropdownOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setRunSettingsOpen(false);
      setIsEffortDropdownOpen(false);
      setIsAgentModeDropdownOpen(false);
      setIsMaxDropdownOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [runSettingsOpen, isEffortDropdownOpen, isAgentModeDropdownOpen, isMaxDropdownOpen]);

  const [isCompacting, setIsCompacting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [railOverlayOpen, setRailOverlayOpen] = useState(false);
  const subagentFloatRef = useRef<HTMLDivElement>(null);
  const subagentTriggerRef = useRef<HTMLButtonElement>(null);
  const subagentPanelRef = useRef<HTMLDivElement>(null);
  const subagentRect = useAnchorRect(subagentTriggerRef, railOverlayOpen);
  const activeTaskCount = tasks.filter(task => task.status === 'running' || task.status === 'pending').length;
  const failedTaskCount = tasks.filter(task => task.status === 'failed').length;
  const subagentStatus = activeTaskCount ? `${activeTaskCount} active` : failedTaskCount ? `${failedTaskCount} failed` : tasks.length ? `${tasks.length} finished` : 'Idle';
  useEffect(() => { setRailOverlayOpen(false); }, [activeSessionId]);
  useEffect(() => {
    if (!railOverlayOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!subagentFloatRef.current?.contains(event.target as Node) && !subagentPanelRef.current?.contains(event.target as Node)) setRailOverlayOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setRailOverlayOpen(false); subagentTriggerRef.current?.focus(); }
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape); };
  }, [railOverlayOpen]);
  const [statusTooltip, setStatusTooltip] = useState<'connection' | 'progress' | null>(null);

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
  const effortDropdownRef = useRef<HTMLDivElement | null>(null);
  const effortDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const maxDropdownRef = useRef<HTMLDivElement | null>(null);
  const maxDropdownMenuRef = useRef<HTMLDivElement | null>(null);
  const connectionPillRef = useRef<HTMLButtonElement | null>(null);
  const progressPillRef = useRef<HTMLDivElement | null>(null);
  const activeSessionIdRef = useRef<string | null>(activeSessionId);
  const contextUsageRequestRef = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  useAnimatedTextareaHeight(composerRef, composer);
  const composerFocusRequest = useActiveSessionStore(state => state.composerFocusRequest);
  useEffect(() => {
    if (!composerFocusRequest || composerFocusRequest.instanceName !== activeInstanceName || composerFocusRequest.sessionId !== activeSessionId) return;
    const frame = requestAnimationFrame(() => {
      const input = composerRef.current;
      if (!input || input.disabled) return;
      input.focus({ preventScroll: true });
      if (document.activeElement === input && useActiveSessionStore.getState().composerFocusRequest === composerFocusRequest) {
        useActiveSessionStore.setState({ composerFocusRequest: null });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [composerFocusRequest, activeInstanceName, activeSessionId, creatingChat, isCompacting, streaming.isCompactingContext]);
  const connectionPanelRef = useRef<HTMLDivElement>(null);
  const [connectionPinned, setConnectionPinned] = useState(false);
  const connectionHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function showConnectionDetails() {
    if (connectionHideTimer.current) clearTimeout(connectionHideTimer.current);
    setStatusTooltip('connection');
  }
  function hideConnectionDetails() {
    if (connectionPinned) return;
    connectionHideTimer.current = setTimeout(() => setStatusTooltip(current => current === 'connection' ? null : current), 150);
  }
  useEffect(() => {
    if (statusTooltip !== 'connection') return;
    const close = () => { setConnectionPinned(false); setStatusTooltip(null); };
    const pointer = (event: PointerEvent) => {
      if (!connectionPillRef.current?.contains(event.target as Node) && !connectionPanelRef.current?.contains(event.target as Node)) close();
    };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    document.addEventListener('pointerdown', pointer); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', pointer); document.removeEventListener('keydown', key); };
  }, [statusTooltip]);
  useEffect(() => () => { if (connectionHideTimer.current) clearTimeout(connectionHideTimer.current); }, []);
  const connectionTooltipRect = useAnchorRect(connectionPillRef, statusTooltip === 'connection');
  const progressTooltipRect = useAnchorRect(progressPillRef, statusTooltip === 'progress');

  // Keep refs in sync so WS callbacks can read current values
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => {
    setRetryCandidate(null);
    setRetryingMessageId(null);
  }, [activeSessionId]);

  const activeForm = useMemo(() => pendingForm(messages), [messages]);
  const stopVisible = runActive || streaming.agentIteration > 0 || streaming.isThinking ||
    streaming.isStreaming || streaming.activeToolCalls.length > 0;
  const streamBusy =
    runActive ||
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

  const chatTimeline = useMemo(() => buildChatTimeline(messages, streaming), [messages, streaming]);

  const activeSession = useMemo(
      () => sessions.find((session) => session.id === activeSessionId) ?? null,
      [sessions, activeSessionId],
  );
  const rightRailTabs = useMemo<Array<{ id: RightRailTab; label: string }>>(
    () => {
      // The rail belongs to the current session’s sub-agents.
      const tabs: Array<{ id: RightRailTab; label: string }> = [
        { id: 'sub_agents', label: 'Sub-agents' },
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
    if (!el || !viewVisibleRef.current || el.clientHeight === 0) return;
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
    if (!el || !viewVisibleRef.current || el.clientHeight === 0) return;

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

  const markSessionRead = useCallback((sessionId: string) => {
    setSessions((current) =>
      current.map((s) => s.id === sessionId ? { ...s, has_unread: false } : s),
    );
    api.post(`/sessions/${sessionId}/read`, {}).catch(() => {/* best-effort */});
  }, []);

  const startWorkbenchResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsWorkbenchResizing(true);
  }, []);

  const stopResizing = useCallback(() => {
    setIsWorkbenchResizing(false);
  }, []);

  const resizeWorkbench = useCallback((e: MouseEvent) => {
    if (!isWorkbenchResizing) return;
    const maxWidth = Math.max(420, window.innerWidth - 360);
    const newWidth = e.clientX;
    const clamped = Math.max(360, Math.min(maxWidth, newWidth));
    setWorkbenchWidth(clamped);
  }, [isWorkbenchResizing]);

  useEffect(() => {
    if (isWorkbenchResizing) {
      window.addEventListener('mousemove', resizeWorkbench);
      window.addEventListener('mouseup', stopResizing);
    }
    return () => {
      window.removeEventListener('mousemove', resizeWorkbench);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isWorkbenchResizing, resizeWorkbench, stopResizing]);

  useEffect(() => {
    void fetchSessions({ autoSelectIfEmpty: true });
    void fetchModels();
    void fetchAgentModes();
    // Live-view + runtime-status fetching (incl. the session-change re-fetch, the
    // booting poll, and the desktop-visibility status refresh) is owned by
    // `useSessionRuntimeStream`.
  }, [activeInstanceName]);

  // Poll sessions every 30s to pick up unread changes
  useEffect(() => {
    if (!viewVisible) return;
    const interval = setInterval(() => { void fetchSessions({ autoSelectIfEmpty: false }); }, 30_000);
    return () => clearInterval(interval);
  }, [activeInstanceName, viewVisible]);

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
    // Machine/workbench state (files, repo changes, workbench tabs, diffs) is
    // reset by `useSessionWorkbench` on session change; terminals + live view by
    // `useSessionRuntimeStream`; the WS connection by the shared stream manager.
    // This effect now only owns chat state (messages / context / tasks /
    // streaming) and re-fetches the initial runtime file tree.
    if (!activeSessionId) {
      setMessages([]);
      setLastRequestUsage(null);
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
    setLastRequestUsage(null);
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
  }, [activeSessionId]);

  // The workbench waits for a confirmed workspace attachment before requesting files.
  useEffect(() => {
    void fetchRuntimeFiles('', { refreshGit: false });
  }, [activeSessionId, fetchRuntimeFiles]);

  useEffect(() => {
    if (!viewVisible || !activeSessionId || !hasActiveSubAgentTasks) return;
    const timer = window.setInterval(() => {
      void fetchTasks(activeSessionId);
    }, 1500);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, hasActiveSubAgentTasks, viewVisible]);

  useLayoutEffect(() => {
    if (!viewVisible) {
      cancelScheduledAutoScroll();
      return;
    }
    const el = scrollRef.current;
    if (el && !shouldAutoScrollRef.current) el.scrollTop = lastScrollTopRef.current;
  }, [viewVisible, cancelScheduledAutoScroll]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !viewVisible || el.clientHeight === 0) return;
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
  }, [viewVisible, messages, streaming.text, streaming.timeline.length, streaming.activeToolCalls.length, streaming.completedToolCalls.length, activeToolPayloadChars, completedToolPayloadChars, activePanes.length > 0, scheduleStickToBottom]);

  useEffect(() => {
    return () => {
      cancelScheduledAutoScroll();
    };
  }, [cancelScheduledAutoScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!viewVisible || !activeSessionId || messagesLoading || isLoadingOlderMessages) return;
    if (!hasMoreMessages || messages.length === 0) return;
    if (el.scrollHeight <= el.clientHeight + 12) {
      void loadOlderMessages();
    }
  }, [viewVisible, activeSessionId, messages, hasMoreMessages, messagesLoading, isLoadingOlderMessages]);

  // API Actions
  async function fetchSessions(options?: { autoSelectIfEmpty?: boolean }) {
    const autoSelectIfEmpty = options?.autoSelectIfEmpty ?? false;
    try {
      if (!activeInstanceName) return;
      const payload = autoSelectIfEmpty
        ? await loadInitialSessions(activeInstanceName)
        : await api.get<SessionListResponse>(`/instances/${encodeURIComponent(activeInstanceName)}/sessions?limit=100&offset=0&include_sub_agents=true`);
      if (activeInstanceName !== activeInstanceRef.current) return;
      const payloadItems = Array.isArray(payload?.items) ? payload.items : [];
      setSessions(payloadItems);
      // An explicit null means the user closed the last session. Only choose a
      // default for an instance that has never had a selection.
      if (autoSelectIfEmpty && !activeSessionIdRef.current && useActiveSessionStore.getState().byInstance[activeInstanceName] === undefined) {
        const firstRoot = payloadItems.find((item) => !item.parent_session_id) ?? payloadItems[0] ?? null;
        if (firstRoot) {
          setActiveSessionId(firstRoot.id);
          activeSessionIdRef.current = firstRoot.id;
        }
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to load sessions');
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

  useEffect(() => {
    modelCheckSequence.current += 1;
    setModelSwitch(null);
    setModelPreview(null);
  }, [modelChoiceKey]);

  async function switchModel(tier: ModelOption['tier'], approved = false, requestedChoice?: SessionModelChoice) {
    const savedChoice = sessionModelChoices[modelChoiceKey]?.[tier] ?? {};
    const choice = requestedChoice ?? { ...savedChoice, provider_id: displayedChoice.provider_id ?? savedChoice.provider_id };
    const requestId = ++modelCheckSequence.current;
    const apply = () => {
      setSelectedTier(tier);
      setSessionModelChoices(previous => ({ ...previous, [modelChoiceKey]: { ...previous[modelChoiceKey], [tier]: choice } }));
      setModelSwitch(null);
      setModelPreview(null);
    };
    const sessionId = activeSessionId;
    const target = models.find(model => model.tier === tier);
    const provider = target?.provider_options?.find(option => option.provider_id === choice.provider_id) ?? target?.provider_options?.[0];
    if (!provider?.supports_fast_mode) choice.fast_mode = false;
    const speedOnly = tier === selectedTier && provider?.provider_id === selectedProviderOption?.provider_id && choice.reasoning_level === selectedChoice.reasoning_level;
    if (speedOnly) { apply(); return; }
    // Codex handles a real overflow through native compaction during generation.
    // Its OAuth transport does not offer preflight counts; unknown is not overflow.
    const codexToCodex = selectedProviderOption?.provider_id === 'openai-codex' && provider?.provider_id === 'openai-codex';
    if (approved || !sessionId || codexToCodex) { apply(); return; }
    setModelPreview({ key: modelChoiceKey, tier, choice });
    setModelSwitch(null);
    try {
      const check = await api.post<{ model: string; input_tokens: number | null; context_token_budget: number | null; count_source: string; requires_compaction: boolean | null }>(`/sessions/${sessionId}/model-context`, {
        tier, ...choice, content: composer, attachments: composerAttachments, agent_mode: selectedAgentMode,
      });
      if (requestId !== modelCheckSequence.current || sessionId !== activeSessionIdRef.current) return;
      if (check.requires_compaction !== false || check.input_tokens === null || check.context_token_budget === null) {
        setModelSwitch({ tier, choice, ...check });
        return;
      }
      apply();
    } catch {
      if (requestId !== modelCheckSequence.current || sessionId !== activeSessionIdRef.current) return;
      setModelSwitch({ tier, choice, model: provider?.model ?? target?.primary_model_id ?? tier, input_tokens: null, context_token_budget: provider?.context_token_budget ?? null, count_source: 'unavailable' });
    }
  }

  async function fetchContextUsage(sessionId: string) {
    const requestId = ++contextUsageRequestRef.current;
    try {
      const payload = await api.get<SessionContextUsage>(`/sessions/${sessionId}/context-usage`);
      if (sessionId !== activeSessionIdRef.current || requestId !== contextUsageRequestRef.current) {
        return;
      }
      setLastRequestUsage(payload.last_request_usage ?? null);
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
          ? `/sessions/${sessionId}/messages?limit=50&view=chat&before=${encodeURIComponent(beforeMessageId)}`
          : `/sessions/${sessionId}/messages?limit=50&view=chat`;
      const payload = await api.get<MessageListResponse>(path);
      if (sessionId !== activeSessionIdRef.current) return;
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
          // History can arrive after a reconnect or a new turn has started.
          // It must not erase live events already received for that turn.
          if (isSessionRunActive(activeInstanceRef.current ?? '', sessionId)) return prev;
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
            text: '', textPresentation: undefined,
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
      notify.error(error instanceof Error ? error.message : 'Failed to load messages');
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
      if (sessionId !== activeSessionIdRef.current) return;
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
      notify.success('Sub-agent task terminated');
      setIsTaskModalOpen(false);
      void fetchTasks(activeSessionId);
    } catch {
      notify.error('Failed to terminate sub-agent task');
    } finally {
      setIsTerminatingTask(false);
    }
  }

  async function spawnSubAgent(name: string, scope: string) {
    if (!activeSessionId || isSpawning) return;

    setIsSpawning(true);
    try {
      await api.post(`/sessions/${activeSessionId}/sub-agents`, {
        name,
        scope,
        allowed_tools: [], // empty allowlist = full tool access for sub-agent
      });
      notify.success('Sub-agent node initialized');
      setIsSpawnModalOpen(false);
      void fetchTasks(activeSessionId);
    } catch {
      notify.error('Failed to spawn sub-agent');
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

  const resolveApprovalInline = useCallback(async (approval: ApprovalRef, decision: 'approve' | 'reject', scope: ApprovalScope = 'once') => {
    const targetKey = approvalKey(approval);
    setResolvingApprovalKey(targetKey);
    approvalDebugLog('ui.approval.resolve.request', {
      provider: approval.provider,
      approval_id: approval.approvalId,
      decision,
    });
    try {
      await api.post(`/approvals/${encodeURIComponent(approval.provider)}/${encodeURIComponent(approval.approvalId)}/${decision}`, {
        scope,
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
      if (scope === 'session') window.dispatchEvent(new Event(SESSION_PERMISSIONS_CHANGED));
      notify.success(scope === 'session' ? 'Action allowed for this session' : decision === 'approve' ? 'Approved once' : 'Denied');
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
      notify.error(message || 'Failed to resolve approval');
    } finally {
      setResolvingApprovalKey(null);
    }
  }, [updatePersistedMessageApproval, updateStreamingCallApproval]);

  // WebSocket connection + reconnect are owned by the shared, ref-counted stream
  // manager (`session-stream.ts`) and consumed via `useSessionRuntimeStream`.
  // `onStreamEvent` below is the chat-side handler, wired in as the hook's
  // `onEvent` tap so chat + runtime share ONE socket per session.

  function markLatestUserMessageRetryable(rawError: string, messageId?: string) {
    const latestUserMessage = messageId
      ? messagesRef.current.find(message => message.id === messageId && message.role === 'user')
      : [...messagesRef.current].reverse().find((message) => message.role === 'user');
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
      await api.post<{ status: string }>(`/sessions/${activeSessionId}/messages/${message.id}/retry`, {
        tier: selectedTier,
        provider_id: selectedProviderOption?.provider_id ?? null,
        reasoning_level: selectedChoice.reasoning_level ?? null,
        fast_mode: !!selectedChoice.fast_mode && !!selectedProviderOption?.supports_fast_mode,
        agent_mode: selectedAgentMode ?? null,
        max_iterations: maxIterations,
      });
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
    } catch (error) {
      const detail = error instanceof Error ? error.message : fallbackError;
      setRetryCandidate({
        messageId: message.id,
        error: detail || fallbackError,
      });
      notify.error(detail || fallbackError);
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
      case 'run_state':
        if (event.run_active === false) void loadMessages(sessionId);
        break;
      case 'connected':
        // Terminal population from `event.panes` is handled by
        // `useSessionRuntimeStream`; here we only own the chat-side payload
        // (context budget + message history).
        void fetchContextUsage(sessionId);
        if (event.history_via_http === true) {
          void loadMessages(sessionId);
          break;
        }
        setMessages((current) => {
          const incoming = sortMessages((event.history as Message[]) ?? []);
          const merged = new Map<string, Message>();
          [...current, ...incoming].forEach((item) => merged.set(item.id, item));
          const next = sortMessages([...merged.values()]);
          oldestServerMessageIdRef.current = next.length > 0 ? next[0].id : null;
          return next;
        });
        break;
      // panes_changed and runtime_ready are
      // handled by useSessionRuntimeStream. SessionsPage does not opt into the
      // hook, so no pane auto-focus here.
      case 'notice_message': {
        const notice = event.message as Message | undefined;
        if (!notice || notice.session_id !== sessionId || !notice.id || !notice.metadata?.notice) break;
        setMessages(current => sortMessages([...current.filter(message => message.id !== notice.id), notice]));
        break;
      }
      case 'steering_delivered': {
        const id = event.delta as string;
        if (!id) break;
        deliveredSteeringRef.current.add(id);
        setMessages(current => current.map(message => message.id === id
          ? { ...message, metadata: { ...message.metadata, steering: 'delivered' } }
          : message));
        break;
      }
      case 'message_ack':
        setMessages((current) => {
          const messageId = (event.message_id as string | undefined)?.trim();
          if (!messageId) return current;
          if (current.some((item) => item.id === messageId)) return current;
          const createdAt = (event.created_at as string | undefined) || new Date().toISOString();
          const metadata = { ...(isObjectRecord(event.metadata) ? event.metadata : { source: 'web' }), ...(deliveredSteeringRef.current.has(messageId) ? { steering: 'delivered' } : {}) };
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
          text: '', textPresentation: undefined,
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
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: true, textPresentation: current.textPresentation ?? presentationOf(event.presentation) ?? { id: crypto.randomUUID(), created_at: new Date().toISOString() }, text: current.text + (event.delta ?? '') }));
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
                    ? { ...item, kind: 'tool', key: `tool-${newKey}`, callKey: newKey }
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
                : [...current.timeline, { kind: 'tool', key: `tool-${callKey}`, callKey, presentation: presentationOf(event.presentation) ?? { id: crypto.randomUUID(), created_at: new Date().toISOString() } }],
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
            keepsWaitingState, presentation: presentationOf(event.presentation),
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
        const compacted = event.compacted === true;
        setStreaming((current) => ({ ...current, isCompactingContext: false }));
        if (compacted) {
          notify.success('Context compacted');
          void loadMessages(sessionId);
        }
        void fetchContextUsage(sessionId);
        break;
      }
      case 'compaction_failed':
        setStreaming((current) => ({ ...current, isCompactingContext: false }));
        notify.error((event.error as string) || 'Auto-compaction failed');
        break;
      // useSessionRuntimeStream clears the booting flag on runtime_ready.
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
              text: '', textPresentation: undefined,
              interimTextSeq: nextSeq,
              timeline: [...current.timeline, { kind: 'text', key: `interim-${nextSeq}`, text: chunk, presentation: current.textPresentation }],
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
        markLatestUserMessageRetryable(raw, typeof event.message_id === 'string' ? event.message_id : undefined);
        notify.error(humanizeAgentError(raw), { duration: 8000 });
        // Reset streaming state so UI doesn't stay stuck in "thinking" mode
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, isCompactingContext: false }));
        break;
      }
      case 'agent_error': {
        const raw = (event.error as string) || (event.message as string) || 'Agent failed';
        markLatestUserMessageRetryable(raw, typeof event.message_id === 'string' ? event.message_id : undefined);
        notify.error(humanizeAgentError(raw), { duration: 8000 });
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, isCompactingContext: false }));
        break;
      }
    }
  }

  async function appendImageFiles(files: File[]) {
    if (!files.length) return;
    const slotsLeft = MAX_IMAGE_ATTACHMENTS - composerAttachments.length;
    if (slotsLeft <= 0) {
      notify.error(`Maximum ${MAX_IMAGE_ATTACHMENTS} images per message`);
      return;
    }

    const nextFiles = files.slice(0, slotsLeft);
    if (files.length > slotsLeft) {
      notify.error(`Only ${slotsLeft} more image${slotsLeft === 1 ? '' : 's'} can be added`);
    }

    const parsed: MessageAttachment[] = [];
    for (const file of nextFiles) {
      const mimeType = file.type.toLowerCase();
      if (!ALLOWED_IMAGE_MIME_TYPES.has(mimeType)) {
        notify.error(`Unsupported file type: ${file.name}`);
        continue;
      }
      if (file.size > MAX_IMAGE_ATTACHMENT_BYTES) {
        notify.error(`Image too large (max 5MB): ${file.name}`);
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
        notify.error(`Failed to read image: ${file.name}`);
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
    if ((!content && composerAttachments.length === 0) || sendingSteeringRef.current || creatingChat || creatingChatRef.current || !activeInstanceName || isCompacting || streaming.isCompactingContext) return;
    const steeringKey = JSON.stringify([activeInstanceName, activeSessionId, content, composerAttachments]);
    if (activeSessionId && (streamBusy || steeringSubmissionRef.current?.key === steeringKey)) {
      sendingSteeringRef.current = true;
      setSendingSteering(true);
      const sessionId = activeSessionId;
      const instance = activeInstanceName;
      const attachments = composerAttachments;
      const key = steeringKey;
      if (steeringSubmissionRef.current?.key !== key) {
        steeringSubmissionRef.current = { key, id: crypto.randomUUID() };
      }
      try {
        const message = await api.post<Message>(`/sessions/${sessionId}/steer`, {
          message_id: steeringSubmissionRef.current.id, content, attachments,
          tier: selectedTier, ...selectedChoice, max_iterations: maxIterations,
          agent_mode: selectedAgentMode ?? undefined,
        });
        if (activeSessionIdRef.current === sessionId && activeInstanceRef.current === instance) {
          setMessages(current => current.some(item => item.id === message.id) ? current : sortMessages([...current, {
            ...message, metadata: { ...message.metadata, ...(deliveredSteeringRef.current.has(message.id) ? { steering: 'delivered' } : {}) },
          }]));
          setComposer(current => current.trim() === content ? '' : current);
          setComposerAttachments(current => current === attachments ? [] : current);
          shouldAutoScrollRef.current = true;
          setIsPinnedToBottom(true);
        }
        steeringSubmissionRef.current = null;
      } catch (error) {
        notify.error(error instanceof Error ? error.message : 'Could not send steering message');
      } finally {
        sendingSteeringRef.current = false;
        setSendingSteering(false);
      }
      return;
    }
    if (!activeSessionId) {
      creatingChatRef.current = true;
      setCreatingChat(true);
      const targetInstance = activeInstanceName;
      const payload = { type: 'message', content, attachments: composerAttachments,
        tier: selectedTier, ...selectedChoice, max_iterations: maxIterations,
        agent_mode: selectedAgentMode ?? undefined };
      try {
        const fresh = await api.post<Session>(`/instances/${encodeURIComponent(targetInstance)}/sessions`, {});
        if (activeInstanceRef.current !== targetInstance) { setCreatingChat(false); return; }
        setSessions(current => [fresh, ...current.filter(item => item.id !== fresh.id)]);
        setActiveSessionId(fresh.id);
        setPendingFirstMessage({ instance: targetInstance, session: fresh.id, payload });
      } catch (error) {
        setCreatingChat(false);
        notify.error(error instanceof Error ? error.message : 'Failed to start chat');
      } finally { creatingChatRef.current = false; }
      return;
    }

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
        tier: selectedTier, ...selectedChoice,
        max_iterations: maxIterations,
        agent_mode: selectedAgentMode ?? undefined,
      });
      setComposer('');
      setComposerAttachments([]);
      shouldAutoScrollRef.current = true;
      setIsPinnedToBottom(true);
    } else {
      notify.error('Connection lost. Reconnecting...');
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
      notify.success('Response stopped');
    } catch {
      notify.error('Failed to stop');
    }
    finally { setIsStopping(false); }
  }

  // Optimistically drops the pill so the UI reacts immediately, then asks the
  // backend to close only that pane. The authoritative panes_changed WS
  // event will also fire and is idempotent against the optimistic update.
  async function closePane(paneId: string) {
    if (!activeSessionId) return;
    dropPane(paneId);
    try {
      await api.delete(
        `/sessions/${activeSessionId}/panes/${encodeURIComponent(paneId)}`,
      );
    } catch {
      notify.error(`Failed to close ${paneId}`);
    }
  }

  async function compactContext() {
    if (!activeSessionId) return;
    if (streamBusy) {
      notify.error('Cannot compact while agent is running');
      return;
    }
    setIsCompacting(true);
    try {
      const result = await api.post<{ compacted: boolean; summary_preview: string }>(
        `/sessions/${activeSessionId}/compact`, {}, { timeoutMs: 300_000 }
      );
      if (!result.compacted) {
        notify.success('Nothing to compact yet (context too small)');
      } else {
        notify.success('Context compacted');
        // Refresh the context after compaction; stored history is retained.
        setMessages([]);
        void loadMessages(activeSessionId);
      }
      void fetchContextUsage(activeSessionId);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 408 || error.status === 0)) {
        notify.error('Could not confirm compaction completed. It may still be running; refresh the conversation before retrying.');
      } else {
        notify.error(error instanceof Error ? error.message : 'Compaction failed');
      }
    }
    finally { setIsCompacting(false); }
  }

  const inputTokens = lastRequestUsage?.usage.input_tokens;
  const contextPercent = typeof inputTokens === 'number' && typeof contextTokenBudget === 'number' && contextTokenBudget > 0 ? Math.round(inputTokens / contextTokenBudget * 100) : null;
  const contextColor = contextPercent === null ? 'var(--text-muted)' : contextPercent < 50 ? '#10b981' : contextPercent < 80 ? '#f59e0b' : '#f43f5e';
  const counts = lastRequestUsage?.usage;
  const details = [
    ['Input', counts?.input_tokens], ['Cached input', counts?.input_tokens_details?.cached_tokens],
    ['Cache writes', counts?.input_tokens_details?.cache_write_tokens], ['Output (includes reasoning)', counts?.output_tokens],
    ['Reasoning', counts?.output_tokens_details?.reasoning_tokens],
  ] as const;

  const subagentControl = (activeSessionId && <div ref={subagentFloatRef} className="subagent-float" data-open={railOverlayOpen}>
              <button ref={subagentTriggerRef} type="button" className="chat-header-pill subagent-float-trigger" aria-expanded={railOverlayOpen} aria-label={`Sub-agents: ${subagentStatus}`} onClick={() => setRailOverlayOpen(value => !value)} data-status={activeTaskCount ? 'active' : failedTaskCount ? 'failed' : tasks.length ? 'finished' : 'idle'}>
                <Users size={14} /><span>Sub-agents</span><span className="subagent-float-status"><i />{subagentStatus}</span><ChevronDown size={13} />
              </button>
              {railOverlayOpen && subagentRect && createPortal(<div ref={subagentPanelRef} data-pane-menu className="subagent-float-expansion subagent-header-panel" style={{ position: 'fixed', top: subagentRect.top + 8, left: Math.max(8, Math.min(subagentRect.left + subagentRect.triggerWidth - 360, window.innerWidth - 368)), zIndex: 10020 }}>
                <div className="subagent-float-clip"><aside aria-label="Sub-agent details" className="subagent-float-panel">
            <div className="relative">
              <div className="flex h-12 items-center gap-2 px-3">
                {rightRailTabs.length === 1 ? <h2 className="flex-1 text-[10px] font-bold uppercase tracking-widest text-(--text-secondary)">Sub-agents</h2> : <div
                  className="relative grid flex-1 gap-0 rounded-full border border-(--border-subtle) p-0.5 bg-(--surface-2) overflow-hidden"
                  style={{ gridTemplateColumns: `repeat(${rightRailTabs.length}, minmax(0, 1fr))` }}
                >
                  {/* Sliding Indicator */}
                  <div
                    className="absolute top-0.5 bottom-0.5 rounded-full bg-(--surface-0) shadow-xs transition-all duration-300 ease-out"
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
                          ? 'text-(--text-primary)'
                          : 'text-(--text-muted) hover:text-(--text-secondary)'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>}
                <button type="button" aria-label="Close sub-agents" onClick={() => setRailOverlayOpen(false)} className="subagent-float-close"><X size={15} /></button>
              </div>
            </div>

            {rightRailTab === 'sub_agents' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex-1 overflow-y-auto p-3 space-y-2.5 custom-scrollbar">
                  {tasks.map(t => (
                    <div key={t.id} className="group p-3.5 rounded-xl border border-(--border-subtle) bg-(--surface-0) hover:border-(--border-strong) transition-all shadow-xs">
                      <div className="flex items-center justify-between gap-3 mb-2">
                        <span className="text-xs font-bold text-(--text-primary) truncate">{t.name}</span>
                        <div className="shrink-0 scale-90 origin-right">
                          <StatusChip label={t.status} tone={taskStatusTone(t.status)} />
                        </div>
                      </div>
                      <p className="text-[10px] text-(--text-secondary) line-clamp-2 leading-relaxed mb-3">
                        {t.scope || 'No scope defined.'}
                      </p>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => { setSelectedTask(t); setIsTaskModalOpen(true); }}
                          className="flex-1 inline-flex items-center justify-center h-7 rounded-full border border-(--border-subtle) bg-(--surface-1) text-[10px] font-bold uppercase tracking-wide text-(--text-secondary) hover:bg-(--surface-2) hover:text-(--text-primary) transition-all active:scale-95"
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
                    <div className="py-12 flex flex-col items-center justify-center text-(--text-muted) opacity-40 gap-3">
                      <div className="p-3 rounded-2xl bg-(--surface-2)">
                        <Terminal size={24} strokeWidth={1} />
                      </div>
                      <p className="text-[10px] font-medium uppercase tracking-widest">Idle</p>
                    </div>
                  )}
                </div>
                <div className="p-3 border-t border-(--border-subtle) bg-(--surface-1)/50 backdrop-blur">
                  <button
                    onClick={() => setIsSpawnModalOpen(true)}
                    className="w-full flex items-center justify-center gap-2 h-10 rounded-full bg-(--accent-solid) text-(--app-bg) text-[11px] font-bold uppercase tracking-widest hover:opacity-90 transition-all active:scale-[0.98] shadow-md shadow-black/5"
                  >
                    <Plus size={14} />
                    Spawn Sub-Agent
                  </button>
                </div>
              </div>
            ) : null}

            {SESSION_DEBUG_PANEL_ENABLED && rightRailTab === 'debug' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-(--border-subtle)">
                  <div className="flex items-center gap-2">
                    <Activity size={15} className="text-amber-400" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                      Session Debug
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDebugEvents([])}
                    className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted) hover:text-(--text-primary)"
                  >
                    Clear
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
                  <section>
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                      Render Gates
                    </div>
                    <pre className="overflow-auto rounded-xl border border-(--border-subtle) bg-(--surface-0) p-3 text-[11px] leading-relaxed text-(--text-secondary)">{JSON.stringify({
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
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                      Streaming State
                    </div>
                    <pre className="overflow-auto rounded-xl border border-(--border-subtle) bg-(--surface-0) p-3 text-[11px] leading-relaxed text-(--text-secondary)">{JSON.stringify(streaming, null, 2)}</pre>
                  </section>

                  <section>
                    <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                      Incoming WS Events
                    </div>
                    <div className="overflow-auto rounded-xl border border-(--border-subtle) bg-(--surface-0)">
                      {debugEvents.length === 0 ? (
                        <div className="px-4 py-3 text-[11px] text-(--text-muted)">No events captured yet.</div>
                      ) : (
                        <div className="divide-y divide-(--border-subtle)">
                          {debugEvents.map((entry) => (
                            <div key={entry.id} className="px-4 py-2.5 font-mono text-[11px] leading-relaxed">
                              <div className="flex items-center gap-2">
                                <span className="text-amber-400">{entry.type}</span>
                                <span className="text-(--text-muted)">{entry.at.split('T')[1]?.replace('Z', '') ?? entry.at}</span>
                              </div>
                              {entry.summary ? (
                                <div className="mt-1 whitespace-pre-wrap wrap-break-word text-(--text-secondary)">{entry.summary}</div>
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
                </aside></div>
              </div>, document.body)}
            </div>);

  return (
      <AppShell
          title="Chat"
          contentClassName="h-full p-0! overflow-hidden"
          actions={
            mode === 'advanced' ? (
              <div className="chat-header-actions flex w-full min-w-0 items-center gap-2">
                {/* Status */}
                <div className="flex min-w-0 shrink-0 items-center gap-2.5">
                  <WorkspaceMetricsProvider sessionId={activeSessionId} instanceName={instanceName ?? null}>
                  <button type="button"
                    ref={connectionPillRef}
                    aria-expanded={statusTooltip === 'connection'} aria-haspopup="dialog"
                    aria-label={`Connection, workspace and context usage: ${contextPercent === null ? 'unavailable' : `${contextPercent}%`}`}
                    onFocus={showConnectionDetails}
                    onBlur={hideConnectionDetails}
                    onClick={() => { setConnectionPinned(true); showConnectionDetails(); }}
                    onMouseEnter={showConnectionDetails}
                    onMouseLeave={hideConnectionDetails}
                    className="chat-header-pill chat-header-connection group relative inline-flex h-7 items-center gap-2 px-3 text-[10px] rounded-full bg-(--surface-1) border border-(--border-subtle) transition-all hover:bg-(--surface-2) cursor-default"
                  >
                    <div className={`h-1.5 w-1.5 rounded-full transition-all duration-500 ${!activeSessionId ? 'bg-(--text-muted)' : streaming.connection === 'connected' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]'}`} />
                    <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-(--text-secondary)">{!activeSessionId ? 'New chat' : streaming.connection === 'connected' ? 'Live' : 'Offline'}</span>
                    <span aria-hidden="true" className="text-(--text-muted)">·</span>
                    <span className={`text-[10px] font-mono font-bold ${contextPercent !== null && contextPercent > 100 ? 'animate-pulse' : ''}`} style={{color: contextColor}}>{contextPercent === null ? 'N/A' : `${contextPercent}%`}</span>
                  </button>
                  {statusTooltip === 'connection' && connectionTooltipRect && createPortal(
                    <div ref={connectionPanelRef} data-pane-menu role="dialog" aria-label="Connection and context details" onMouseEnter={showConnectionDetails} onMouseLeave={hideConnectionDetails}
                      style={{
                        position: 'fixed', pointerEvents: 'auto',
                        top: connectionTooltipRect.top + 8,
                        left: Math.max(8, Math.min(connectionTooltipRect.left, window.innerWidth - 328)),
                        zIndex: 10000,
                      }}
                      className="session-telemetry-panel animate-in fade-in slide-in-from-top-1 duration-150"
                    >
                      <div className="font-bold uppercase tracking-wider text-(--text-muted) mb-1">Connection</div>
                      <div className="flex items-center gap-2">
                        <div className={`h-1.5 w-1.5 rounded-full ${!activeSessionId ? 'bg-(--text-muted)' : streaming.connection === 'connected' ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                        <span className={!activeSessionId ? 'text-(--text-secondary)' : streaming.connection === 'connected' ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'}>{!activeSessionId ? 'Connects when you send your first message' : streaming.connection.toUpperCase()}</span>
                      </div>
                      <WorkspaceRuntimeStats />
                      <div className="mt-3 pt-3 border-t border-(--border-subtle)">
                          <div className="mb-3">
                            <div className="flex items-center justify-between mb-2">
                              <span className="font-bold uppercase tracking-widest text-(--text-muted)">Context usage</span>
                              <span className="font-mono tabular-nums" style={{ color: contextColor }}>{contextPercent === null ? 'N/A' : `${contextPercent}%`}</span>
                            </div>
                            <div role="progressbar" aria-label="Context usage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={contextPercent === null ? undefined : Math.max(0, Math.min(100, contextPercent))} aria-valuetext={contextPercent === null ? 'Unavailable' : `${contextPercent}% of context budget`} className="h-1.5 rounded-full overflow-hidden bg-(--surface-2)">
                              <div className="h-full rounded-full transition-[width,background-color] duration-300 motion-reduce:transition-none" style={{ width: `${Math.max(0, Math.min(100, contextPercent ?? 0))}%`, backgroundColor: contextColor }} />
                            </div>
                          </div>
                          <div className="font-bold uppercase tracking-widest text-(--text-muted) mb-2">Last request · Provider usage</div>
                          {lastRequestUsage ? <>
                            <div className="mb-2 text-(--text-secondary)">{lastRequestUsage.model}</div>
                            {details.map(([label, count]) => <div key={label} className="flex justify-between gap-8 mb-1">
                              <span className="text-(--text-secondary)">{label}</span>
                              <span className="text-(--text-primary)">{typeof count === 'number' ? count.toLocaleString() : 'N/A'}</span>
                            </div>)}
                            <div className="flex justify-between gap-8 mt-2">
                              <span className="text-(--text-secondary)">{lastRequestUsage.price_kind === 'api_equivalent' ? 'API-equivalent token cost' : 'Token cost · list price'}</span>
                              <span className="text-(--text-primary)">{lastRequestUsage.price ? Number(lastRequestUsage.price.usd).toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 9 }) : 'Unavailable'}</span>
                            </div>
                            {lastRequestUsage.price_kind === 'api_equivalent' && <div className="mt-1 text-(--text-muted)">Subscription charge is not reported.</div>}
                            <div className="mt-2 text-(--text-muted)">Input / context budget: {contextPercent === null ? 'N/A' : `${contextPercent}%`} · {contextTokenBudget?.toLocaleString() ?? '—'} tokens</div>
                            <div className="mt-1 text-(--text-muted)">Updates when the provider finishes a request.</div>
                          </> : <div className="text-(--text-muted)">Token usage: N/A. No provider usage reported yet.</div>}

                      </div>
                    </div>,
                    document.body,
                  )}

                  </WorkspaceMetricsProvider>
                <WorkspaceAttachment sessionId={activeSessionId} instanceName={instanceName ?? null} busy={streamBusy} />
                <button
                    onClick={compactContext}
                    disabled={isCompacting}
                    className="chat-header-pill inline-flex h-7 items-center gap-2 rounded-full border border-(--border-subtle) bg-(--surface-1) px-3 text-[10px] font-bold uppercase tracking-widest text-(--text-secondary) transition-all hover:bg-(--surface-2) hover:text-(--text-primary) hover:border-(--border-strong) active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 shadow-xs"
                >
                  <Minimize2 size={14} className={`${isCompacting ? 'animate-spin' : ''} text-amber-500/80`} />
                  Compact
                </button>

                  {streaming.agentMaxIterations > 0 && (
                    <div
                      ref={progressPillRef}
                      onMouseEnter={() => setStatusTooltip('progress')}
                      onMouseLeave={() => setStatusTooltip((current) => current === 'progress' ? null : current)}
                      className="chat-header-pill group relative flex items-center gap-3 px-3 py-1.5 rounded-full bg-(--surface-1) border border-(--border-subtle) transition-all hover:bg-(--surface-2) cursor-default"
                    >
                      <div className="w-16 h-1 rounded-full bg-(--surface-3) overflow-hidden">
                        <div
                          className="h-full rounded-full bg-(--accent-solid) transition-all duration-700 ease-out"
                          style={{ width: `${Math.min((streaming.agentIteration / streaming.agentMaxIterations) * 100, 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono font-bold text-(--text-primary)">
                        {streaming.agentIteration}<span className="text-(--text-muted) mx-0.5">/</span>{streaming.agentMaxIterations}
                      </span>
                    </div>
                  )}
                  {statusTooltip === 'progress' && progressTooltipRect && streaming.agentMaxIterations > 0 && createPortal(
                    <div data-pane-menu
                      style={{
                        position: 'fixed',
                        top: progressTooltipRect.top + 8,
                        left: Math.max(8, Math.min(progressTooltipRect.left, window.innerWidth - 300)),
                        zIndex: 10000,
                      }}
                      className="session-telemetry-panel animate-in fade-in slide-in-from-top-1 duration-150"
                    >
                        <div className="font-bold uppercase tracking-wider text-(--text-muted) mb-1">Execution Pipeline</div>
                        <div><span className="font-bold text-(--accent-solid)">{streaming.agentIteration}</span><span className="text-(--text-muted)"> of {streaming.agentMaxIterations} steps completed</span></div>
                        <div className="mt-1 h-1 w-full bg-(--surface-2) rounded-full overflow-hidden">
                          <div className="h-full bg-(--accent-solid)" style={{ width: `${(streaming.agentIteration / streaming.agentMaxIterations) * 100}%` }} />
                        </div>
                    </div>,
                    document.body,
                  )}


              </div>

              {/* Controls */}
              <div className="ml-auto flex shrink-0 items-center gap-3">
                <div className="run-settings-control" ref={runSettingsControlRef}>
                  <button ref={runSettingsTrigger} type="button" aria-label="Run settings" aria-expanded={runSettingsOpen} aria-haspopup="dialog"
                    onClick={() => { setRunSettingsOpen(value => !value); if (!runSettingsOpen) { setIsEffortDropdownOpen(true); setIsAgentModeDropdownOpen(false); setIsMaxDropdownOpen(false); } }}
                    className="chat-header-pill flex items-center gap-2 px-3 h-7 rounded-full border border-(--border-subtle) bg-(--surface-1) hover:bg-(--surface-2) hover:border-(--border-strong) transition-all shadow-xs">
                    <Settings size={11} className="text-(--sentinel-blue)" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-primary)">Run settings</span>
                    <ChevronDown size={11} className="opacity-40" />
                  </button>
                  {runSettingsOpen && runSettingsRect && createPortal(
                    <div ref={runSettingsMenu} data-pane-menu role="dialog" aria-label="Run settings" className="run-settings-menu"
                      style={{position:'fixed', top:runSettingsRect.top + 8, left:Math.max(8, Math.min(runSettingsRect.left + runSettingsRect.triggerWidth - 304, window.innerWidth - 312)), maxHeight:Math.max(100, window.innerHeight - runSettingsRect.top - 16), zIndex:10010}}>
                {/* Effort / Tier Selector */}
                <section ref={effortDropdownRef} className="run-settings-section">
                  {(() => {
                      const active = models.find(m => m.tier === displayedTier) || models[0];
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
                            setIsMaxDropdownOpen(false);
                            setIsEffortDropdownOpen(!isEffortDropdownOpen);
                          }}
                          className="run-settings-section-toggle" aria-expanded={isEffortDropdownOpen}
                        >
                          <span className="text-(--text-secondary)">{icons[tier] || <Activity size={11} />}</span>
                          <span className="run-settings-section-label">Model &amp; reasoning</span><span className="run-settings-section-value">{active?.label || 'Mode'}</span>
                          <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${isEffortDropdownOpen ? 'rotate-180' : ''}`} />
                        </button>
                      );
                    })()}

                  {isEffortDropdownOpen && (
                      <div data-pane-menu
                        ref={effortDropdownMenuRef}

                        className="run-settings-options"
                      >
                        <SessionModelControls models={models} tier={displayedTier} choice={displayedChoice} disabled={streamBusy} onSelect={(tier, choice) => void switchModel(tier, false, Object.keys(choice).length ? choice : undefined)}>
                        {models.map(m => {
                          const active = displayedTier === m.tier;
                          const providerOption = m.provider_options?.find(option => option.provider_id === displayedChoice.provider_id) ?? m.provider_options?.[0];
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
                          }[tier as string] || 'text-(--text-muted)';

                          return (
                            <button
                              key={m.tier}
                              disabled={streamBusy}
                              onClick={() => { void switchModel(m.tier); }}
                              className={`w-full flex items-start gap-3.5 px-4 py-3 transition-all text-left group ${
                                active
                                  ? 'bg-(--accent-solid) text-(--app-bg)'
                                  : 'hover:bg-(--surface-1)'
                              }`}
                            >
                              <div className={`mt-0.5 shrink-0 transition-transform group-hover:scale-110 duration-200 ${active ? 'text-(--app-bg) opacity-90' : tierColor}`}>
                                <TierIcon size={14} />
                              </div>
                              <div className="flex flex-col gap-0.5 min-w-0">
                                <div className={`text-[10px] font-bold uppercase tracking-widest ${active ? 'text-(--app-bg)' : 'text-(--text-primary)'}`}>
                                  {m.label}
                                </div>
                                <div className={`text-[9px] font-medium leading-tight ${active ? 'text-(--app-bg) opacity-70' : 'text-(--text-muted)'}`}>
                                  {m.description}
                                </div>
                                {m.primary_provider_id && (
                                  <div className="mt-2 flex items-center gap-1.5">
                                    <span className={`text-[8px] font-mono px-1 rounded uppercase tracking-wider ${active ? 'bg-(--app-bg)/10 text-(--app-bg) border border-(--app-bg)/20' : 'bg-(--surface-2) text-(--text-secondary) border border-(--border-subtle)'}`}>
                                      {providerOption?.provider_id ?? m.primary_provider_id}
                                    </span>
                                    <span className={`text-[8px] font-mono truncate tracking-tight ${active ? 'text-(--app-bg) opacity-80' : 'text-(--text-secondary)'}`}>
                                      {providerOption?.model ?? m.primary_model_id}
                                    </span>
                                  </div>
                                )}
                              </div>
                              {active && (
                                <div className="ml-auto w-1 h-6 rounded-full bg-(--app-bg)/20 my-auto shadow-xs" />
                              )}
                            </button>
                          );
                        })}
                        </SessionModelControls>
                      </div>
                  )}
                </section>

                {/* Agent Mode Selector */}
                <section ref={agentModeDropdownRef} className="run-settings-section">
                  {(() => {
                    const active = agentModes.find((item) => item.id === selectedAgentMode) ?? agentModes[0];
                    return (
                      <button
                        onClick={() => {
                          if (agentModes.length === 0) return;
                          setIsEffortDropdownOpen(false);
                          setIsMaxDropdownOpen(false);
                          setIsAgentModeDropdownOpen((prev) => !prev);
                        }}
                        disabled={agentModes.length === 0}
                        className="run-settings-section-toggle" aria-expanded={isAgentModeDropdownOpen}
                        title={active?.description ?? 'Agent mode'}
                      >
                        <span className="text-(--text-secondary)"><Bot size={11} /></span>
                        <span className="run-settings-section-label">Agent mode</span><span className="run-settings-section-value">
                          {active?.label ?? 'Agent Mode'}
                        </span>
                        <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${isAgentModeDropdownOpen ? 'rotate-180' : ''}`} />
                      </button>
                    );
                  })()}

                  {isAgentModeDropdownOpen && (
                    <div data-pane-menu
                      ref={agentModeDropdownMenuRef}

                      className="run-settings-options"
                    >
                      {agentModes.map((modeOption) => {
                        const active = selectedAgentMode === modeOption.id;
                        return (
                          <button
                            key={modeOption.id}
                            onClick={() => {
                              setSelectedAgentMode(modeOption.id);
                            }}
                            className={`w-full flex items-start gap-3.5 px-4 py-3 transition-all text-left group ${
                              active
                                ? 'bg-(--accent-solid) text-(--app-bg)'
                                : 'hover:bg-(--surface-1)'
                            }`}
                          >
                            <div className={`mt-0.5 shrink-0 transition-transform group-hover:scale-110 duration-200 ${active ? 'text-(--app-bg) opacity-90' : 'text-(--text-muted)'}`}>
                              <Bot size={14} />
                            </div>
                            <div className="flex flex-col gap-0.5 min-w-0">
                              <div className={`text-[10px] font-bold uppercase tracking-widest ${active ? 'text-(--app-bg)' : 'text-(--text-primary)'}`}>
                                {modeOption.label}
                              </div>
                              <div className={`text-[9px] font-medium leading-tight ${active ? 'text-(--app-bg) opacity-70' : 'text-(--text-muted)'}`}>
                                {modeOption.description}
                              </div>
                            </div>
                            {active && (
                              <div className="ml-auto w-1 h-6 rounded-full bg-(--app-bg)/20 my-auto shadow-xs" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </section>

                {/* Max Steps Selector */}
                <section ref={maxDropdownRef} className="run-settings-section">
                  <button
                    onClick={() => {
                      setIsEffortDropdownOpen(false);
                      setIsAgentModeDropdownOpen(false);
                      setIsMaxDropdownOpen((prev) => !prev);
                    }}
                    title="Maximum agent steps per run"
                    className="run-settings-section-toggle" aria-expanded={isMaxDropdownOpen}
                  >
                    <span className="text-(--text-secondary)"><Gauge size={11} /></span>
                    <span className="run-settings-section-label">Step limit</span>
                    <span className="run-settings-section-value">{maxIterations === 0 ? 'Auto' : maxIterations}</span>
                    <ChevronDown size={11} className={`transition-transform duration-300 opacity-40 ${isMaxDropdownOpen ? 'rotate-180' : ''}`} />
                  </button>

                  {isMaxDropdownOpen && (
                    <div data-pane-menu
                      ref={maxDropdownMenuRef}

                      className="run-settings-options"
                    >
                      <div className="px-4 py-2 text-[9px] font-bold uppercase tracking-widest text-(--text-muted) border-b border-(--border-subtle)">
                        Max steps per run
                      </div>
                      {[0, 5, 10, 20, 30, 50, 100].map((n) => {
                        const active = maxIterations === n;
                        return (
                          <button
                            key={n}
                            onClick={() => {
                              setMaxIterations(n);
                            }}
                            className={`w-full flex items-center gap-2.5 px-4 py-2.5 transition-all text-left ${
                              active
                                ? 'bg-(--accent-solid) text-(--app-bg)'
                                : 'hover:bg-(--surface-1)'
                            }`}
                          >
                            <span className={`text-[11px] font-bold tabular-nums ${active ? 'text-(--app-bg)' : 'text-(--text-primary)'}`}>{n === 0 ? 'Auto' : n}</span>
                            <span className={`text-[9px] font-medium ${active ? 'text-(--app-bg) opacity-70' : 'text-(--text-muted)'}`}>{n === 0 ? 'No step limit' : 'steps'}</span>
                            {active && (
                              <div className="ml-auto w-1 h-5 rounded-full bg-(--app-bg)/20 shadow-xs" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </section>

                {activeSessionId && instanceName && <SessionPermissions key={`${instanceName}:${activeSessionId}`} instanceName={instanceName} sessionId={activeSessionId} />}
                    </div>, document.body)}
                </div>

                {subagentControl}
              </div>
              </div>
            ) : null
          }
      >
        <div data-session-view={activeSessionId ?? undefined} className="relative flex h-full w-full overflow-hidden">
          {/* Chat Area */}
          <main className="focus-chat-surface order-5 relative z-0 flex-1 flex flex-col min-w-0 bg-(--surface-0) overflow-hidden">

            {/* Session toolbar items moved into the pane-header actions */}

            {/* Messages */}
            <div
                ref={scrollRef}
                onScroll={onMessagesScroll}
            className="session-chat-messages flex-1 overflow-y-auto p-4 md:p-6 space-y-6"
            >
              {messagesLoading && messages.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center gap-4 text-(--text-muted) animate-in">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-(--surface-2) text-(--text-primary)">
                      <Loader2 size={24} className="animate-spin" />
                    </div>
                    <p className="text-[10px] font-bold uppercase tracking-widest">Loading conversation...</p>
                  </div>
              ) : (
                  <>
                    {!activeSessionId ? (
                        <div className="h-full flex flex-col items-center justify-center text-center p-8">
                          <EmptySessionLogo />
                          <p className="text-sm font-medium text-(--text-muted)">Start a conversation</p>

                        </div>
                    ) : messages.length === 0 && (
                        <div className="h-full flex flex-col items-center justify-center text-center p-8">
                          <EmptySessionLogo />
                          <p className="text-sm font-medium text-(--text-muted)">No messages in this session yet.</p>
                          <p className="text-xs text-(--text-muted)">Start a conversation to see it here.</p>
                        </div>
                    )}

                    {messages.length > 0 && isLoadingOlderMessages && (
                      <div className="flex justify-center">
                        <div className="inline-flex items-center gap-1.5 rounded-full border border-(--border-subtle) bg-(--surface-0) px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
                          <Loader2 size={11} className="animate-spin" />
                          Loading older messages
                        </div>
                      </div>
                    )}

                    <ChatTurnTimeline key={activeSessionId ?? 'draft'} rows={chatTimeline} busy={streamBusy || messagesLoading} renderRow={row => {
                      if (row.kind === 'message') {
                        const m = row.message;
                        const persistedRetryError = isObjectRecord(m.metadata) && typeof m.metadata.retryable_error === 'string'
                            ? m.metadata.retryable_error
                            : null;
                        // Older retries may have erased their error before finishing.
                        // Keep the last unanswered attempt recoverable once the run is idle.
                        const unansweredRetry = !streamBusy && !messagesLoading &&
                            messages.at(-1)?.id === m.id && m.role === 'user' &&
                            isObjectRecord(m.metadata) && isObjectRecord(m.metadata.retry_settings);
                        const retryError = retryCandidate?.messageId === m.id
                            ? retryCandidate.error
                            : persistedRetryError ?? (unansweredRetry ? 'No reply was saved for this attempt. Retry with the current settings.' : null);
                        return (
                        <SessionMessageCard
                          key={row.key}
                          message={m}
                          toolArgumentsByCallId={toolArgumentsByCallId}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                          onRetryMessage={retryError ? retryFailedMessage : undefined}
                          retryError={retryError}
                          retrying={retryingMessageId === m.id}
                        />
                        );
                      }
                      if (row.kind === 'tool') {
                        return <StreamToolCard key={row.key} call={row.call} active={row.active} sessionId={activeSessionId}
                          onResolveApproval={resolveApprovalInline} resolvingApprovalKey={resolvingApprovalKey}
                          onOpenPane={openOrFocusPane} />;
                      }
                      return (
                        <div key={row.key} className="flex flex-col gap-1.5 animate-in items-start w-full">
                          <div className="flex items-center gap-2 px-1">
                            <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
                              assistant • {row.streaming ? 'streaming' : 'interim'}
                            </span>
                          </div>
                          <div className="chat-message-surface chat-message-assistant max-w-[90%] rounded-2xl rounded-tl-none px-4 py-1.5 text-xs font-medium shadow-xs border bg-(--surface-1) border-(--border-subtle)">
                            <Markdown content={row.text} />
                          </div>
                        </div>
                      );
                    }} />

                    {showThinkingIndicator && (
                        <div className="session-thinking" role="status">
                          <span className="session-thinking-dots" aria-hidden="true"><i /><i /><i /></span>
                          <span className="session-thinking-shimmer">Sentinel is thinking</span>
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

                  </>
              )}
            </div>

            <ConversationNavigator key={activeSessionId ?? 'draft'} scrollRef={scrollRef} revision={chatTimeline} />

            {activeForm && <AgentForm key={activeForm.form_id} form={activeForm} disabled={streamBusy} onSubmit={response => {
              if (streamBusy || !isStreamOpen()) return false;
              return sendStreamMessage({ type: 'message', content: 'Form answers', form_response: response, tier: selectedTier, ...selectedChoice, max_iterations: maxIterations, agent_mode: selectedAgentMode ?? undefined });
            }} />}

            {/* Composer */}
            <div className="chat-composer-area">
              <>
                <ComposerTerminalPills
                  key={`${activeInstanceName}:${activeSessionId}`}
                  panes={activePanes}
                  focusedPaneId={focusedPaneId}
                  onOpen={openOrFocusPane}
                  onClose={closePane}
                />
                <form onSubmit={sendMessage} className="chat-composer" data-running={stopVisible || undefined}>
                    <button
                        type="button"
                        onClick={() => scrollToBottom('smooth')}
                        className="session-latest-button"
                        data-visible={!isPinnedToBottom}
                        aria-hidden={isPinnedToBottom || undefined}
                        tabIndex={isPinnedToBottom ? -1 : 0}
                        disabled={isPinnedToBottom}
                        title="Scroll to latest message"
                        aria-label="Scroll to latest message"
                      >
                        <ArrowDown size={16} />
                    </button>
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
                        aria-label="Message"
                        rows={2}
                        value={composer}
                        onChange={(e) => setComposer(e.target.value)}
                        onPaste={onComposerPaste}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendMessage(e as any);
                          }
                        }}
                        disabled={!activeInstanceName || creatingChat || isCompacting || streaming.isCompactingContext}
                        placeholder={isCompacting || streaming.isCompactingContext ? 'Compacting context…' : streamBusy ? 'Add a direction for Sentinel…' : 'Ask Sentinel anything...'}
                        className="chat-composer-input"
                    />
                      <div className="chat-composer-toolbar">
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={!activeInstanceName || creatingChat || sendingSteering || composerAttachments.length >= MAX_IMAGE_ATTACHMENTS}
                          className="chat-composer-attach"
                          title="Attach images · up to 4, 5MB each"
                          aria-label="Attach images"
                        >
                          <Paperclip size={16} />
                          <span>Attach</span>
                        </button>
                        <div className="chat-composer-actions">
                          <span className="chat-composer-shortcuts"><kbd>↵</kbd> {streamBusy ? 'Steer' : 'Send'} <span>·</span> <kbd>⇧ ↵</kbd> New line</span>
                          <button
                              type={stopVisible ? 'button' : 'submit'}
                              onClick={stopVisible ? stopCurrent : undefined}
                              aria-label={stopVisible ? (isStopping ? 'Stopping generation' : 'Stop generation') : creatingChat ? 'Starting chat' : streamBusy ? 'Steer agent' : 'Send message'}
                              disabled={stopVisible ? isStopping : !activeInstanceName || creatingChat || sendingSteering || isCompacting || streaming.isCompactingContext || (composer.trim().length === 0 && composerAttachments.length === 0)}
                              className="chat-composer-send"
                              data-running={stopVisible || undefined}
                              data-stopping={isStopping || undefined}
                          >
                            <span className="chat-composer-send-icon" aria-hidden="true">
                              {creatingChat || sendingSteering ? <Loader2 size={18} className="animate-spin" /> : <ArrowUp size={18} strokeWidth={2.25} />}
                            </span>
                            <span className="chat-composer-stop-content" aria-hidden="true">
                              <Square size={9} fill="currentColor" />
                              <span>{isStopping ? 'Stopping…' : 'Stop'}</span>
                            </span>
                          </button>
                        </div>
                      </div>
                </form>
                {composerAttachments.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {composerAttachments.map((item, index) => (
                          <div key={`${item.base64.slice(0, 24)}-${index}`} className="relative rounded-lg border border-(--border-subtle) bg-(--surface-1) p-1.5">
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

              </>
            </div>
          </main>

          {workbenchVisible ? (
            <>
              <div
                className="order-4 relative hidden xl:block w-0 shrink-0"
              />
              <div
                className={`absolute inset-y-0 z-40 hidden xl:block w-3 -translate-x-1/2 cursor-col-resize transition-colors ${isWorkbenchResizing ? 'bg-(--accent-solid)/20' : 'hover:bg-(--accent-solid)/10'}`}
                onMouseDown={startWorkbenchResizing}
                style={{ left: `${workbenchWidth}px` }}
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

        {modelSwitch && <ModelSwitchDialog
          model={modelSwitch.model}
          providerId={modelSwitch.choice.provider_id}
          reasoning={modelSwitch.choice.reasoning_level}
          exceedsContext={Boolean(modelSwitch.requires_compaction)}
          onCancel={() => { modelCheckSequence.current += 1; setModelSwitch(null); setModelPreview(null); }}
          onConfirm={() => void switchModel(modelSwitch.tier, true, modelSwitch.choice)}
        />}

        {confirmTerminateTaskId && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-xs" onClick={() => setConfirmTerminateTaskId(null)} />
            <div className="relative rounded-xl border border-(--border-subtle) bg-(--surface-0) shadow-2xl p-6 w-full max-w-sm animate-in zoom-in-95 duration-150 space-y-4">
              <h2 className="text-sm font-bold uppercase tracking-widest">Terminate Sub-Agent?</h2>
              <p className="text-xs text-(--text-secondary) leading-relaxed">
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
