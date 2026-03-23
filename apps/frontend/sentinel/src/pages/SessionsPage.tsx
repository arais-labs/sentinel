import {
  ArrowDown,
  Bot,
  ChevronDown,
  Expand,
  History,
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
  Globe,
  ExternalLink,
  BadgeCheck,
  Zap,
  Activity,
  Brain,
  Sparkles,
  Paperclip,
  Folder,
  FileCode2,
  ChevronRight,
  ArrowUp,
  Clock3,
  Check,
  Pencil,
  GitBranch,
  Boxes,
} from 'lucide-react';
import { ChangeEvent, ClipboardEvent, FormEvent, useEffect, useMemo, useRef, useState, memo, useCallback, useLayoutEffect } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { SessionMessageCard, buildToolArgumentsByCallId } from '../components/session/SessionMessageCard';
import { SessionHistorySidebar } from '../components/session/SessionHistorySidebar';
import { DesktopPreview } from '../components/session/DesktopPreview';
import { SubAgentTaskModal } from '../components/SubAgentTaskModal';
import { SpawnSubAgentModal } from '../components/SpawnSubAgentModal';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Markdown } from '../components/ui/Markdown';
import { StatusChip } from '../components/ui/StatusChip';
import { WS_BASE_URL } from '../lib/env';
import { formatCompactDate, toPrettyJson, truncate } from '../lib/format';
import { extractCriticalToolFields, parsePayloadJson, previewPayloadValue, topLevelPayloadFieldCount, type ToolPayloadKind } from '../lib/toolPayloadPreview';
import { buildRuntimeCommandRows } from '../lib/runtimeCommands';
import {
  approvalKey,
  approvalRefFromMetadata,
  isWaitingApproval,
  type ApprovalRef,
} from '../lib/approvals';
import { api } from '../lib/api';
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
  SessionRuntimeStatus,
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

function runtimeStatusLabel(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'Unavailable';
  if (!runtime.runtime_exists) return 'Missing';
  return runtime.active ? 'Active' : 'Idle';
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

function ToolFieldPreviewList({
  items,
  extraCount = 0,
}: {
  items: Array<{ key: string; text: string; truncated: boolean; redacted?: boolean }>;
  extraCount?: number;
}) {
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.key} className="rounded-lg border border-sky-500/10 bg-sky-500/5 p-2">
          <p className="text-[9px] font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400">{item.key}</p>
          <p className="mt-1 font-mono text-[12px] break-words text-[color:var(--text-primary)]">{item.text || '""'}</p>
          {item.redacted ? (
            <p className="mt-1 text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">Sensitive value hidden</p>
          ) : null}
          {item.truncated ? (
            <p className="mt-1 text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">Truncated in preview</p>
          ) : null}
        </div>
      ))}
      {extraCount > 0 ? (
        <p className="text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">+{extraCount} more field{extraCount === 1 ? '' : 's'}</p>
      ) : null}
    </div>
  );
}

function ToolPayloadView({
  raw,
  emptyLabel,
  showRawJson = true,
  toolName,
  payloadKind,
  criticalOnly = false,
  maxCriticalFields = 3,
}: {
  raw: string;
  emptyLabel: string;
  showRawJson?: boolean;
  toolName?: string;
  payloadKind?: ToolPayloadKind;
  criticalOnly?: boolean;
  maxCriticalFields?: number;
}) {
  const parsed = useMemo(() => parsePayloadJson(raw), [raw]);
  const criticalFields = useMemo(() => {
    if (!toolName || !payloadKind) return [];
    return extractCriticalToolFields({
      toolName,
      raw,
      kind: payloadKind,
      maxFields: maxCriticalFields,
    });
  }, [toolName, raw, payloadKind, maxCriticalFields]);

  if (!raw.trim()) {
    return <p className="text-sky-500/60 italic">{emptyLabel}</p>;
  }

  if (criticalOnly) {
    if (criticalFields.length > 0) {
      const extraCount = Math.max(0, topLevelPayloadFieldCount(raw) - criticalFields.length);
      return <ToolFieldPreviewList items={criticalFields} extraCount={extraCount} />;
    }
    const preview = previewPayloadValue(parsed ?? raw, 220);
    return (
      <ToolFieldPreviewList
        items={[
          {
            key: payloadKind ?? 'payload',
            text: preview.text || '""',
            truncated: preview.truncated,
          },
        ]}
      />
    );
  }

  if (isObjectRecord(parsed)) {
    const entries = Object.entries(parsed).map(([key, value]) => {
      const preview = previewPayloadValue(value);
      return { key, text: preview.text, truncated: preview.truncated };
    });
    return (
      <div className="space-y-2">
        <ToolFieldPreviewList items={entries} />
        {showRawJson ? (
          <details className="group">
            <summary className="cursor-pointer list-none flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-sky-600/80 dark:text-sky-400/80">
              <ChevronDown size={12} className="group-open:rotate-180 transition-transform" />
              Raw JSON
            </summary>
            <JsonBlock value={JSON.stringify(parsed, null, 2)} className="mt-2 bg-transparent border-sky-500/10 p-2 max-h-[220px]" />
          </details>
        ) : null}
      </div>
    );
  }

  if (parsed !== null) {
    const preview = previewPayloadValue(parsed, 260);
    return (
      <div className="space-y-2">
        <ToolFieldPreviewList
          items={[
            {
              key: payloadKind ?? 'value',
              text: preview.text || '""',
              truncated: preview.truncated,
            },
          ]}
        />
        {showRawJson ? (
          <details className="group">
            <summary className="cursor-pointer list-none flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-sky-600/80 dark:text-sky-400/80">
              <ChevronDown size={12} className="group-open:rotate-180 transition-transform" />
              Raw JSON
            </summary>
            <JsonBlock value={JSON.stringify(parsed, null, 2)} className="mt-2 bg-transparent border-sky-500/10 p-2 max-h-[220px]" />
          </details>
        ) : null}
      </div>
    );
  }

  return (
    <details className="group">
      <summary className="cursor-pointer list-none flex items-center gap-2 text-sky-600 dark:text-sky-400">
        <ChevronDown size={14} className="group-open:rotate-180 transition-transform" />
        <span className="font-bold uppercase tracking-widest text-[10px]">Execution Telemetry</span>
      </summary>
      <div className="mt-3 overflow-auto">
        <Markdown content={raw} />
      </div>
    </details>
  );
}

