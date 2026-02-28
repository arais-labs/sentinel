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
} from 'lucide-react';
import { ChangeEvent, ClipboardEvent, FormEvent, useEffect, useMemo, useRef, useState, memo, useCallback, useLayoutEffect } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { SubAgentTaskModal } from '../components/SubAgentTaskModal';
import { SpawnSubAgentModal } from '../components/SpawnSubAgentModal';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Markdown } from '../components/ui/Markdown';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { WS_BASE_URL } from '../lib/env';
import { formatCompactDate, toPrettyJson, truncate } from '../lib/format';
import { api } from '../lib/api';
import { useAuthStore } from '../store/auth-store';
import type {
  Message,
  MessageAttachment,
  MessageListResponse,
  ModelOption,
  ModelsResponse,
  PlaywrightLiveView,
  Session,
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

function tryParseJson(raw: string): unknown | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function humanizeAgentError(raw: string): string {
  const lower = raw.toLowerCase();
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
  if (lower.includes('all providers failed')) {
    return 'All AI providers failed. Please check your API keys and account status in Settings.';
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

function parseTier(value: string | null): ModelOption['tier'] | null {
  if (value === 'fast' || value === 'normal' || value === 'hard') {
    return value;
  }
  return null;
}

function sessionChannelKind(session: Session): 'default' | 'telegram_group' | 'telegram_dm' {
  const title = (session.title ?? '').trim().toLowerCase();
  if (title.startsWith('tg group ·')) return 'telegram_group';
  if (title.startsWith('tg dm ·')) return 'telegram_dm';
  return 'default';
}

interface TelegramGroupTurnContext {
  chatTitle: string;
  userName: string;
}

function parseTelegramGroupResponseLabel(content: string): TelegramGroupTurnContext | null {
  const firstLine = content.split('\n', 1)[0]?.trim() ?? '';
  if (!firstLine.startsWith('TG Group Response')) return null;
  const parts = firstLine.split('·').map((part) => part.trim());
  if (parts.length >= 3) {
    return {
      chatTitle: parts[1] || 'Group',
      userName: parts[2] || 'Unknown',
    };
  }
  return { chatTitle: 'Group', userName: 'Unknown' };
}

function isTelegramGroupAuditMessage(message: Message): boolean {
  if (message.role !== 'assistant') return false;
  const content = message.content ?? '';
  const metadata = message.metadata ?? {};
  const source = typeof metadata.source === 'string' ? metadata.source.toLowerCase() : '';
  const chatType = typeof metadata.telegram_chat_type === 'string'
    ? metadata.telegram_chat_type.toLowerCase()
    : '';
  if (source === 'telegram_audit' && (chatType === 'group' || chatType === 'supergroup')) return true;
  const lower = content.toLowerCase();
  return (
    lower.includes('telegram audit:') && lower.includes('(group)')
  ) || content.includes('TG Group Response');
}

function previewPayloadValue(value: unknown, maxChars = 180): { text: string; truncated: boolean } {
  let raw: string;
  if (typeof value === 'string') {
    raw = value;
  } else if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    raw = String(value);
  } else {
    try {
      raw = JSON.stringify(value);
    } catch {
      raw = String(value);
    }
  }
  const normalized = raw.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxChars) {
    return { text: normalized, truncated: false };
  }
  return { text: `${normalized.slice(0, maxChars)}…`, truncated: true };
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

function extractImageAttachments(metadata: Record<string, unknown> | null | undefined): MessageAttachment[] {
  const raw = metadata?.attachments;
  if (!Array.isArray(raw)) return [];
  const out: MessageAttachment[] = [];
  for (const item of raw) {
    if (!isObjectRecord(item)) continue;
    const mime = typeof item.mime_type === 'string' ? item.mime_type : '';
    const base64 = typeof item.base64 === 'string' ? item.base64 : '';
    if (!mime || !base64) continue;
    const filename = typeof item.filename === 'string' ? item.filename : null;
    const sizeBytes = typeof item.size_bytes === 'number' ? item.size_bytes : undefined;
    out.push({ mime_type: mime, base64, filename, size_bytes: sizeBytes });
  }
  return out;
}

function ToolPayloadView({
  raw,
  emptyLabel,
  showRawJson = true,
}: {
  raw: string;
  emptyLabel: string;
  showRawJson?: boolean;
}) {
  const parsed = useMemo(() => tryParseJson(raw), [raw]);
  if (!raw.trim()) {
    return <p className="text-sky-500/60 italic">{emptyLabel}</p>;
  }

  if (isObjectRecord(parsed)) {
    const entries = Object.entries(parsed);
    return (
      <div className="space-y-2">
        {entries.map(([key, value]) => (
          <div key={key} className="rounded-lg border border-sky-500/10 bg-sky-500/5 p-2">
            <p className="text-[9px] font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400">{key}</p>
            {(() => {
              const preview = previewPayloadValue(value);
              return (
                <>
                  <p className="mt-1 font-mono text-[12px] break-words text-[color:var(--text-primary)]">{preview.text || '""'}</p>
                  {preview.truncated && (
                    <p className="mt-1 text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">Truncated in preview</p>
                  )}
                </>
              );
            })()}
          </div>
        ))}
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
        <div className="rounded-lg border border-sky-500/10 bg-sky-500/5 p-2">
          <p className="font-mono text-[12px] break-words text-[color:var(--text-primary)]">{preview.text || '""'}</p>
          {preview.truncated && (
            <p className="mt-1 text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">Truncated in preview</p>
          )}
        </div>
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

// --- Memoized Components ---

const SessionRow = memo(({
  session,
  isActive,
  onClick,
  canDelete,
  isDeleting,
  onDelete,
  onSetMain,
  multiSelectMode,
  selected,
  onToggleSelect,
}: {
  session: Session;
  isActive: boolean;
  onClick: (id: string) => void;
  canDelete: boolean;
  isDeleting: boolean;
  onDelete: (session: Session) => void;
  onSetMain: (session: Session) => void;
  multiSelectMode: boolean;
  selected: boolean;
  onToggleSelect: (id: string) => void;
}) => (
  <div className="group relative">
    {multiSelectMode && canDelete ? (
      <button
        onClick={() => onToggleSelect(session.id)}
        title={selected ? 'Unselect session' : 'Select session'}
        className={`absolute left-2 top-2 h-6 w-6 rounded-md border flex items-center justify-center transition-colors ${
          selected
            ? 'border-sky-500/40 bg-sky-500/15'
            : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-2)]'
        }`}
      >
        <span className={`h-2.5 w-2.5 rounded-sm ${selected ? 'bg-sky-500' : 'bg-transparent'}`} />
      </button>
    ) : null}
    <button
      onClick={() => {
        if (multiSelectMode) {
          if (canDelete) onToggleSelect(session.id);
          return;
        }
        onClick(session.id);
      }}
      className={`w-full flex flex-col gap-1 p-3 rounded-lg text-left transition-colors duration-150 border ${
        isActive
          ? 'bg-[color:var(--surface-0)] shadow-sm border-[color:var(--border-strong)]'
          : 'hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)] border-transparent'
      } ${multiSelectMode ? 'pl-10 pr-3' : 'pr-10'}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 flex-1 text-xs font-semibold truncate">{session.title || 'Session'}</span>
        <div className="flex shrink-0 items-center gap-1">
          {sessionChannelKind(session) === 'telegram_group' ? (
            <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-sky-400">
              <Users size={8} />
              <span>{'TG\u00A0Group'}</span>
            </span>
          ) : null}
          {sessionChannelKind(session) === 'telegram_dm' ? (
            <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-sky-400">
              <Send size={8} />
              <span>{'TG\u00A0DM'}</span>
            </span>
          ) : null}
          {session.is_main ? (
            <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-emerald-500/35 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-400">
              <BadgeCheck size={8} />
              Main
            </span>
          ) : null}
        </div>
      </div>
      <span className="text-[10px] text-[color:var(--text-muted)]">{formatCompactDate(session.started_at)}</span>
    </button>
    {!multiSelectMode && !session.is_main ? (
      <button
        onClick={() => onSetMain(session)}
        title="Set as main session"
        className="absolute right-10 top-2 h-7 w-7 rounded-md border border-emerald-500/35 text-emerald-400 bg-[color:var(--surface-1)] hover:bg-emerald-500/10 flex items-center justify-center transition-opacity opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto"
      >
        <BadgeCheck size={13} />
      </button>
    ) : null}
    {canDelete && !multiSelectMode ? (
      <button
        onClick={() => onDelete(session)}
        disabled={isDeleting}
        title="Delete session"
        className="absolute right-2 top-2 h-7 w-7 rounded-md border border-rose-500/20 text-rose-500 bg-[color:var(--surface-1)] hover:bg-rose-500/10 flex items-center justify-center transition-opacity opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto"
      >
        {isDeleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
      </button>
    ) : null}
  </div>
));
SessionRow.displayName = 'SessionRow';

const SourceChip = memo(({ metadata }: { metadata: Record<string, unknown> }) => {
  const source = metadata?.source as string | undefined;
  if (!source) return null;

  if (source === 'telegram') {
    const chatType = metadata.telegram_chat_type as string | undefined;
    const userName = metadata.telegram_user_name as string | undefined;
    const chatTitle = metadata.telegram_chat_title as string | undefined;
    const label = chatType === 'private'
      ? `TG DM${userName ? ` · ${userName}` : ''}`
      : `TG Group${chatTitle ? ` · ${chatTitle}` : ''}${userName ? ` · ${userName}` : ''}`;
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-400 text-[9px] font-bold uppercase tracking-wide">
        <Send size={9} />
        {label}
      </span>
    );
  }

  if (source === 'web') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[9px] font-bold uppercase tracking-wide">
        <Globe size={9} />
        Web
      </span>
    );
  }

  return null;
});
SourceChip.displayName = 'SourceChip';

const MessageCard = memo(({
  message,
  toolArgumentsByCallId,
}: {
  message: Message;
  toolArgumentsByCallId: Map<string, string>;
}) => {
  const isUser = message.role === 'user';
  const isToolResult = message.role === 'tool_result';
  const isTelegramGroupResponse = !isUser && !isToolResult && isTelegramGroupAuditMessage(message);
  const telegramGroupLabel = parseTelegramGroupResponseLabel(message.content ?? '');
  const renderedAssistantContent = isTelegramGroupResponse
    ? (message.content ?? '').replace(/^TG Group Response[^\n]*\n?/i, '').trimStart()
    : message.content;
  const userAttachments = isUser ? extractImageAttachments(message.metadata) : [];
  const attachments = (message.metadata?.attachments as Array<{ base64: string }> | undefined) ?? [];
  const screenshotBase64 = isToolResult ? (attachments.find(a => a.base64)?.base64 ?? null) : null;
  const isScreenshotTool =
    isToolResult &&
    (Boolean(screenshotBase64) || String(message.tool_name ?? '').toLowerCase().includes('screenshot'));
  const toolInputRaw =
    isToolResult && message.tool_call_id
      ? (toolArgumentsByCallId.get(message.tool_call_id) ?? '')
      : '';
  const toolFailed = Boolean(isToolResult && message.metadata?.is_error);
  const [toolExpanded, setToolExpanded] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

  function openLightbox() { setLightboxOpen(true); setZoom(1); setPan({ x: 0, y: 0 }); }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    setZoom(z => Math.min(10, Math.max(0.5, z * (e.deltaY < 0 ? 1.1 : 0.9))));
  }

  function onMouseDown(e: React.MouseEvent) {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragRef.current) return;
    setPan({ x: dragRef.current.panX + e.clientX - dragRef.current.startX, y: dragRef.current.panY + e.clientY - dragRef.current.startY });
  }

  function onMouseUp() { dragRef.current = null; }

  useEffect(() => {
    if (isScreenshotTool) {
      setToolExpanded(true);
    }
  }, [isScreenshotTool]);

  return (
      <div className={`flex w-full flex-col gap-1.5 animate-in ${isUser ? 'items-end' : 'items-start'}`}>
        <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">
          {message.role}
        </span>
          {isUser && <SourceChip metadata={message.metadata} />}
          <span className="text-[10px] text-[color:var(--text-muted)] opacity-60">
          {formatCompactDate(message.created_at)}
        </span>
        </div>

      <div
        className={`${isToolResult ? (toolExpanded ? 'w-full max-w-[90%]' : 'w-fit') : 'max-w-[90%]'} ${isToolResult ? 'inline-flex flex-col' : ''} rounded-2xl px-4 py-1.5 text-xs shadow-sm border ${
          isUser
            ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent rounded-tr-none font-medium'
            : isToolResult
            ? 'bg-sky-500/5 border-sky-500/20 font-mono text-[12px] rounded-tl-none'
            : isTelegramGroupResponse
            ? 'bg-emerald-500/8 border-emerald-500/25 rounded-tl-none font-medium'
            : 'bg-[color:var(--surface-1)] border-[color:var(--border-subtle)] rounded-tl-none font-medium'
        }`}
      >
          {isToolResult ? (
            <>
              <button
                type="button"
                onClick={() => setToolExpanded((prev) => !prev)}
                className={`${toolExpanded ? 'w-full' : 'w-auto'} flex items-center justify-between gap-3 text-left`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Wrench size={12} className="text-sky-600 dark:text-sky-400 shrink-0" />
                  <span className="font-bold uppercase tracking-wide text-sky-600 dark:text-sky-400 truncate">
                    {message.tool_name || 'tool_result'}
                  </span>
                </div>
                <ChevronDown size={14} className={`text-sky-600 dark:text-sky-400 shrink-0 transition-transform ${toolExpanded ? 'rotate-180' : ''}`} />
              </button>
              {toolExpanded ? (
                <div className={`mt-3 border-t border-sky-500/10 pt-3 grid ${isScreenshotTool ? 'grid-cols-1' : 'grid-cols-2'} gap-3 animate-in fade-in duration-200`}>
                  {!isScreenshotTool ? (
                    <div className="min-w-0">
                      <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Input</p>
                      <ToolPayloadView raw={toolInputRaw} emptyLabel="No input payload." />
                    </div>
                  ) : null}
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Output</p>
                      {toolFailed && (
                        <span className="inline-flex items-center rounded-full border border-rose-500/30 bg-rose-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-rose-500">
                          Error
                        </span>
                      )}
                    </div>
                    {screenshotBase64 ? (
                      <>
                        <img
                          src={`data:image/png;base64,${screenshotBase64}`}
                          alt="Browser screenshot"
                          onClick={openLightbox}
                          className="rounded-lg max-w-full border border-sky-500/20 mt-1 cursor-zoom-in hover:opacity-90 transition-opacity"
                          style={{ maxHeight: '400px', objectFit: 'contain' }}
                        />
                        {lightboxOpen && (
                          <div
                            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm animate-in fade-in duration-150"
                            onClick={() => setLightboxOpen(false)}
                            onMouseMove={onMouseMove}
                            onMouseUp={onMouseUp}
                          >
                            <div
                              className="relative overflow-hidden"
                              style={{ width: '90vw', height: '90vh' }}
                              onClick={e => e.stopPropagation()}
                              onWheel={onWheel}
                              onMouseDown={onMouseDown}
                            >
                              <img
                                src={`data:image/png;base64,${screenshotBase64}`}
                                alt="Browser screenshot"
                                className="absolute rounded-xl shadow-2xl border border-white/10 select-none"
                                style={{
                                  maxWidth: 'none',
                                  transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`,
                                  top: '50%', left: '50%',
                                  cursor: zoom > 1 ? 'grab' : 'zoom-in',
                                  transformOrigin: 'center',
                                }}
                                draggable={false}
                              />
                              <button
                                onClick={() => setLightboxOpen(false)}
                                className="absolute top-3 right-3 p-1.5 rounded-full bg-black/60 text-white hover:bg-black/80 transition-colors z-10"
                              >
                                <X size={16} />
                              </button>
                              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-black/50 text-white text-[10px] font-mono">
                                {Math.round(zoom * 100)}% · scroll to zoom · drag to pan
                              </div>
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <ToolPayloadView
                        raw={message.content}
                        emptyLabel="No output payload."
                        showRawJson={!isScreenshotTool}
                      />
                    )}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="space-y-2">
              {isTelegramGroupResponse ? (
                <div className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-emerald-400">
                  <Users size={10} />
                  <span className="truncate">
                    {telegramGroupLabel
                      ? `TG Group Response · ${telegramGroupLabel.chatTitle} · ${telegramGroupLabel.userName}`
                      : 'TG Group Response'}
                  </span>
                </div>
              ) : null}
              <Markdown content={renderedAssistantContent} invert={isUser} />
              {userAttachments.length > 0 ? (
                <div className="grid grid-cols-2 gap-2">
                  {userAttachments.map((item, index) => (
                    <img
                      key={`${item.base64.slice(0, 24)}-${index}`}
                      src={`data:${item.mime_type};base64,${item.base64}`}
                      alt={item.filename || `attachment-${index + 1}`}
                      className="rounded-lg border border-white/10 bg-black/10 object-cover max-h-[180px]"
                    />
                  ))}
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
  );
});

MessageCard.displayName = 'MessageCard';

const BrowserPreview = memo(({
                               url,
                               isFullscreen,
                               onClose
                             }: {
  url: string | null;
  isFullscreen: boolean;
  onClose: () => void
}) => {
  // Sanitize URL to prevent accidental character injection and FORCE 127.0.0.1
  const cleanUrl = useMemo(() => {
    if (!url) return null;
    const normalized = url
        .replace(/["']/g, '')
        .replace('localhost', '127.0.0.1')
        .trim();
    try {
      const parsed = new URL(normalized);
      // Force fit-to-container behavior in embedded noVNC.
      parsed.searchParams.set('resize', 'scale');
      parsed.searchParams.set('autoconnect', '1');
      parsed.searchParams.set('view_only', '0');
      return parsed.toString();
    } catch {
      return normalized;
    }
  }, [url]);

  if (!cleanUrl) {
    return (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-[color:var(--text-muted)] gap-3 bg-zinc-900 rounded-xl border border-[color:var(--border-strong)]">
          <Globe size={32} strokeWidth={1} />
          <p className="text-[10px] font-bold uppercase tracking-widest">No Active Browser</p>
        </div>
    );
  }

  return (
      <>
        <div
            className={`fixed inset-0 z-[90] bg-black/80 backdrop-blur-md transition-opacity duration-500 ${isFullscreen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
            onClick={onClose}
        />

        <div
            onMouseDown={(e) => e.stopPropagation()}
            className={`transition-all duration-500 ease-in-out bg-black shadow-2xl overflow-hidden ${
                isFullscreen
                    ? 'fixed inset-4 md:inset-12 z-[100] rounded-2xl border border-white/10'
                    : 'absolute inset-0 rounded-none'
            }`}
        >
          {isFullscreen && (
              <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 bg-zinc-900">
                <div className="flex items-center gap-3">
                  <Globe size={16} className="text-sky-400" />
                  <div className="flex flex-col">
                    <span className="font-bold text-[10px] tracking-widest text-white uppercase">Live Browser Session</span>
                    <span className="text-[9px] text-sky-400/60 font-mono leading-none mt-1 uppercase text-emerald-400">interactive mode</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                      onClick={() => window.open(cleanUrl, '_blank')}
                      className="p-1.5 rounded-lg hover:bg-white/10 text-white/60 hover:text-white transition-colors"
                      title="Open in new tab"
                  >
                    <ExternalLink size={16} />
                  </button>
                  <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 text-white/60 hover:text-white transition-colors">
                    <X size={18} />
                  </button>
                </div>
              </div>
          )}
          <div className={isFullscreen ? 'h-[calc(100%-53px)] w-full' : 'h-full w-full'}>
            <iframe
                src={cleanUrl}
                className="w-full h-full border-none pointer-events-auto bg-black"
                allow="fullscreen; clipboard-read; clipboard-write"
                title="sentinel-browser"
            />
          </div>
        </div>
      </>
  );
});

BrowserPreview.displayName = 'BrowserPreview';

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

function StreamToolCard({ call, active }: { call: StreamingToolCall; active: boolean }) {
  const [expanded, setExpanded] = useState(active);
  const isScreenshotCall = call.name.toLowerCase().includes('screenshot');

  useEffect(() => {
    if (active) setExpanded(true);
  }, [active]);

  useEffect(() => {
    if (call.name.toLowerCase().includes('screenshot')) {
      setExpanded(true);
    }
  }, [call.name]);

  return (
      <div className="flex flex-col gap-1.5 animate-in items-start w-full">
        <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
          tool_call • {active ? 'running' : 'complete'}
        </span>
        </div>
        <div className={`${expanded ? 'w-full max-w-[90%]' : 'w-fit'} inline-flex flex-col rounded-2xl rounded-tl-none border border-sky-500/20 bg-sky-500/5 px-4 py-1.5 text-[12px] shadow-sm`}>
          <button
            type="button"
            onClick={() => setExpanded((prev) => !prev)}
            className={`${expanded ? 'w-full' : 'w-auto'} flex items-center justify-between gap-3 text-left`}
          >
            <div className="flex items-center gap-2 font-mono font-bold text-sky-600 dark:text-sky-400 min-w-0">
              <Wrench size={14} className="shrink-0" />
              <span className="truncate">{call.name}</span>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {active && <Loader2 size={12} className="animate-spin text-sky-500" />}
              <ChevronDown size={14} className={`text-sky-600 dark:text-sky-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            </div>
          </button>
          {expanded ? (
            <div className={`mt-3 border-t border-sky-500/10 pt-3 grid ${isScreenshotCall ? 'grid-cols-1' : 'grid-cols-2'} gap-3 animate-in fade-in duration-200`}>
              {!isScreenshotCall ? (
                <div className="min-w-0">
                  <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Input</p>
                  <ToolPayloadView raw={call.argumentsJson} emptyLabel="No input payload." />
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
                <ToolPayloadView
                  raw={call.outputJson}
                  emptyLabel={active ? 'Running tool...' : 'No output payload.'}
                  showRawJson={!isScreenshotCall}
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>
  );
}

// --- Main Page Component ---

export function SessionsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id: routeSessionId } = useParams<{ id: string }>();
  const auth = useAuthStore();

  const [sessions, setSessions] = useState<Session[]>([]);
  const [defaultSessionId, setDefaultSessionId] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [settingMainSessionId, setSettingMainSessionId] = useState<string | null>(null);
  const [isMultiSelectMode, setIsMultiSelectMode] = useState(false);
  const [selectedSessionIds, setSelectedSessionIds] = useState<string[]>([]);
  const [historyTab, setHistoryTab] = useState<'sessions' | 'sub_agents'>('sessions');
  const [sessionFilter, setSessionFilter] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(routeSessionId ?? null);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedTier, setSelectedTier] = useState(
    () => parseTier(localStorage.getItem('sentinel-selected-tier')) ?? 'normal',
  );
  const [isEffortDropdownOpen, setIsEffortDropdownOpen] = useState(false);
  const [maxIterations, setMaxIterations] = useState(50);

  const [messages, setMessages] = useState<Message[]>([]);
  const [contextTokenBudget, setContextTokenBudget] = useState<number | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);
  const [composer, setComposer] = useState('');
  const [composerAttachments, setComposerAttachments] = useState<MessageAttachment[]>([]);

  const [streaming, setStreaming] = useState<StreamingState>(defaultStreamingState);

  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [spawnObjective, setSpawnObjective] = useState('');
  const [spawnScope, setSpawnScope] = useState('');
  const [spawnMaxSteps, setSpawnMaxSteps] = useState(5);
  const [isSpawning, setIsSpawning] = useState(false);

  const [selectedTask, setSelectedTask] = useState<SubAgentTask | null>(null);
  const [isTaskModalOpen, setIsTaskModalOpen] = useState(false);
  const [isSpawnModalOpen, setIsSpawnModalOpen] = useState(false);
  const [isTerminatingTask, setIsTerminatingTask] = useState(false);
  const [confirmTerminateTaskId, setConfirmTerminateTaskId] = useState<string | null>(null);

  const [liveView, setLiveView] = useState<PlaywrightLiveView | null>(null);

  const [mode, setMode] = useState<'solo' | 'advanced'>(
      () => (localStorage.getItem('sentinel-mode') as 'solo' | 'advanced') ?? 'solo',
  );
  const hasActiveSubAgentTasks = tasks.some((task) => task.status === 'running' || task.status === 'pending');

  useEffect(() => {
    localStorage.setItem('sentinel-mode', mode);
  }, [mode]);

  useEffect(() => {
    localStorage.setItem('sentinel-selected-tier', selectedTier);
  }, [selectedTier]);

  const [isCompacting, setIsCompacting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isResettingBrowser, setIsResettingBrowser] = useState(false);
  const [isBrowserFullscreen, setIsBrowserFullscreen] = useState(false);
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
  const fullscreenFrameRef = useRef<HTMLIFrameElement | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const activeSessionIdRef = useRef<string | null>(routeSessionId ?? null);
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

  const toolArgumentsByCallId = useMemo(() => {
    const map = new Map<string, string>();
    for (const message of messages) {
      if (message.role !== 'assistant') continue;
      const toolCalls = (message.metadata?.tool_calls as unknown[] | undefined) ?? [];
      for (const item of toolCalls) {
        if (!isObjectRecord(item)) continue;
        const id = typeof item.id === 'string' ? item.id : '';
        if (!id) continue;
        map.set(id, serializeToolArguments(item.arguments));
      }
    }
    return map;
  }, [messages]);

  const detectBottom = useCallback((el: HTMLDivElement) => {
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distance <= 20;
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
    if (autoScrollRafRef.current !== null) {
      window.cancelAnimationFrame(autoScrollRafRef.current);
      autoScrollRafRef.current = null;
    }
    autoScrollRafRef.current = window.requestAnimationFrame(() => {
      stickToBottomNow();
      autoScrollRafRef.current = null;
    });
    if (autoScrollTimerShortRef.current !== null) {
      window.clearTimeout(autoScrollTimerShortRef.current);
      autoScrollTimerShortRef.current = null;
    }
    if (autoScrollTimerLongRef.current !== null) {
      window.clearTimeout(autoScrollTimerLongRef.current);
      autoScrollTimerLongRef.current = null;
    }
    // Tool cards expand with transitions; run delayed pins to land at true bottom.
    autoScrollTimerShortRef.current = window.setTimeout(() => {
      stickToBottomNow();
      autoScrollTimerShortRef.current = null;
    }, 140);
    autoScrollTimerLongRef.current = window.setTimeout(() => {
      stickToBottomNow();
      autoScrollTimerLongRef.current = null;
    }, 420);
  }, [stickToBottomNow]);

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
      shouldAutoScrollRef.current = false;
      setIsPinnedToBottom(false);
    } else {
      // Keep current state for non-user drift (e.g. streaming content growth).
      setIsPinnedToBottom(shouldAutoScrollRef.current);
    }
    if (!atBottom && el.scrollTop <= 120 && hasMoreMessages && !loadingOlderRef.current) {
      void loadOlderMessages();
    }
  }, [detectBottom, hasMoreMessages, activeSessionId, messages.length]);

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

  const onSessionClick = useCallback((id: string) => {
    setActiveSessionId(id);
    navigate(`/sessions/${id}`);
  }, [navigate]);

  const startResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
  }, []);

  const resize = useCallback((e: MouseEvent) => {
    if (isResizing) {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth > 300 && newWidth < 800) {
        setRightPanelWidth(newWidth);
      }
    }
  }, [isResizing]);

  useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', resize);
      window.addEventListener('mouseup', stopResizing);
    }
    return () => {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isResizing, resize, stopResizing]);

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
    void fetchSessions();
    void fetchModels();
    void fetchLiveView();
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
      setTasks([]);
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
    void fetchTasks(activeSessionId);
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
      if (autoScrollRafRef.current !== null) {
        window.cancelAnimationFrame(autoScrollRafRef.current);
      }
      if (autoScrollTimerShortRef.current !== null) {
        window.clearTimeout(autoScrollTimerShortRef.current);
      }
      if (autoScrollTimerLongRef.current !== null) {
        window.clearTimeout(autoScrollTimerLongRef.current);
      }
    };
  }, []);

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
  async function fetchSessions() {
    try {
      const [payload, defaultSession] = await Promise.all([
        api.get<SessionListResponse>('/sessions/?limit=100&offset=0&include_sub_agents=true'),
        api.get<Session>('/sessions/default'),
      ]);
      setDefaultSessionId(defaultSession.id);
      const payloadItems = Array.isArray(payload?.items) ? payload.items : [];
      setSessions((current) => {
        const exists = payloadItems.find((s) => s.id === defaultSession.id);
        const merged = exists ? payloadItems : [defaultSession, ...payloadItems];
        return merged.map((item) => ({ ...item, is_main: item.id === defaultSession.id }));
      });
      if (!activeSessionId) {
        setActiveSessionId(defaultSession.id);
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
      const payload = await api.get<ModelsResponse>('/models/');
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

  async function fetchLiveView() {
    try {
      const payload = await api.get<PlaywrightLiveView>('/playwright/live-view');
      setLiveView(payload);
    } catch {
      setLiveView(null);
    }
  }

  async function resetBrowser() {
    if (isResettingBrowser) return;
    setIsResettingBrowser(true);
    try {
      await api.post('/playwright/reset-browser', {});
      toast.success('Browser runtime reset successful');
      await fetchLiveView();
    } catch {
      toast.error('Failed to reset browser runtime');
    } finally {
      setIsResettingBrowser(false);
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
      navigate(`/sessions/${fresh.id}`, { replace: true });
      toast.success('New session started. Memories preserved.');
    } catch {
      toast.error('Failed to reset session');
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
        setStreaming((prev) => ({
          ...prev,
          text: '',
          timeline: [],
          interimTextSeq: 0,
          activeToolCalls: [],
          completedToolCalls: [],
          isThinking: false,
          isStreaming: false,
        }));
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

    const token = await auth.getValidAccessToken();
    if (!token) return;

    setStreaming((current) => ({
      ...current,
      connection: reconnectAttemptsRef.current > 0 ? 'reconnecting' : 'connecting',
    }));

    const instanceId = ++wsInstanceRef.current;
    const ws = new WebSocket(`${WS_BASE_URL}/ws/sessions/${sessionId}/stream?token=${encodeURIComponent(token)}`);
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
        setStreaming((current) => {
          const callId = String((event.tool_call as any)?.id ?? `tool-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
          const contentIndex = typeof event.content_index === 'number' ? event.content_index : null;
          if (current.activeToolCalls.some((item) => item.id === callId && item.contentIndex === contentIndex)) {
            return { ...current, isThinking: false };
          }
          const initialArguments = serializeToolArguments((event.tool_call as any)?.arguments);
          const call = {
            id: callId,
            name: String((event.tool_call as any)?.name ?? 'unknown'),
            argumentsJson: initialArguments,
            outputJson: '',
            isError: false,
            metadata: {},
            complete: false,
            contentIndex,
          };
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
        break;
      case 'toolcall_delta':
        setStreaming((current) => {
          const delta = event.delta ?? '';
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
          next[targetIndex] = { ...call, argumentsJson: `${call.argumentsJson}${delta}` };
          return { ...current, activeToolCalls: next };
        });
        break;
      case 'toolcall_end':
        setStreaming((current) => {
          if (!current.activeToolCalls.length) return current;
          const nextActive = [...current.activeToolCalls];
          const callId = String((event.tool_call as any)?.id ?? '');
          const contentIndex = typeof event.content_index === 'number' ? event.content_index : null;
          let targetIndex = -1;
          if (callId) {
            targetIndex = nextActive.findIndex((item) => item.id === callId);
          }
          if (targetIndex < 0 && contentIndex !== null) {
            targetIndex = nextActive.findIndex((item) => item.contentIndex === contentIndex);
          }
          if (targetIndex < 0) targetIndex = nextActive.length - 1;
          const doneCall = nextActive[targetIndex];
          nextActive.splice(targetIndex, 1);
          const alreadyDone = current.completedToolCalls.some((item) => item.id === doneCall.id && item.contentIndex === doneCall.contentIndex);
          if (alreadyDone) {
            return { ...current, activeToolCalls: nextActive };
          }
          return {
            ...current,
            activeToolCalls: nextActive,
            completedToolCalls: [...current.completedToolCalls, { ...doneCall, complete: true }],
          };
        });
        break;
      case 'tool_result':
        {
          const payload = (event.tool_result as Record<string, unknown> | undefined) ?? {};
          const toolNameForRefresh = String(payload.tool_name ?? (event.tool_call as any)?.name ?? '').trim();
          if (
            toolNameForRefresh === 'spawn_sub_agent' ||
            toolNameForRefresh === 'cancel_sub_agent' ||
            toolNameForRefresh === 'pythonXagent'
          ) {
            void fetchTasks(sessionId);
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
          const isError = Boolean(payload.is_error);
          const metadata = isObjectRecord(payload.metadata) ? payload.metadata : {};

          const hydrate = (call: StreamingToolCall): StreamingToolCall => ({
            ...call,
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
            argumentsJson: '',
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
        if (raw > 0) {
          toast.success(`Context compacted ${raw} -> ${compressed} tokens`);
          void loadMessages(sessionId);
        }
        break;
      }
      case 'compaction_failed':
        setStreaming((current) => ({ ...current, isCompactingContext: false }));
        toast.error((event.error as string) || 'Auto-compaction failed');
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
          void loadMessages(sessionId);
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
    } catch { toast.error('Failed to stop'); }
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
        // Re-fetch messages so the UI reflects deleted old messages
        setMessages([]);
        void loadMessages(activeSessionId);
      }
    } catch { toast.error('Compaction failed'); }
    finally { setIsCompacting(false); }
  }

  return (
      <AppShell
          title={activeSession?.title || 'Untitled Session'}
          subtitle={activeSession ? `ID: ${activeSession.id.slice(0, 8)}` : 'Operator Workspace'}
          contentClassName="h-full !p-0 overflow-hidden"
          actions={
            <div className="flex items-center gap-2">
                        {/* Mode toggle */}
                        <div className="flex items-center rounded-lg bg-[color:var(--surface-2)] p-0.5">
                          {[
                            { id: 'solo', label: 'Focus' },
                            { id: 'advanced', label: 'History' }
                          ].map((m) => (
                            <button
                              key={m.id}
                              onClick={() => setMode(m.id as any)}
                              className={`px-3 h-7 text-xs font-bold uppercase tracking-widest rounded-md transition-all duration-200 ${
                                mode === m.id
                                  ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] shadow-sm'
                                  : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                              }`}
                            >
                              {m.label}
                            </button>
                          ))}
                        </div>
                            <div className="h-6 w-px bg-[color:var(--border-subtle)] mx-1" />

              <button
                  onClick={resetSession}
                  title="Start fresh (memories preserved)"
                  className="btn-secondary h-9 px-3 gap-2 text-xs"
              >
                <RefreshCw size={14} />
                New Chat
              </button>

              {mode === 'advanced' && (
                  <button
                      onClick={compactContext}
                      disabled={isCompacting}
                      className="btn-secondary h-9 px-3 gap-2 text-xs"
                  >
                    <Wand2 size={14} className={isCompacting ? 'animate-spin' : ''} />
                    Compact
                  </button>
              )}

            </div>
          }
      >
        <div className="flex h-full w-full overflow-hidden">
          {/* Session History Sidebar — advanced mode only */}
          <aside 
            className={`hidden lg:flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shrink-0 transition-all duration-300 ease-in-out ${
              mode === 'advanced' ? 'w-64 opacity-100' : 'w-0 opacity-0 pointer-events-none border-none'
            }`}
          >
            <div className={`flex flex-col h-full min-w-[16rem] transition-opacity duration-200 ${mode === 'advanced' ? 'opacity-100' : 'opacity-0'}`}>
              <div className="p-3 border-b border-[color:var(--border-subtle)] space-y-2">
                <h2 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] px-1">History</h2>
                <div className="grid grid-cols-2 gap-1 rounded-md border border-[color:var(--border-subtle)] p-1">
                  <button
                    onClick={() => setHistoryTab('sessions')}
                    className={`h-7 rounded text-[10px] font-bold uppercase tracking-wider transition-colors ${
                      historyTab === 'sessions'
                        ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)]'
                        : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                    }`}
                  >
                    Sessions
                  </button>
                  <button
                    onClick={() => setHistoryTab('sub_agents')}
                    className={`h-7 rounded text-[10px] font-bold uppercase tracking-wider transition-colors ${
                      historyTab === 'sub_agents'
                        ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)]'
                        : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                    }`}
                  >
                    Sub-agents
                  </button>
                </div>
                <div className="relative">
                  <History size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                  <input
                      className="input-field pl-8 h-8 text-xs"
                      placeholder="Search..."
                      value={sessionFilter}
                      onChange={(e) => setSessionFilter(e.target.value)}
                  />
                </div>
                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() => {
                      setIsMultiSelectMode((current) => {
                        const next = !current;
                        if (!next) setSelectedSessionIds([]);
                        return next;
                      });
                    }}
                    className="rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)]"
                  >
                    {isMultiSelectMode ? 'Done' : 'Select'}
                  </button>
                  {isMultiSelectMode ? (
                    <button
                      onClick={() =>
                        setSelectedSessionIds((current) => {
                          const set = new Set(current);
                          if (allVisibleSelected) {
                            selectableVisibleSessionIds.forEach((id) => set.delete(id));
                          } else {
                            selectableVisibleSessionIds.forEach((id) => set.add(id));
                          }
                          return Array.from(set);
                        })
                      }
                      className="rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)]"
                    >
                      {allVisibleSelected ? 'Unselect All' : 'Select All'}
                    </button>
                  ) : null}
                </div>
                {isMultiSelectMode ? (
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">
                      {selectedSessionIds.length} selected
                    </p>
                    <button
                      onClick={() => void deleteSelectedSessions()}
                      disabled={selectedSessionIds.length === 0 || deletingSessionId !== null}
                      className="rounded-md border border-rose-500/30 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-rose-500 hover:bg-rose-500/10 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Delete Selected
                    </button>
                  </div>
                ) : null}
              </div>
              <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
                {filteredSessions.map((s) => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      isActive={s.id === activeSessionId}
                      onClick={onSessionClick}
                      canDelete={Boolean(defaultSessionId) && s.id !== defaultSessionId}
                      isDeleting={
                        deletingSessionId === s.id ||
                        deletingSessionId === 'bulk' ||
                        settingMainSessionId === s.id
                      }
                      onDelete={deleteSession}
                      onSetMain={setMainSession}
                      multiSelectMode={isMultiSelectMode}
                      selected={selectedSessionIdSet.has(s.id)}
                      onToggleSelect={(id) =>
                        setSelectedSessionIds((current) =>
                          current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
                        )
                      }
                    />
                ))}
                {filteredSessions.length === 0 && (
                    <div className="py-8 text-center text-[10px] text-[color:var(--text-muted)] uppercase tracking-widest">
                      No sessions
                    </div>
                )}
              </div>
            </div>
          </aside>

          {/* Chat Area */}
          <main className="flex-1 flex flex-col min-w-0 bg-[color:var(--surface-0)]">
            {/* Model Selector / Status Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
              <div className="flex items-center gap-3">
                <div className="relative flex items-center gap-1.5 group cursor-default">
                  <div className={`h-2 w-2 rounded-full ${streaming.connection === 'connected' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`} />
                  <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">{streaming.connection}</span>
                  <div className="absolute top-full left-0 mt-2 px-3 py-2 rounded-lg bg-[color:var(--surface-0)] border border-[color:var(--border)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-xl z-50 space-y-0.5">
                    <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)]">WebSocket</div>
                    <div className={streaming.connection === 'connected' ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'}>{streaming.connection}</div>
                  </div>
                </div>
                {streaming.agentMaxIterations > 0 && (
                  <>
                    <div className="w-px h-3 bg-[color:var(--border)]" />
                    <div className="relative flex items-center gap-2 group cursor-default">
                      <div className="w-20 h-1 rounded-full bg-[color:var(--surface-2)] overflow-hidden">
                        <div
                          className="h-full rounded-full bg-[color:var(--accent-solid)] transition-all duration-500"
                          style={{ width: `${Math.min((streaming.agentIteration / streaming.agentMaxIterations) * 100, 100)}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono font-bold text-[color:var(--text-muted)]">
                        {streaming.agentIteration}/{streaming.agentMaxIterations}
                      </span>
                      <div className="absolute top-full left-0 mt-2 px-3 py-2 rounded-lg bg-[color:var(--surface-0)] border border-[color:var(--border)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-xl z-50 space-y-0.5">
                        <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Agent Steps</div>
                        <div><span className="font-bold text-[color:var(--accent-solid)]">{streaming.agentIteration}</span><span className="text-[color:var(--text-muted)]"> / {streaming.agentMaxIterations} steps used</span></div>
                        <div className="text-[color:var(--text-muted)]">{streaming.agentMaxIterations - streaming.agentIteration} remaining</div>
                      </div>
                    </div>
                  </>
                )}
                {streaming.isCompactingContext && (
                  <>
                    <div className="w-px h-3 bg-[color:var(--border)]" />
                    <div className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-amber-500">
                      <Loader2 size={10} className="animate-spin" />
                      Compacting context
                    </div>
                  </>
                )}
              </div>

              <div className="flex items-center gap-4">
                {/* Context ring indicator */}
                {(() => {
                  // Estimate tokens: use stored token_count when available, fall back to chars/4
                  const estimatedTokens = messages.reduce((sum, m) => {
                    return sum + (m.token_count ?? Math.round((m.content?.length ?? 0) / 4));
                  }, 0);
                  const hasBudget = typeof contextTokenBudget === 'number' && contextTokenBudget > 0;
                  const CTX_CEILING = hasBudget ? contextTokenBudget : 1;
                  const fill = hasBudget ? Math.min(estimatedTokens / CTX_CEILING, 1) : 0;
                  const pct = Math.round(fill * 100);
                  const r = 7;
                  const circ = 2 * Math.PI * r;
                  const dash = circ * fill;
                  const ringColor = fill < 0.5 ? '#10b981' : fill < 0.8 ? '#f59e0b' : '#ef4444';
                  const warn = hasBudget && estimatedTokens >= CTX_CEILING;
                  const kTokens = estimatedTokens >= 1000 ? `${(estimatedTokens / 1000).toFixed(1)}k` : `${estimatedTokens}`;
                  const ceilingLabel = hasBudget ? `${Math.round(CTX_CEILING / 1000)}k tokens` : '…';
                  return (
                    <div className="relative flex items-center gap-1.5 group cursor-default">
                      <svg width="18" height="18" viewBox="0 0 20 20" className="-rotate-90 shrink-0">
                        <circle cx="10" cy="10" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="2.5" />
                        <circle
                          cx="10" cy="10" r={r} fill="none"
                          stroke={ringColor}
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeDasharray={`${dash} ${circ}`}
                          className="transition-all duration-500"
                        />
                      </svg>
                      <span className={`text-[10px] font-mono font-bold transition-colors ${warn ? 'text-amber-500' : 'text-[color:var(--text-muted)]'}`}>
                        {pct}<span className="opacity-40">%</span>
                      </span>
                      {warn && <span className="absolute -top-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />}
                      {/* Tooltip */}
                      <div className="absolute top-full right-0 mt-2 px-3 py-2 rounded-lg bg-[color:var(--surface-0)] border border-[color:var(--border)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-xl z-50 space-y-1">
                        <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Context Window</div>
                        <div><span className="font-bold" style={{ color: ringColor }}>~{kTokens}</span><span className="text-[color:var(--text-muted)]"> / {ceilingLabel}</span></div>
                        <div className="text-[color:var(--text-muted)]">{messages.length} messages · {pct}% used</div>
                        {warn && <div className="text-amber-500 font-bold">Auto-compaction should trigger on next turn</div>}
                      </div>
                    </div>
                  );
                })()}

                <div className="w-px h-3 bg-[color:var(--border)]" />

                {/* --- Effort selector (Fast / Normal / Deep Think) --- */}
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Effort:</span>
                  <div className="relative">
                    {(() => {
                        const active = models.find(m => m.tier === selectedTier) || models[0];
                        const tier = active?.tier ?? 'normal';
                        const icons: Record<string, any> = {
                          fast: <Zap size={10} />,
                          normal: <Sparkles size={10} />,
                          hard: <Brain size={10} />,
                        };
                        const themes: Record<string, string> = {
                          fast: 'bg-emerald-600 dark:bg-emerald-500/40 border-emerald-600/20 dark:border-emerald-500/50 text-white dark:text-white/90 hover:bg-emerald-500 dark:hover:bg-emerald-500/50',
                          normal: 'bg-sky-600 dark:bg-sky-500/40 border-sky-600/20 dark:border-sky-500/50 text-white dark:text-white/90 hover:bg-sky-500 dark:hover:bg-sky-500/50',
                          hard: 'bg-rose-600 dark:bg-rose-500/40 border-rose-600/20 dark:border-rose-500/50 text-white dark:text-white/90 hover:bg-rose-500 dark:hover:bg-rose-500/50',
                        };
                        return (
                          <button
                            onClick={() => setIsEffortDropdownOpen(!isEffortDropdownOpen)}
                            className={`flex items-center gap-2 px-2.5 h-6 text-[10px] font-bold uppercase tracking-widest rounded-lg transition-all duration-200 border shadow-sm ${themes[tier] || 'bg-[color:var(--surface-2)] border-[color:var(--border-subtle)] text-[color:var(--text-primary)]'}`}
                          >
                            <span className="opacity-90">{icons[tier] || <Activity size={10} />}</span>
                            <span>{active?.label || 'Select'}</span>
                            <ChevronDown size={10} className={`transition-transform duration-200 opacity-40 ${isEffortDropdownOpen ? 'rotate-180' : ''}`} />
                          </button>
                        );
                      })()}

                    {isEffortDropdownOpen && (
                      <>
                        <div
                          className="fixed inset-0 z-40"
                          onClick={() => setIsEffortDropdownOpen(false)}
                        />
                        <div className="absolute top-full left-0 mt-1 w-64 rounded-xl bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-2xl z-50 overflow-hidden py-1 animate-in fade-in zoom-in-95 duration-100">
                          {models.map(m => {
                            const active = selectedTier === m.tier;
                            const tier = m.tier ?? 'normal';
                            const icons: Record<string, any> = {
                              fast: <Zap size={14} className={active ? 'text-white/90' : 'text-emerald-500'} />,
                              normal: <Sparkles size={14} className={active ? 'text-white/90' : 'text-sky-500'} />,
                              hard: <Brain size={14} className={active ? 'text-white/90' : 'text-rose-500'} />,
                            };
                            const bgColors: Record<string, string> = {
                              fast: 'bg-emerald-600 dark:bg-emerald-500/40',
                              normal: 'bg-sky-600 dark:bg-sky-500/40',
                              hard: 'bg-rose-600 dark:bg-rose-500/40',
                            };
                            return (
                              <button
                                key={m.tier}
                                onClick={() => {
                                  setSelectedTier(m.tier);
                                  setIsEffortDropdownOpen(false);
                                }}
                                className={`w-full flex items-start gap-3 px-4 py-3 transition-all text-left group ${
                                  active
                                    ? `text-white ${bgColors[tier] || ''}`
                                    : 'hover:bg-[color:var(--surface-1)]'
                                }`}
                              >
                                <div className="mt-0.5 shrink-0 transition-transform group-hover:scale-110 duration-200">
                                  {icons[tier] || <Activity size={14} />}
                                </div>
                                <div className="flex flex-col gap-0.5 min-w-0">
                                  <div className={`text-[10px] font-bold uppercase tracking-widest ${active ? 'text-white' : 'text-[color:var(--text-primary)] group-hover:text-[color:var(--text-primary)]'}`}>
                                    {m.label}
                                  </div>
                                  <div className={`text-[9px] font-medium leading-tight ${active ? 'text-white/80' : 'text-[color:var(--text-muted)]'}`}>
                                    {m.description}
                                  </div>
                                  {m.primary_provider_id && (
                                    <div className="mt-1.5 flex items-center gap-1.5">
                                      <span className={`text-[8px] font-mono px-1 rounded uppercase ${active ? 'bg-black/20 text-white/90' : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] opacity-60'}`}>{m.primary_provider_id}</span>
                                      <span className={`text-[8px] font-mono truncate ${active ? 'text-white/40' : 'text-[color:var(--text-muted)] opacity-40'}`}>{m.primary_model_id}</span>
                                    </div>
                                  )}
                                </div>
                                {active && (
                                  <div className="ml-auto w-1 h-1 rounded-full bg-white/60 mt-1.5 shadow-[0_0_8px_rgba(255,255,255,0.2)]" />
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="w-px h-3 bg-[color:var(--border)]" />

                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Steps:</span>
                  <select
                      value={maxIterations}
                      onChange={(e) => setMaxIterations(Number(e.target.value))}
                      className="bg-transparent text-xs font-semibold outline-none cursor-pointer hover:text-[color:var(--accent-solid)] transition-colors"
                  >
                    {[5, 10, 15, 20, 25, 30, 50, 75, 100].map(n => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* Messages */}
            <div
                ref={scrollRef}
                onScroll={onMessagesScroll}
                className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 scroll-smooth"
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
                        <MessageCard
                          key={m.id}
                          message={m}
                          toolArgumentsByCallId={toolArgumentsByCallId}
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
                        />
                      );
                    })}

                    {streaming.completedToolCalls
                      .filter((call) => !timelineToolKeys.has(streamingCallKey(call)))
                      .map((c, idx) => (
                        <StreamToolCard key={`${c.id}-${c.contentIndex ?? 'na'}-fallback-complete-${idx}`} call={c} active={false} />
                      ))}
                    {streaming.activeToolCalls
                      .filter((call) => !timelineToolKeys.has(streamingCallKey(call)))
                      .map((c, idx) => (
                        <StreamToolCard key={`${c.id}-${c.contentIndex ?? 'na'}-fallback-active-${idx}`} call={c} active={true} />
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
                        <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-amber-500 animate-pulse">
                          <Loader2 size={14} className="animate-spin" />
                          Compacting context...
                        </div>
                    )}

                    {streaming.agentIteration > 0 || streaming.isThinking || streaming.isStreaming || streaming.activeToolCalls.length > 0 ? (
                      <div className="sticky bottom-2 z-20 flex justify-center pointer-events-none">
                        <button
                          type="button"
                          onClick={stopCurrent}
                          disabled={isStopping}
                          className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-rose-500/40 bg-rose-500/10 px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500/20 disabled:opacity-60 shadow-lg"
                        >
                          <Square size={12} fill="currentColor" />
                          {isStopping ? 'Stopping...' : 'Stop'}
                        </button>
                      </div>
                    ) : null}

                    {!isPinnedToBottom && (
                      <div className="sticky bottom-2 z-20 flex justify-end pointer-events-none">
                        <button
                          type="button"
                          onClick={() => scrollToBottom('smooth')}
                          className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--border-strong)] bg-[color:var(--surface-0)] px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)] shadow-lg hover:border-[color:var(--accent-solid)] hover:text-[color:var(--accent-solid)] transition-colors"
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
                          className="p-2 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-2)] text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                          title="Attach image"
                        >
                          <Paperclip size={16} />
                        </button>
                        <button
                            type="submit"
                            disabled={(composer.trim().length === 0 && composerAttachments.length === 0) || streamBusy}
                            className={`p-2 rounded-lg transition-all ${
                                (composer.trim().length > 0 || composerAttachments.length > 0) && !streamBusy
                                    ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-md'
                                    : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] cursor-not-allowed'
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

          {/* Resize Handle */}
          <div
              className={`hidden xl:block w-1 cursor-col-resize hover:bg-[color:var(--accent-solid)] transition-colors ${isResizing ? 'bg-[color:var(--accent-solid)]' : 'bg-transparent'}`}
              onMouseDown={startResizing}
          />

          {/* Right Rail (Tools & Browser) */}
          <aside
              style={{ width: `${rightPanelWidth}px` }}
              className="hidden xl:flex flex-col border-l border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden"
          >
                       {/* Browser Preview */}
                       <div className="flex flex-col min-h-0 border-b border-[color:var(--border-subtle)]">
                          <div className="flex items-center justify-between p-4 border-b border-[color:var(--border-subtle)]">
                            <div className="flex items-center gap-2">
                              <Globe size={16} className="text-sky-500" />
                              <h2 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Live Browser</h2>
                            </div>
                                            <div className="flex items-center gap-1">
                                                                                  <button 
                                                                                    onClick={() => resetBrowser()} 
                                                                                    disabled={isResettingBrowser}
                                                                                    className="p-1.5 rounded-md hover:bg-rose-500/10 transition-colors text-rose-500 disabled:opacity-50"
                                                                                    title="Reset browser runtime"
                                                                                  >
                                                                
                                                                  <RotateCcw size={14} className={isResettingBrowser ? 'animate-spin' : ''} />
                                                                </button>
                                              
                                              <button onClick={() => setIsBrowserFullscreen(true)} className="p-1.5 rounded-md hover:bg-[color:var(--surface-2)] transition-colors text-sky-500">
                                                <Expand size={14} />
                                              </button>
                                            </div>
                            
                          </div>
                          <div className="flex-1 min-h-0">
                             <div className="relative aspect-video w-full bg-black overflow-hidden border-b border-[color:var(--border-subtle)]">
                                <BrowserPreview 
                                  url={liveView?.url ?? null} 
                                  isFullscreen={isBrowserFullscreen}
                                  onClose={() => setIsBrowserFullscreen(false)}
                                />
                             </div>
                          </div>
                       </div>
            
                       {/* Sub-Agents Panel */}
                       <div className="flex-1 flex flex-col min-h-0">
                          <div className="flex items-center justify-between p-4 border-b border-[color:var(--border-subtle)]">
                            <div className="flex items-center gap-2">
                              <Terminal size={16} className="text-emerald-500" />
                              <h2 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Sub-Agents</h2>
                            </div>
                            <span className="text-[10px] bg-emerald-500/10 text-emerald-600 px-1.5 py-0.5 rounded font-bold">{tasks.length} Active</span>
                          </div>
            
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                {tasks.map(t => (
                    <div key={t.id} className="p-3 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] shadow-sm space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-bold truncate">{t.name}</span>
                        <StatusChip label={t.status} tone={taskStatusTone(t.status)} className="scale-75 origin-right" />
                      </div>
                      <p className="text-[10px] text-[color:var(--text-secondary)] line-clamp-2 leading-relaxed">
                        {t.scope || 'No scope defined.'}
                      </p>
                      <div className="flex items-center gap-2 pt-1">
                        <button
                            onClick={() => { setSelectedTask(t); setIsTaskModalOpen(true); }}
                            className="text-[10px] font-bold text-[color:var(--accent-solid)] hover:underline uppercase tracking-wide"
                        >
                          View Task
                        </button>
                        {(t.status === 'running' || t.status === 'pending') && (
                          <>
                            <div className="h-3 w-px bg-[color:var(--border-subtle)]" />
                            <button
                                onClick={() => terminateTask(t.id)}
                                className="text-[10px] font-bold text-rose-500 hover:underline uppercase tracking-wide"
                            >
                              Terminate
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                ))}
                {tasks.length === 0 && (
                    <div className="h-32 flex flex-col items-center justify-center text-[color:var(--text-muted)] opacity-50 gap-2">
                      <Terminal size={24} strokeWidth={1} />
                      <p className="text-[10px] font-medium uppercase tracking-widest">Idle</p>
                    </div>
                )}
              </div>
              <div className="p-4 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/30">
                <button
                    onClick={() => setIsSpawnModalOpen(true)}
                    className="btn-primary w-full h-10 text-xs shadow-sm"
                >
                  <Plus size={14} />
                  Spawn Sub-Agent
                </button>
              </div>
            </div>
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