function ToolPayloadCompactSummary({
  toolName,
  inputRaw,
  outputRaw,
  outputEmptyLabel,
  outputError = false,
  hideInput = false,
}: {
  toolName: string;
  inputRaw: string;
  outputRaw: string;
  outputEmptyLabel: string;
  outputError?: boolean;
  hideInput?: boolean;
}) {
  const inputFields = useMemo(
    () => extractCriticalToolFields({ toolName, raw: inputRaw, kind: 'input', maxFields: 2 }),
    [toolName, inputRaw],
  );
  const outputFields = useMemo(
    () => extractCriticalToolFields({ toolName, raw: outputRaw, kind: 'output', maxFields: 2 }),
    [toolName, outputRaw],
  );

  const compactValue = (value: string): string => {
    const trimmed = value.replace(/\s+/g, ' ').trim();
    if (trimmed.length <= 56) return trimmed;
    return `${trimmed.slice(0, 56)}…`;
  };

  const renderFieldChips = (items: Array<{ key: string; text: string; redacted?: boolean }>) => {
    if (!items.length) {
      return <span className="text-[10px] text-[color:var(--text-muted)] italic">none</span>;
    }
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {items.map((item) => (
          <span
            key={item.key}
            className="inline-flex max-w-full items-center gap-1 rounded-md border border-sky-500/20 bg-sky-500/8 px-1.5 py-0.5"
          >
            <span className="text-[9px] font-bold uppercase tracking-wide text-sky-600 dark:text-sky-400">
              {item.key}
            </span>
            <span className="font-mono text-[10px] text-[color:var(--text-primary)] break-all">
              {item.redacted ? '[redacted]' : compactValue(item.text || '""')}
            </span>
          </span>
        ))}
      </div>
    );
  };

  return (
    <div className="mt-2 border-t border-sky-500/10 pt-2 space-y-1.5 animate-in fade-in duration-200 max-w-[620px]">
      {!hideInput ? (
        <div className="min-w-0 flex items-start gap-2">
          <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] pt-0.5 shrink-0">Input</p>
          <div className="min-w-0 flex-1">{renderFieldChips(inputFields)}</div>
        </div>
      ) : null}
      <div className="min-w-0 flex items-start gap-2">
        <div className="flex items-center gap-1.5 pt-0.5 shrink-0">
          <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Output</p>
          {outputError ? <span className="h-1.5 w-1.5 rounded-full bg-rose-400" title="Error output" /> : null}
        </div>
        <div className="min-w-0 flex-1">
          {outputRaw.trim() ? renderFieldChips(outputFields) : (
            <span className="text-[10px] text-[color:var(--text-muted)] italic">{outputEmptyLabel}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// --- Memoized Components ---

// --- Types ---

interface StreamingToolCall {
  id: string;
  name: string;
  argumentsJson: string;
  outputJson: string;
  isError: boolean;
  metadata: Record<string, unknown>;
  complete: boolean;
  contentIndex: number | null;
}

interface StreamTimelineToolItem {
  kind: 'tool';
  key: string;
  callKey: string;
}

interface StreamTimelineTextItem {
  kind: 'text';
  key: string;
  text: string;
}

type StreamTimelineItem = StreamTimelineToolItem | StreamTimelineTextItem;

interface StreamingState {
  connection: WsConnectionState;
  isThinking: boolean;
  isStreaming: boolean;
  isCompactingContext: boolean;
  text: string;
  timeline: StreamTimelineItem[];
  interimTextSeq: number;
  activeToolCalls: StreamingToolCall[];
  completedToolCalls: StreamingToolCall[];
  agentIteration: number;
  agentMaxIterations: number;
}

interface WorkbenchTab {
  path: string;
  name: string;
  size_bytes: number;
  modified_at: string | null;
  content: string;
  truncated: boolean;
  max_bytes: number;
}

const defaultStreamingState: StreamingState = {
  connection: 'disconnected',
  isThinking: false,
  isStreaming: false,
  isCompactingContext: false,
  text: '',
  timeline: [],
  interimTextSeq: 0,
  activeToolCalls: [],
  completedToolCalls: [],
  agentIteration: 0,
  agentMaxIterations: 0,
};

function streamingCallKeyFromParts(id: string, contentIndex: number | null): string {
  return `${id}::${contentIndex ?? 'na'}`;
}

function streamingCallKey(call: StreamingToolCall): string {
  return streamingCallKeyFromParts(call.id, call.contentIndex);
}

// --- Sub-Components ---

function StreamToolCard({
  call,
  active,
  onResolveApproval,
  resolvingApprovalKey,
}: {
  call: StreamingToolCall;
  active: boolean;
  onResolveApproval: (approval: ApprovalRef, decision: 'approve' | 'reject') => void;
  resolvingApprovalKey: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const isScreenshotCall = call.name.toLowerCase().includes('screenshot');
  const pendingApproval = isWaitingApproval(call.metadata);
  const approvalRef = pendingApproval ? approvalRefFromMetadata(call.metadata) : null;
  const canResolveApproval = pendingApproval && approvalRef?.canResolve === true;
  const approvalLinkMissing = pendingApproval && !approvalRef;
  const approvalActionBusy = approvalRef ? resolvingApprovalKey === approvalKey(approvalRef) : false;

  useEffect(() => {
    if (pendingApproval) {
      setExpanded(true);
    }
  }, [pendingApproval]);

  return (
      <div className="flex flex-col gap-1.5 animate-in items-start w-full">
        <div className="flex items-center gap-2 px-1">
        <span className={`text-[9px] font-bold uppercase tracking-[0.2em] ${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-400'}`}>
          {pendingApproval ? 'tool_call • waiting approval' : `tool_call • ${active ? 'running' : 'complete'}`}
        </span>
        </div>
        <div className={`${expanded ? 'w-full max-w-[90%]' : 'w-fit max-w-[90%]'} inline-flex flex-col rounded-2xl rounded-tl-none border ${pendingApproval ? 'border-rose-500/35 bg-rose-500/10' : 'border-sky-500/20 bg-sky-500/5'} px-4 py-1.5 text-[12px] shadow-sm`}>
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className={`${expanded ? 'w-full' : 'w-auto'} flex items-center justify-between gap-3 text-left`}
          >
            <div className={`flex items-center gap-2 font-mono font-bold min-w-0 ${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-400'}`}>
              <Wrench size={14} className="shrink-0" />
              <span className="truncate">{call.name}</span>
              {pendingApproval ? (
                <span className="inline-flex items-center rounded-full border border-rose-500/35 bg-rose-500/15 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-rose-300">
                  Pending
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {active && <Loader2 size={12} className={`animate-spin ${pendingApproval ? 'text-rose-300' : 'text-sky-500'}`} />}
              <ChevronDown size={14} className={`${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-400'} transition-transform ${expanded ? 'rotate-180' : ''}`} />
            </div>
          </button>
          {expanded ? (
            <div className={`mt-3 border-t border-sky-500/10 pt-3 grid ${isScreenshotCall ? 'grid-cols-1' : 'grid-cols-2'} gap-3 animate-in fade-in duration-200`}>
              {!isScreenshotCall ? (
                <div className="min-w-0">
                  <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Input</p>
                  <ToolPayloadView
                    raw={call.argumentsJson}
                    emptyLabel="No input payload."
                    toolName={call.name}
                    payloadKind="input"
                  />
                </div>
              ) : null}
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Output</p>
                  {call.isError && (
                    <span className="inline-flex items-center rounded-full border border-rose-500/30 bg-rose-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-rose-500">
                      Error
                    </span>
                  )}
                </div>
                <div className="space-y-2">
                  <ToolPayloadView
                    raw={call.outputJson}
                    emptyLabel={active ? 'Running tool...' : 'No output payload.'}
                    showRawJson={!isScreenshotCall}
                    toolName={call.name}
                    payloadKind="output"
                  />
                  {canResolveApproval && approvalRef ? (
                    <div className="flex items-center gap-2 pt-1">
                      <button
                        type="button"
                        onClick={() => onResolveApproval(approvalRef, 'reject')}
                        disabled={approvalActionBusy}
                        className="inline-flex items-center gap-1 rounded-md border border-rose-500/35 bg-rose-500/10 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-rose-300 hover:bg-rose-500/20 disabled:opacity-60"
                      >
                        {approvalActionBusy ? <Loader2 size={11} className="animate-spin" /> : null}
                        Reject
                      </button>
                      <button
                        type="button"
                        onClick={() => onResolveApproval(approvalRef, 'approve')}
                        disabled={approvalActionBusy}
                        className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-60"
                      >
                        {approvalActionBusy ? <Loader2 size={11} className="animate-spin" /> : null}
                        Approve
                      </button>
                    </div>
                  ) : null}
                  {approvalLinkMissing ? (
                    <p className="text-[10px] leading-relaxed text-amber-300">
                      Pending approval detected but controls are unavailable. Refresh and stop/retry this run if it remains stuck.
                    </p>
                  ) : null}
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

// --- Module Action Log ---

const MODULE_OP_STYLES: Record<string, { label: string; color: string }> = {
  list_modules:  { label: 'list modules',  color: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  get_module:    { label: 'get module',    color: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  create_module: { label: 'create module', color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' },
  delete_module: { label: 'delete module', color: 'text-rose-400 bg-rose-500/10 border-rose-500/20' },
  list_records:  { label: 'list records',  color: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  get_record:    { label: 'get record',    color: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  create_record: { label: 'create record', color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' },
  update_record: { label: 'update record', color: 'text-amber-400 bg-amber-500/10 border-amber-500/20' },
  delete_record: { label: 'delete record', color: 'text-rose-400 bg-rose-500/10 border-rose-500/20' },
  run_action:    { label: 'run action',    color: 'text-purple-400 bg-purple-500/10 border-purple-500/20' },
};

interface ModuleLogEntry {
  id: string;
  command: string;
  module?: string;
  record_id?: string;
  action_id?: string;
  argsJson: string;
  outputJson: string;
  isError: boolean;
  isActive: boolean;
}

function ModuleActionEntryRow({ entry }: { entry: ModuleLogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const style = MODULE_OP_STYLES[entry.command] ?? {
    label: entry.command,
    color: 'text-[color:var(--text-muted)] bg-[color:var(--surface-2)] border-[color:var(--border-subtle)]',
  };
  return (
    <div className={`rounded-lg border overflow-hidden ${entry.isError ? 'border-rose-500/30 bg-rose-500/5' : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]'}`}>
      <button
        type="button"
        onClick={() => setExpanded(p => !p)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[color:var(--surface-1)] transition-colors"
      >
        <div className={`h-1.5 w-1.5 rounded-full shrink-0 ${entry.isError ? 'bg-rose-500' : entry.isActive ? 'bg-sky-400 animate-pulse' : 'bg-emerald-500'}`} />
        <span className={`text-[9px] font-bold uppercase tracking-widest border rounded px-1.5 py-0.5 shrink-0 ${style.color}`}>
          {style.label}
        </span>
        <div className="flex-1 min-w-0 flex items-center gap-1.5 overflow-hidden">
          {entry.module && <span className="text-[10px] font-medium text-[color:var(--text-primary)] truncate">{entry.module}</span>}
          {entry.record_id && <span className="text-[9px] text-[color:var(--text-muted)] shrink-0">· {entry.record_id.slice(0, 8)}</span>}
          {entry.action_id && <span className="text-[9px] text-[color:var(--text-muted)] shrink-0 truncate">· {entry.action_id}</span>}
        </div>
        <ChevronDown size={12} className={`shrink-0 text-[color:var(--text-muted)] transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <div className="border-t border-[color:var(--border-subtle)] p-3 space-y-3 animate-in fade-in duration-150">
          <div>
            <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Input</p>
            <ToolPayloadView raw={entry.argsJson} emptyLabel="No input." toolName="module_manager" />
          </div>
          {entry.outputJson && (
            <div>
              <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Output</p>
              <ToolPayloadView raw={entry.outputJson} emptyLabel="No output." toolName="module_manager" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ModuleActionLog({ completedToolCalls, activeToolCalls, messages }: {
  completedToolCalls: StreamingToolCall[];
  activeToolCalls: StreamingToolCall[];
  messages: Message[];
}) {
  const [moduleNames, setModuleNames] = useState<Set<string>>(new Set(['module_manager']));

  useEffect(() => {
    let mounted = true;
    fetch('/api/modules', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!mounted || !data) return;
        const names = new Set<string>(['module_manager']);
        for (const m of (data.modules || [])) names.add(m.name);
        setModuleNames(names);
      })
      .catch(() => {});
    return () => { mounted = false; };
  }, []);

  const entries = useMemo<ModuleLogEntry[]>(() => {
    const result: ModuleLogEntry[] = [];
    const seenIds = new Set<string>();

    // 1. Active streaming calls (live, at the top)
    for (const call of [...activeToolCalls].reverse()) {
      if (!moduleNames.has(call.name)) continue;
      seenIds.add(call.id);
      try {
        const args = JSON.parse(call.argumentsJson || '{}');
        const isManager = call.name === 'module_manager';
        result.push({ id: `active-${call.id}`, command: args.command || '?', module: isManager ? (args.module || args.name) : call.name, record_id: args.record_id, action_id: args.action_id, argsJson: call.argumentsJson, outputJson: call.outputJson || '', isError: call.isError, isActive: true });
      } catch { /* ignore */ }
    }

    // 2. Completed streaming calls (current ws session)
    for (const call of [...completedToolCalls].reverse()) {
      if (!moduleNames.has(call.name)) continue;
      seenIds.add(call.id);
      try {
        const args = JSON.parse(call.argumentsJson || '{}');
        const isManager = call.name === 'module_manager';
        result.push({ id: call.id, command: args.command || '?', module: isManager ? (args.module || args.name) : call.name, record_id: args.record_id, action_id: args.action_id, argsJson: call.argumentsJson, outputJson: call.outputJson || '', isError: call.isError, isActive: false });
      } catch { /* ignore */ }
    }

    // 3. Persisted messages (past turns / page refresh)
    const argsById = buildToolArgumentsByCallId(messages);
    const toolMsgs = [...messages]
      .filter(m => m.role === 'tool_result' && m.tool_name && moduleNames.has(m.tool_name))
      .reverse();
    for (const msg of toolMsgs) {
      if (!msg.tool_call_id || seenIds.has(msg.tool_call_id)) continue;
      seenIds.add(msg.tool_call_id);
      const argsJson = argsById.get(msg.tool_call_id) ?? '{}';
      try {
        const args = JSON.parse(argsJson);
        const isManager = msg.tool_name === 'module_manager';
        result.push({ id: msg.tool_call_id, command: args.command || '?', module: isManager ? (args.module || args.name) : msg.tool_name!, record_id: args.record_id, action_id: args.action_id, argsJson, outputJson: msg.content || '', isError: false, isActive: false });
      } catch { /* ignore */ }
    }

    return result;
  }, [completedToolCalls, activeToolCalls, messages, moduleNames]);

  if (entries.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-[color:var(--text-muted)] opacity-40 gap-2 p-8">
        <Boxes size={24} strokeWidth={1} />
        <p className="text-[10px] font-medium uppercase tracking-widest text-center">No module activity this session</p>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-3 space-y-1.5">
      {entries.map(entry => <ModuleActionEntryRow key={entry.id} entry={entry} />)}
    </div>
  );
}

// --- Main Page Component ---

export function SessionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id: routeSessionId } = useParams<{ id: string }>();

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

  const [streaming, setStreaming] = useState<StreamingState>(defaultStreamingState);
  const [resolvingApprovalKey, setResolvingApprovalKey] = useState<string | null>(null);

  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [rightRailTab, setRightRailTab] = useState<'desktop' | 'sub_agents' | 'runtime' | 'modules'>('desktop');
  const [runtimeStatus, setRuntimeStatus] = useState<SessionRuntimeStatus | null>(null);
  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);
  const [runtimeInspectorTab, setRuntimeInspectorTab] = useState<'files' | 'commands'>('files');
  const [runtimePath, setRuntimePath] = useState('');
  const [runtimeChangedFiles, setRuntimeChangedFiles] = useState<SessionRuntimeGitChangedFilesResponse | null>(null);
  const [runtimeChangedFilesLoading, setRuntimeChangedFilesLoading] = useState(false);
  const [runtimeCommandOutputCollapsed, setRuntimeCommandOutputCollapsed] = useState<Record<string, boolean>>({});
  const [workbenchTabs, setWorkbenchTabs] = useState<WorkbenchTab[]>([]);
  const [activeWorkbenchPath, setActiveWorkbenchPath] = useState<string | null>(null);
  const [workbenchLoadingPath, setWorkbenchLoadingPath] = useState<string | null>(null);
  const [workbenchWidth, setWorkbenchWidth] = useState(520);
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
  const [mode, setMode] = useState<'solo' | 'advanced'>(
      () => (localStorage.getItem('sentinel-mode') as 'solo' | 'advanced') ?? 'advanced',
  );

  const hasActiveSubAgentTasks = tasks.some((task) => task.status === 'running' || task.status === 'pending');

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
    if (!isEffortDropdownOpen && !isAgentModeDropdownOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (effortDropdownRef.current?.contains(target)) return;
      if (agentModeDropdownRef.current?.contains(target)) return;
      setIsEffortDropdownOpen(false);
      setIsAgentModeDropdownOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [isEffortDropdownOpen, isAgentModeDropdownOpen]);

  const [isCompacting, setIsCompacting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isResettingRuntime, setIsResettingRuntime] = useState(false);
  const [isRestartingContainer, setIsRestartingContainer] = useState(false);
  const [resetMenuOpen, setResetMenuOpen] = useState(false);
  const resetMenuRef = useRef<HTMLDivElement>(null);
  const [isDesktopFullscreen, setIsDesktopFullscreen] = useState(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
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
  const effortDropdownRef = useRef<HTMLDivElement | null>(null);
  const agentModeDropdownRef = useRef<HTMLDivElement | null>(null);
  const fullscreenFrameRef = useRef<HTMLIFrameElement | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const activeSessionIdRef = useRef<string | null>(routeSessionId ?? null);
  const contextUsageRequestRef = useRef(0);
  const wsInstanceRef = useRef(0);

  // Keep refs in sync so WS callbacks can read current values
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);

  const streamBusy =
    streaming.isThinking ||
    streaming.isStreaming ||
    streaming.isCompactingContext ||
    streaming.activeToolCalls.length > 0 ||
    streaming.agentIteration > 0 ||
    isCompacting;

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

  const runtimeCommandActions = useMemo(() => {
    return buildRuntimeCommandRows(runtimeStatus, { newestFirst: true, limit: 50 });
  }, [runtimeStatus]);

  const toggleRuntimeCommandOutput = useCallback((rowId: string) => {
    setRuntimeCommandOutputCollapsed((current) => ({
      ...current,
      [rowId]: !(current[rowId] ?? true),
    }));
  }, []);

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

  const onSessionClick = useCallback((id: string) => {
    const previousId = activeSessionIdRef.current;
    if (previousId) {
      markSessionRead(previousId);
    }
    setActiveSessionId(id);
    navigate(`/sessions/${id}`);
    markSessionRead(id);
  }, [markSessionRead, navigate]);

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
    const newWidth = window.innerWidth - e.clientX;
    const clamped = Math.max(300, Math.min(800, newWidth));
    setRightPanelWidth(clamped);
  }, [isResizing]);

  const resizeWorkbench = useCallback((e: MouseEvent) => {
    if (!isWorkbenchResizing) return;
    const maxWidth = Math.max(420, window.innerWidth - rightPanelWidth - 360);
    const newWidth = window.innerWidth - rightPanelWidth - e.clientX;
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
    void fetchSessions({ autoSelectIfEmpty: true });
    void fetchModels();
    void fetchAgentModes();
    void fetchLiveView();
  }, []);

  // Re-fetch live view when the active session changes
  useEffect(() => {
    setRuntimeBooting(true);
    void fetchLiveView();
  }, [activeSessionId]);

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
      setRuntimeStatus(null);
      setRuntimeFiles(null);
      setRuntimePath('');
      setRuntimeChangedFiles(null);
      setRuntimeChangedFilesLoading(false);
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
    setRuntimeStatus(null);
    setRuntimeFiles(null);
    setRuntimePath('');
    setRuntimeChangedFiles(null);
    setRuntimeChangedFilesLoading(false);
    setWorkbenchTabs([]);
    setActiveWorkbenchPath(null);
    setWorkbenchShowDiffByPath({});
    setWorkbenchDiffByPath({});
    setWorkbenchDiffErrorByPath({});
    setWorkbenchDiffBaseRefByPath({});
    setWorkbenchGitRootsByPath({});
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
    void fetchRuntimeStatus(activeSessionId);
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
    if (!activeSessionId || rightRailTab !== 'runtime') return;
    if (streaming.connection !== 'connected') return;
    const timer = window.setInterval(() => {
      void fetchRuntimeStatus(activeSessionId, 120);
      void fetchRuntimeFiles(activeSessionId, runtimePath, {
        refreshGit: true,
        silent: true,
      });
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, rightRailTab, runtimePath, streaming.connection]);

  useEffect(() => {
    if (!activeSessionId || rightRailTab !== 'runtime') return;
    void fetchRuntimeChangedFilesForExplorer(activeSessionId, runtimePath);
  }, [activeSessionId, rightRailTab, runtimePath]);

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
        navigate(`/sessions/${defaultSession.id}`, { replace: true });
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load sessions');
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
          navigate(`/sessions/${fallbackId}`, { replace: true });
        } else {
          navigate('/sessions', { replace: true });
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
      navigate(`/sessions/${updated.id}`);
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
            navigate(`/sessions/${fallbackId}`, { replace: true });
          } else {
            navigate('/sessions', { replace: true });
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
      navigate(`/sessions/${fresh.id}`, { replace: true });
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

  async function fetchRuntimeStatus(sessionId: string, actionLimit = 80) {
    try {
      const payload = await api.get<SessionRuntimeStatus>(`/sessions/${sessionId}/runtime?action_limit=${actionLimit}`);
      if (sessionId !== activeSessionIdRef.current) return;
      setRuntimeStatus(payload);
    } catch {
      if (sessionId !== activeSessionIdRef.current) return;
      setRuntimeStatus(null);
    }
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
      if (options?.refreshGit ?? rightRailTab === 'runtime') {
        void fetchRuntimeChangedFilesForExplorer(sessionId, payload.path || '', {
          silent,
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
      setRuntimeFiles(null);
      setRuntimePath(path);
      setRuntimeChangedFiles(null);
    } finally {
      if (sessionId === activeSessionIdRef.current && !silent) {
        setRuntimeFilesLoading(false);
      }
    }
  }

  async function fetchRuntimeChangedFilesForExplorer(
    sessionId: string,
    path: string,
    options?: { silent?: boolean },
  ): Promise<SessionRuntimeGitChangedFilesResponse | null> {
    const silent = Boolean(options?.silent);
    if (!silent) {
      setRuntimeChangedFilesLoading(true);
    }
    try {
      const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
        `/sessions/${sessionId}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (sessionId !== activeSessionIdRef.current) return null;
      setRuntimeChangedFiles(payload);
      return payload;
    } catch {
      if (sessionId !== activeSessionIdRef.current) return null;
      setRuntimeChangedFiles(null);
      return null;
    } finally {
      if (sessionId === activeSessionIdRef.current && !silent) {
        setRuntimeChangedFilesLoading(false);
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
    const changed = await fetchRuntimeChangedFilesForExplorer(activeSessionId, path);
    const firstPath = changed?.entries?.[0]?.path;
    if (!firstPath) return;
    await openRuntimeFileDiff(firstPath);
  }

  async function openRuntimeFile(path: string) {
    if (!activeSessionId) return;
    setWorkbenchLoadingPath(path);
    try {
      const payload = await api.get<SessionRuntimeFilePreviewResponse>(
        `/sessions/${activeSessionId}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
      );
      if (activeSessionId !== activeSessionIdRef.current) return;
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
    } catch {
      toast.error('Failed to open runtime file');
    } finally {
      if (activeSessionId === activeSessionIdRef.current) {
        setWorkbenchLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  async function openRuntimeFileDiff(path: string) {
    if (!activeSessionId) return;
    await openRuntimeFile(path);
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
    const ws = new WebSocket(`${WS_BASE_URL}/ws/sessions/${sessionId}/stream`);
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

  function onStreamEvent(sessionId: string, event: WsEvent) {
    // Drop events from stale WS connections (e.g. fired after session reset)
    if (sessionId !== activeSessionIdRef.current) return;

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
        break;
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
        setStreaming((current) => ({ ...current, agentIteration: (event.iteration as number) ?? current.agentIteration, agentMaxIterations: (event.max_iterations as number) ?? current.agentMaxIterations }));
        break;
      case 'text_delta':
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: true, text: current.text + (event.delta ?? '') }));
        break;
      case 'toolcall_start':
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
            if (!current.activeToolCalls.length) return current;
            const nextActive = [...current.activeToolCalls];
            let targetIndex = -1;
            if (eventCallId) {
              targetIndex = nextActive.findIndex((item) => item.id === eventCallId);
            }
            if (targetIndex < 0 && eventContentIndex !== null) {
              targetIndex = nextActive.findIndex((item) => item.contentIndex === eventContentIndex);
            }
            if (targetIndex < 0) targetIndex = nextActive.length - 1;
            const doneCall = nextActive[targetIndex];
            nextActive.splice(targetIndex, 1);
            const callApprovalRef = approvalRefFromMetadata(doneCall.metadata);
            const isPendingApproval = Boolean(callApprovalRef?.pending);
            approvalDebugLog('ws.toolcall_end.classify', {
              session_id: sessionId,
              tool_call_id: doneCall.id,
              tool_name: doneCall.name,
              pending_from_metadata: Boolean(callApprovalRef?.pending),
              pending_final: isPendingApproval,
              metadata_approval_id: callApprovalRef?.approvalId ?? null,
              metadata_provider: callApprovalRef?.provider ?? null,
            });
            const hydratedDoneCall: StreamingToolCall = isPendingApproval
              ? {
                ...doneCall,
                outputJson: doneCall.outputJson || '{"status":"pending","message":"Waiting for approval..."}',
                metadata: {
                  ...doneCall.metadata,
                  pending: true,
                },
              }
              : doneCall;
            const alreadyDone = current.completedToolCalls.some(
              (item) => item.id === hydratedDoneCall.id && item.contentIndex === hydratedDoneCall.contentIndex,
            );
            if (alreadyDone) {
              return { ...current, activeToolCalls: nextActive };
            }
            return {
              ...current,
              activeToolCalls: nextActive,
              completedToolCalls: [...current.completedToolCalls, { ...hydratedDoneCall, complete: true }],
            };
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
          if (toolNameForRefresh === 'sub_agents') {
            void fetchTasks(sessionId);
          }
          if (
            toolNameForRefresh === 'runtime_exec' ||
            toolNameForRefresh === 'python' ||
            toolNameForRefresh === 'git_exec'
          ) {
            void fetchRuntimeStatus(sessionId, 120);
            if (rightRailTab === 'runtime') {
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

          const hydrate = (call: StreamingToolCall): StreamingToolCall => ({
            ...call,
            argumentsJson: hasMeaningfulToolArguments(call.argumentsJson)
              ? call.argumentsJson
              : fallbackArguments,
            outputJson,
            isError,
            metadata,
          });

          let touched = false;
          const nextActive = current.activeToolCalls.map((call) => {
            if (callId && call.id === callId) {
              touched = true;
              return hydrate(call);
            }
            return call;
          });
          const nextCompleted = current.completedToolCalls.map((call) => {
            if (callId && call.id === callId) {
              touched = true;
              return hydrate(call);
            }
            return call;
          });

          if (touched) {
            return {
              ...current,
              activeToolCalls: nextActive,
              completedToolCalls: nextCompleted,
            };
          }

          const syntheticCall: StreamingToolCall = {
            id: callId || `tool-result-${Date.now()}`,
            name: toolName,
            argumentsJson: fallbackArguments,
            outputJson,
            isError,
            metadata,
            complete: true,
            contentIndex: null,
          };
          const syntheticKey = streamingCallKey(syntheticCall);
          const hasTimelineItem = current.timeline.some(
            (item) => item.kind === 'tool' && item.callKey === syntheticKey
          );
          return {
            ...current,
            timeline: hasTimelineItem
              ? current.timeline
              : [...current.timeline, { kind: 'tool', key: `tool-${syntheticKey}`, callKey: syntheticKey }],
            completedToolCalls: [
              ...nextCompleted,
              syntheticCall,
            ],
          };
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
          setRuntimeBooting(false);
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
      case 'error':
      case 'agent_error': {
        const raw = (event.error as string) || (event.message as string) || 'Stream error';
        toast.error(humanizeAgentError(raw), { duration: 8000 });
        // Reset streaming state so UI doesn't stay stuck in "thinking" mode
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
    setIsStopping(true);
    try {
      await api.post(`/sessions/${activeSessionId}/stop`, {});
      toast.success('Stopping response');
      setStreaming((current) => ({
        ...current,
        isThinking: false,
        isStreaming: false,
        isCompactingContext: false,
      }));
      void loadMessages(activeSessionId);
      void fetchContextUsage(activeSessionId);
    } catch {
      toast.error('Failed to stop');
    }
    finally { setIsStopping(false); }
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
              <div className="flex items-center gap-2">
                <button
                    onClick={() => setMode('solo')}
                    className="inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm"
                >
                  <Expand size={14} className="text-emerald-500/80" />
                  Focus
                </button>

                <div className="h-4 w-px bg-[color:var(--border-subtle)] mx-1" />

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
            ) : null
          }
      >
        <div className="relative flex h-full w-full overflow-hidden">
          {mode === 'solo' ? (
            <div className="absolute top-4 left-4 z-40">
              <button
                type="button"
                onClick={() => setMode('advanced')}
                className="inline-flex h-9 items-center gap-2 rounded-full border border-[color:var(--border-strong)] bg-[color:var(--surface-0)]/90 backdrop-blur px-4 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)] hover:bg-[color:var(--surface-1)] transition-all active:scale-95 shadow-xl"
              >
                <X size={14} className="text-[color:var(--text-muted)]" />
                Exit Focus
              </button>
            </div>
          ) : null}
          <aside
            className={`hidden lg:flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shrink-0 transition-all duration-300 ease-in-out ${
              mode === 'advanced' ? 'w-64 opacity-100' : 'w-0 opacity-0 pointer-events-none border-none'
            }`}
          >
            <div className={`flex flex-col h-full min-w-[16rem] transition-opacity duration-200 ${mode === 'advanced' ? 'opacity-100' : 'opacity-0'}`}>
              <SessionHistorySidebar
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
          </aside>

          {/* Chat Area */}
          <main className="relative z-0 flex-1 flex flex-col min-w-0 bg-[color:var(--surface-0)] overflow-hidden">
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
                        <SessionMessageCard
                          key={m.id}
                          message={m}
                          toolArgumentsByCallId={toolArgumentsByCallId}
                          onResolveApproval={resolveApprovalInline}
                          resolvingApprovalKey={resolvingApprovalKey}
                        />
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
                        />
                      ))}

                    {streaming.text && (
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

                    {streaming.isThinking && !streaming.text && streaming.activeToolCalls.length === 0 && (
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

                    {streaming.agentIteration > 0 || streaming.isThinking || streaming.isStreaming || streaming.activeToolCalls.length > 0 ? (
                      <div className="sticky bottom-2 z-20 flex justify-center pointer-events-none">
                        <button
                          type="button"
                          onClick={stopCurrent}
                          disabled={isStopping}
                          className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-rose-500/40 bg-[color:var(--surface-0)]/90 backdrop-blur px-4 h-9 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500 hover:text-white transition-all active:scale-95 shadow-xl disabled:opacity-50"
                        >
                          <Square size={12} fill="currentColor" />
                          {isStopping ? 'Stopping...' : 'Stop Execution'}
                        </button>
                      </div>
                    ) : null}

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
                className={`hidden xl:block w-1 cursor-col-resize hover:bg-[color:var(--accent-solid)] transition-colors ${isWorkbenchResizing ? 'bg-[color:var(--accent-solid)]' : 'bg-transparent'}`}
                onMouseDown={startWorkbenchResizing}
              />
              <aside
                style={{ width: `${workbenchWidth}px` }}
                className="relative z-30 hidden xl:flex flex-col border-l border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden min-w-[360px] animate-[workbenchDockIn_180ms_ease-out]"
              >
                <div className="border-b border-[color:var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Open Files</div>
                    <button
                      type="button"
                      onClick={() => {
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
                      className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-rose-400/50 bg-rose-500/20 text-rose-300 transition-colors hover:bg-rose-500/35 hover:text-rose-100"
                      title="Close all tabs"
                      aria-label="Close all tabs"
                    >
                      <X size={12} />
                    </button>
                  </div>
                  <div className="flex items-center gap-1 overflow-x-auto no-scrollbar">
                    {workbenchTabs.map((tab) => (
                      <button
                        key={tab.path}
                        type="button"
                        onClick={() => setActiveWorkbenchPath(tab.path)}
                        className={`group inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-semibold max-w-[240px] shrink-0 ${
                          activeWorkbenchTab?.path === tab.path
                            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                            : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-secondary)]'
                        }`}
                        title={tab.path}
                      >
                        <span className="truncate">{tab.name}</span>
                        <span
                          role="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            closeWorkbenchTab(tab.path);
                          }}
                          className="inline-flex h-4 w-4 items-center justify-center rounded hover:bg-rose-500/20 hover:text-rose-300"
                          title="Close tab"
                        >
                          <X size={11} />
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                {activeWorkbenchTab ? (
                  <div className="flex-1 min-h-0 flex flex-col">
                    <div className="border-b border-[color:var(--border-subtle)] px-3 py-2 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[11px] font-semibold truncate">{activeWorkbenchTab.name}</div>
                          <div className="text-[9px] text-[color:var(--text-muted)] font-mono truncate" title={activeWorkbenchTab.path}>
                            {activeWorkbenchTab.path}
                          </div>
                        </div>
                        <div className="text-[9px] text-[color:var(--text-muted)]">{formatBytes(activeWorkbenchTab.size_bytes)}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: false }))}
                          className={`rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${
                            !workbenchShowDiffByPath[activeWorkbenchTab.path]
                              ? 'border-sky-500/40 bg-sky-500/15 text-sky-300'
                              : 'border-[color:var(--border-subtle)] text-[color:var(--text-muted)]'
                          }`}
                        >
                          Content
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: true }));
                            if (activeSessionId) {
                              void fetchRuntimeGitDiff(activeSessionId, activeWorkbenchTab.path);
                            }
                          }}
                          className={`rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${
                            workbenchShowDiffByPath[activeWorkbenchTab.path]
                              ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                              : 'border-[color:var(--border-subtle)] text-[color:var(--text-muted)]'
                          }`}
                        >
                          Diff
                        </button>
                        <div className="ml-auto flex items-center gap-1.5">
                          <select
                            value={activeWorkbenchBaseRef}
                            onChange={(event) => {
                              const selectedRef = event.target.value || 'HEAD';
                              setWorkbenchDiffBaseRefByPath((current) => ({
                                ...current,
                                [activeWorkbenchTab.path]: selectedRef,
                              }));
                              if (activeSessionId && workbenchShowDiffByPath[activeWorkbenchTab.path]) {
                                void fetchRuntimeGitDiff(activeSessionId, activeWorkbenchTab.path, {
                                  baseRef: selectedRef,
                                });
                              }
                            }}
                            className="h-7 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 text-[10px] font-mono text-[color:var(--text-secondary)]"
                            title="Diff base reference"
                          >
                            {activeWorkbenchBaseRefOptions.map((ref) => (
                              <option key={`${activeWorkbenchTab.path}:base-ref:${ref}`} value={ref}>
                                {ref}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {activeWorkbenchGitRoots.length > 0 ? (
                        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar">
                          {activeWorkbenchGitRoots.slice(0, 6).map((root) => (
                            <span
                              key={`${activeWorkbenchTab.path}:${root.root_path || '.'}:${root.branch ?? 'detached'}`}
                              className="inline-flex items-center gap-1 rounded-full border border-violet-500/35 bg-violet-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-violet-700 dark:text-violet-300"
                            >
                              <span>{root.root_path || '.'}</span>
                              <span>{root.detached_head ? 'detached' : root.branch || 'unknown'}</span>
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>

                    <div className="flex-1 min-h-0 overflow-auto p-3">
                      <div key={activeWorkbenchViewerKey} className="h-full animate-[workbenchViewerIn_170ms_ease-out]">
                        {workbenchShowDiffByPath[activeWorkbenchTab.path] ? (
                        workbenchDiffLoadingPath === activeWorkbenchTab.path ? (
                          <div className="flex items-center gap-2 text-[11px] text-[color:var(--text-muted)]">
                            <Loader2 size={13} className="animate-spin" />
                            Loading diff…
                          </div>
                        ) : activeWorkbenchDiff ? (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between text-[10px] text-[color:var(--text-muted)]">
                              <span>root: {activeWorkbenchDiff.git_root || '.'}</span>
                              <span>{activeWorkbenchDiff.truncated ? 'truncated' : 'full'}</span>
                            </div>
                            <div className="rounded-lg border border-[color:var(--border-subtle)] p-2">
                              <Markdown
                                content={toMarkdownCodeFence(activeWorkbenchDiff.diff || '[no diff output]', 'diff')}
                                className="!text-[11px] markdown-workbench"
                              />
                            </div>
                          </div>
                        ) : activeWorkbenchDiffError ? (
                          <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-2 text-[10px] text-rose-300">
                            {activeWorkbenchDiffError}
                          </div>
                        ) : (
                          <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">
                            Open Diff to load the comparison automatically.
                          </div>
                        )
                      ) : (
                        <div className="rounded-lg border border-[color:var(--border-subtle)] p-2">
                          <Markdown
                            content={toMarkdownCodeFence(
                              activeWorkbenchTab.content || '[empty file]',
                              inferCodeLanguageFromName(activeWorkbenchTab.name),
                            )}
                            className="!text-[11px] markdown-workbench"
                          />
                        </div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 flex items-center justify-center text-[10px] text-[color:var(--text-muted)]">
                    No file selected.
                  </div>
                )}
              </aside>
            </>
          ) : null}

          {/* Resize Handle */}
          <div
              className={`hidden xl:block w-1 cursor-col-resize hover:bg-[color:var(--accent-solid)] transition-colors ${isResizing ? 'bg-[color:var(--accent-solid)]' : 'bg-transparent'}`}
              onMouseDown={startResizing}
          />

          {/* Right Rail */}
          <aside
              style={{ width: `${rightPanelWidth}px` }}
              className="relative z-30 hidden xl:flex flex-col border-l border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden"
          >
            <div className="border-b border-[color:var(--border-subtle)] p-3 space-y-2">
              <div className="relative grid grid-cols-4 gap-0 rounded-full border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)] overflow-hidden">
                {/* Sliding Indicator */}
                <div
                  className={`absolute top-0.5 bottom-0.5 w-[calc(25%-1px)] rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out ${
                    rightRailTab === 'desktop'
                      ? 'left-0.5'
                      : rightRailTab === 'sub_agents'
                        ? 'left-[calc(25%)]'
                        : rightRailTab === 'runtime'
                          ? 'left-[calc(50%)]'
                          : 'left-[calc(75%-0.5px)]'
                  }`}
                />

                <button
                  type="button"
                  onClick={() => setRightRailTab('desktop')}
                  className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                    rightRailTab === 'desktop'
                      ? 'text-[color:var(--text-primary)]'
                      : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                  }`}
                >
                  Desktop
                </button>
                <button
                  type="button"
                  onClick={() => setRightRailTab('sub_agents')}
                  className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                    rightRailTab === 'sub_agents'
                      ? 'text-[color:var(--text-primary)]'
                      : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                  }`}
                >
                  Agents
                </button>
                <button
                  type="button"
                  onClick={() => setRightRailTab('runtime')}
                  className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                    rightRailTab === 'runtime'
                      ? 'text-[color:var(--text-primary)]'
                      : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                  }`}
                >
                  Runtime
                </button>
                <button
                  type="button"
                  onClick={() => setRightRailTab('modules')}
                  className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                    rightRailTab === 'modules'
                      ? 'text-[color:var(--text-primary)]'
                      : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                  }`}
                >
                  Modules
                </button>
              </div>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                    {rightRailTab === 'desktop'
                      ? 'Live Desktop'
                      : rightRailTab === 'sub_agents'
                        ? 'Sub-Agent Tasks'
                        : rightRailTab === 'runtime'
                          ? 'Workspace Runtime'
                          : 'Module Action Log'}
                  </div>
                </div>
                {rightRailTab === 'sub_agents' ? (
                  <span className="text-[10px] bg-emerald-500/10 text-emerald-600 px-1.5 py-0.5 rounded font-bold">
                    {tasks.filter((task) => task.status === 'running' || task.status === 'pending').length} Active
                  </span>
                ) : rightRailTab === 'runtime' ? (
                  <span className="text-[10px] bg-sky-500/10 text-sky-500 px-1.5 py-0.5 rounded font-bold">
                    {runtimeStatusLabel(runtimeStatus)}
                  </span>
                ) : null}
              </div>
            </div>

            {rightRailTab === 'desktop' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-[color:var(--border-subtle)]">
                  <div className="flex items-center gap-2">
                    <Globe size={15} className="text-sky-500" />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                      Interactive View
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* Reset dropdown */}
                    <div className="relative" ref={resetMenuRef}>
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
                          className="absolute right-0 top-full mt-1 z-50 w-48 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shadow-xl overflow-hidden"
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
                              <div className="font-semibold">Restart Container</div>
                              <div className="text-[9px] text-[color:var(--text-muted)]">Full VM reset</div>
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
                      isBooting={runtimeBooting && !(liveView?.enabled && liveView?.available)}
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
                            ) : runtimeBooting ? (
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

            {rightRailTab === 'runtime' ? (
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex-1 min-h-0 flex flex-col">
                  <div className="border-b border-[color:var(--border-subtle)] px-4 py-2">
                    <div className="relative grid grid-cols-2 gap-0 rounded-full border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)] overflow-hidden">
                      {/* Sliding Indicator */}
                      <div
                        className={`absolute top-0.5 bottom-0.5 w-[calc(50%-1px)] rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out ${
                          runtimeInspectorTab === 'files'
                            ? 'left-0.5'
                            : 'left-[calc(50%)]'
                        }`}
                      />

                      <button
                        type="button"
                        onClick={() => setRuntimeInspectorTab('files')}
                        className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                          runtimeInspectorTab === 'files'
                            ? 'text-[color:var(--text-primary)]'
                            : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                        }`}
                      >
                        Files
                      </button>
                      <button
                        type="button"
                        onClick={() => setRuntimeInspectorTab('commands')}
                        className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                          runtimeInspectorTab === 'commands'
                            ? 'text-[color:var(--text-primary)]'
                            : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                        }`}
                      >
                        Commands
                      </button>
                    </div>
                  </div>

                  <div className="flex-1 min-h-0 overflow-y-auto p-4">
                    {runtimeInspectorTab === 'files' ? (
                      <div className="space-y-2">
                        <div className="mb-1 flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              if (!activeSessionId || !runtimeFiles || runtimeFiles.parent_path === null) return;
                              void openRuntimeDirectory(runtimeFiles.parent_path);
                            }}
                            disabled={!runtimeFiles || runtimeFiles.parent_path === null || runtimeFilesLoading}
                            className="inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-[color:var(--text-muted)] disabled:opacity-40"
                          >
                            <ArrowUp size={11} />
                            Up
                          </button>
                          <div className="min-w-0 flex-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-mono text-[color:var(--text-secondary)] truncate">
                            /workspace{runtimePath ? `/${runtimePath}` : ''}
                          </div>
                        </div>
                        <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Explorer</div>
                        {runtimeChangedFiles?.git_root ? (
                          <div className="rounded-lg border border-violet-500/30 bg-violet-500/10 p-2">
                            <div className="flex items-center justify-between gap-2">
                              <div className="min-w-0 flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-widest text-violet-700 dark:text-violet-200">
                                <GitBranch size={11} />
                                <span className="truncate">
                                  Repo {runtimeChangedFiles.git_root || '.'}
                                </span>
                                <span className="text-violet-600/90 dark:text-violet-300/90">
                                  {runtimeChangedFiles.detached_head
                                    ? 'detached'
                                    : runtimeChangedFiles.branch || 'unknown'}
                                </span>
                              </div>
                              <span className="text-[8px] font-bold uppercase tracking-widest text-violet-600 dark:text-violet-300/80">
                                auto
                              </span>
                            </div>
                            {runtimeChangedFiles.entries.length > 0 ? (
                              <div className="relative mt-2">
                                {runtimeChangedFilesLoading ? (
                                  <div className="pointer-events-none absolute inset-x-0 -top-1 z-10 mx-auto w-fit rounded-full border border-violet-500/35 bg-violet-50 px-2 py-0.5 text-[8px] font-bold uppercase tracking-widest text-violet-700 dark:bg-violet-900/35 dark:text-violet-100">
                                    Updating…
                                  </div>
                                ) : null}
                                <div className={`space-y-1 transition-opacity duration-150 ${runtimeChangedFilesLoading ? 'opacity-85' : 'opacity-100'} ${runtimeChangedFilesLoading ? '' : 'animate-[fade-in_160ms_ease-out]'}`}>
                                  {runtimeChangedFiles.entries.slice(0, 8).map((entry) => (
                                    <button
                                      key={`runtime-inline-change:${entry.path}:${entry.status}`}
                                      type="button"
                                      onClick={() => void openRuntimeFileDiff(entry.path)}
                                      className="w-full rounded-md border border-violet-400/30 bg-violet-50/80 px-2 py-1.5 text-left hover:border-violet-500/50 transition-colors dark:bg-violet-950/30"
                                    >
                                      <div className="flex items-center gap-2 min-w-0">
                                        <span className="w-7 shrink-0 text-[9px] font-bold uppercase text-violet-700 dark:text-violet-200">
                                          {entry.status}
                                        </span>
                                        <span className="truncate text-[10px] font-mono text-violet-700/90 dark:text-violet-100/90">
                                          {entry.path}
                                        </span>
                                        <ChevronRight size={11} className="ml-auto shrink-0 text-violet-500 dark:text-violet-200/70" />
                                      </div>
                                    </button>
                                  ))}
                                  {runtimeChangedFiles.entries.length > 8 ? (
                                    <div className="text-[9px] uppercase tracking-wider text-violet-600 dark:text-violet-200/80">
                                      +{runtimeChangedFiles.entries.length - 8} more changed files
                                    </div>
                                  ) : null}
                                </div>
                              </div>
                            ) : runtimeChangedFilesLoading ? (
                              <div className="mt-2 flex items-center gap-1.5 text-[10px] text-violet-600 dark:text-violet-200/80">
                                <Loader2 size={11} className="animate-spin" />
                                Scanning changes…
                              </div>
                            ) : (
                              <div className="mt-2 text-[10px] text-violet-600 dark:text-violet-100/80">
                                No changed files in this repository.
                              </div>
                            )}
                          </div>
                        ) : null}
                        {runtimeFiles?.entries?.length ? (
                          <div className="space-y-3">
                            <div className="flex items-center justify-between px-1">
                              <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Workspace Explorer</div>
                              {runtimeFilesLoading && <Loader2 size={10} className="animate-spin text-[color:var(--text-muted)]" />}
                            </div>
                            <div className="space-y-1.5">
                              {runtimeFiles.entries.map((entry: SessionRuntimeFileEntry) => (
                                <button
                                  key={`${entry.path}:${entry.kind}`}
                                  type="button"
                                  onClick={() => {
                                    if (entry.kind === 'directory') {
                                      void openRuntimeDirectory(entry.path, {
                                        autoOpenFirstDiff: Boolean(entry.is_git_root),
                                      });
                                    } else {
                                      void openRuntimeFile(entry.path);
                                    }
                                  }}
                                  className="w-full group flex items-center justify-between p-2.5 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)] transition-all active:scale-[0.99]"
                                >
                                  <div className="flex items-center gap-3 min-w-0">
                                    {entry.kind === 'directory' ? (
                                      <Folder size={14} className="text-sky-500 shrink-0" />
                                    ) : (
                                      <FileCode2 size={14} className="text-[color:var(--text-muted)] shrink-0 group-hover:text-[color:var(--text-primary)] transition-colors" />
                                    )}
                                    <div className="flex flex-col items-start min-w-0">
                                      <span className="text-[11px] font-bold text-[color:var(--text-primary)] truncate">{entry.name}</span>
                                      <div className="flex items-center gap-2 mt-0.5">
                                        <span className="text-[9px] font-mono text-[color:var(--text-muted)] uppercase tracking-tight">
                                          {entry.kind === 'directory' ? 'Folder' : formatBytes(entry.size_bytes)}
                                        </span>
                                        {entry.modified_at && (
                                          <>
                                            <div className="w-0.5 h-0.5 rounded-full bg-[color:var(--border-strong)]" />
                                            <span className="text-[9px] font-mono text-[color:var(--text-muted)]">
                                              {formatCompactDate(entry.modified_at)}
                                            </span>
                                          </>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {entry.kind === 'directory' && entry.is_git_root && (
                                      <span className="inline-flex items-center gap-1 rounded-full border border-violet-500/30 bg-violet-500/5 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-violet-500">
                                        <GitBranch size={9} />
                                        {entry.git_detached_head ? 'detached' : entry.git_branch || 'repo'}
                                      </span>
                                    )}
                                    <ChevronRight size={12} className="text-[color:var(--text-muted)] opacity-40 group-hover:opacity-100 transition-opacity" />
                                  </div>
                                </button>
                              ))}
                              {runtimeFiles.truncated && (
                                <div className="p-3 text-center rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/20">
                                  <p className="text-[9px] font-bold uppercase tracking-widest text-amber-500/80">List truncated to 400 entries</p>
                                </div>
                              )}
                            </div>
                          </div>
                        ) : runtimeFilesLoading ? (
                          <div className="py-12 flex flex-col items-center justify-center gap-3 text-[color:var(--text-muted)] animate-pulse">
                            <Loader2 size={20} className="animate-spin" />
                            <p className="text-[10px] font-bold uppercase tracking-widest">Mapping Workspace...</p>
                          </div>
                        ) : (
                          <div className="py-12 flex flex-col items-center justify-center gap-3 text-[color:var(--text-muted)] opacity-40">
                            <div className="p-3 rounded-2xl bg-[color:var(--surface-2)]">
                              <FileCode2 size={20} strokeWidth={1.5} />
                            </div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.1em]">Workspace is empty</p>
                          </div>
                        )}
                      </div>
                    ) : null}

                    {runtimeInspectorTab === 'commands' ? (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between px-1">
                          <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Recent Commands</div>
                          <span className="text-[9px] font-mono text-[color:var(--text-muted)] opacity-60">Last 25</span>
                        </div>
                        {runtimeCommandActions.length > 0 ? (
                          <div className="space-y-2.5">
                            {runtimeCommandActions.slice(0, 25).map((entry) => {
                              const isRunning = entry.state === 'running';
                              const hasOutput = Boolean(
                                entry.output &&
                                  (entry.output.stdout.trim().length > 0 ||
                                    entry.output.stderr.trim().length > 0 ||
                                    entry.output.timedOut ||
                                    entry.output.returncode !== null ||
                                    entry.output.ok !== null),
                              );
                              const isOutputCollapsed = runtimeCommandOutputCollapsed[entry.id] ?? true;

                              return (
                                <div
                                  key={entry.id}
                                  className={`group relative overflow-hidden rounded-xl border transition-all ${
                                    isRunning
                                      ? 'border-emerald-500/30 bg-emerald-500/[0.02] shadow-[0_4px_12px_rgba(16,185,129,0.05)]'
                                      : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)]'
                                  }`}
                                >
                                  <div className="p-3">
                                    <div className="flex items-center justify-between mb-2">
                                      <div className="flex items-center gap-2">
                                        <div className={`h-1.5 w-1.5 rounded-full ${
                                          entry.state === 'running' ? 'bg-emerald-500 animate-pulse' :
                                          entry.state === 'failed' || entry.state === 'cancelled' ? 'bg-rose-500' :
                                          'bg-[color:var(--text-muted)] opacity-40'
                                        }`} />
                                        <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                                          {entry.source === 'detached_job' ? 'DETACHED JOB' : 'SHELL'}
                                        </span>
                                        <div className="h-3 w-px bg-[color:var(--border-subtle)]" />
                                        <span className={`text-[8px] font-bold uppercase tracking-wider ${
                                          entry.state === 'running' ? 'text-emerald-500' :
                                          entry.state === 'failed' || entry.state === 'cancelled' ? 'text-rose-500' :
                                          'text-[color:var(--text-muted)]'
                                        }`}>
                                          {entry.state}
                                        </span>
                                      </div>
                                      <span className="text-[9px] font-mono text-[color:var(--text-muted)] opacity-60">
                                        {formatCompactDate(entry.endedAt || entry.startedAt)}
                                      </span>
                                    </div>

                                    <div className="rounded-lg bg-black/5 dark:bg-black/40 p-2 border border-black/5">
                                      <Markdown
                                        content={toMarkdownCodeFence(entry.command || '[empty command]', 'bash')}
                                        className="!text-[10px] markdown-command-inline"
                                      />
                                    </div>

                                    {hasOutput && (
                                      <div className="mt-2.5">
                                        <button
                                          type="button"
                                          onClick={() => toggleRuntimeCommandOutput(entry.id)}
                                          className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] hover:opacity-80 transition-opacity"
                                        >
                                          {isOutputCollapsed ? 'Show Output' : 'Hide Output'}
                                          <ChevronDown size={10} className={`transition-transform ${isOutputCollapsed ? '' : 'rotate-180'}`} />
                                        </button>

                                        {!isOutputCollapsed && entry.output && (
                                          <div className="mt-2 space-y-2 animate-in slide-in-from-top-1 duration-200">
                                            <div className="flex gap-2">
                                              {entry.output.returncode !== null && (
                                                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">
                                                  EXIT: {entry.output.returncode}
                                                </span>
                                              )}
                                              {entry.output.timedOut && (
                                                <span className="text-[8px] font-bold px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-500 uppercase tracking-widest">
                                                  Timed Out
                                                </span>
                                              )}
                                            </div>

                                            {entry.output.stdout.trim() && (
                                              <div className="space-y-1">
                                                <div className="text-[8px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] px-1">stdout</div>
                                                <pre className="p-2 rounded-lg bg-black/5 dark:bg-black/60 font-mono text-[10px] text-[color:var(--text-secondary)] overflow-auto max-h-48 whitespace-pre-wrap">{entry.output.stdout}</pre>
                                              </div>
                                            )}
                                            {entry.output.stderr.trim() && (
                                              <div className="space-y-1">
                                                <div className="text-[8px] font-bold uppercase tracking-widest text-rose-500/80 px-1">stderr</div>
                                                <pre className="p-2 rounded-lg bg-rose-500/5 dark:bg-rose-500/10 font-mono text-[10px] text-rose-600 dark:text-rose-300 overflow-auto max-h-48 whitespace-pre-wrap">{entry.output.stderr}</pre>
                                              </div>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    )}

                                    {isRunning && (
                                      <div className="mt-3 flex justify-end">
                                        <button
                                          type="button"
                                          onClick={() => void stopCurrent()}
                                          disabled={isStopping}
                                          className="inline-flex items-center gap-1.5 h-7 px-3 rounded-full border border-rose-500/20 bg-rose-500/5 text-rose-500 text-[9px] font-bold uppercase tracking-wider hover:bg-rose-500 hover:text-white transition-all active:scale-95"
                                        >
                                          {isStopping ? 'Stopping...' : 'Stop Execution'}
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div className="py-12 flex flex-col items-center justify-center text-[color:var(--text-muted)] opacity-40 gap-3">
                            <div className="p-3 rounded-2xl bg-[color:var(--surface-2)]">
                              <Terminal size={20} strokeWidth={1.5} />
                            </div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.1em]">No shell history</p>
                          </div>
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="border-t border-[color:var(--border-subtle)] p-4 bg-[color:var(--surface-2)]/20">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-2">Workbench</div>
                  <div className="text-[10px] text-[color:var(--text-muted)] opacity-80">
                    {workbenchVisible
                      ? `${workbenchTabs.length} file tab${workbenchTabs.length === 1 ? '' : 's'} open in the file workbench.`
                      : 'Open a file from the workspace list to create a file tab in the workbench pane.'}
                  </div>
                  {workbenchLoadingPath ? (
                    <div className="mt-2 flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                      <Loader2 size={12} className="animate-spin" />
                      Opening {workbenchLoadingPath}
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {rightRailTab === 'modules' ? (
              <ModuleActionLog
                completedToolCalls={streaming.completedToolCalls}
                activeToolCalls={streaming.activeToolCalls}
                messages={messages}
              />
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
