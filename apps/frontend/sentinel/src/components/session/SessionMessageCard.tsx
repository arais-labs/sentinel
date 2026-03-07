import { ChevronDown, Clock3, Globe, Loader2, Send, Users, Wrench, X } from 'lucide-react';
import { memo, useEffect, useMemo, useRef, useState } from 'react';

import { JsonBlock } from '../ui/JsonBlock';
import { Markdown } from '../ui/Markdown';
import { approvalKey, approvalRefFromMetadata, isWaitingApproval, type ApprovalRef } from '../../lib/approvals';
import { formatCompactDate } from '../../lib/format';
import {
  extractCriticalToolFields,
  parsePayloadJson,
  previewPayloadValue,
  topLevelPayloadFieldCount,
  type ToolPayloadKind,
} from '../../lib/toolPayloadPreview';
import type { Message, MessageAttachment } from '../../types/api';

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
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

export function buildToolArgumentsByCallId(messages: Message[]): Map<string, string> {
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
}

interface TelegramGroupTurnContext {
  chatTitle: string;
  userName: string;
}

interface MessageGenerationMetadata {
  requestedTier?: string;
  resolvedModel?: string;
  provider?: string;
  temperature?: number;
  maxIterations?: number;
}

function isTierLikeValue(value: string | undefined): boolean {
  if (!value) return false;
  const normalized = value.trim().toLowerCase();
  return normalized === 'fast' || normalized === 'normal' || normalized === 'hard' || normalized === 'tier';
}

function parseMessageGenerationMetadata(message: Message): MessageGenerationMetadata | null {
  const rawGeneration = isObjectRecord(message.metadata) ? message.metadata.generation : null;
  if (!isObjectRecord(rawGeneration)) return null;
  const requestedTier = typeof rawGeneration.requested_tier === 'string'
    ? rawGeneration.requested_tier.trim()
    : '';
  const resolvedModelRaw = typeof rawGeneration.resolved_model === 'string'
    ? rawGeneration.resolved_model.trim()
    : '';
  const resolvedModel = isTierLikeValue(resolvedModelRaw) ? '' : resolvedModelRaw;
  const providerRaw = typeof rawGeneration.provider === 'string' ? rawGeneration.provider.trim() : '';
  const provider = providerRaw.toLowerCase() === 'tier' ? '' : providerRaw;
  const temperature = typeof rawGeneration.temperature === 'number' ? rawGeneration.temperature : undefined;
  const maxIterations = typeof rawGeneration.max_iterations === 'number' ? rawGeneration.max_iterations : undefined;
  if (!requestedTier && !resolvedModel && !provider && temperature == null && maxIterations == null) {
    return null;
  }
  return {
    requestedTier: requestedTier || undefined,
    resolvedModel: resolvedModel || undefined,
    provider: provider || undefined,
    temperature,
    maxIterations,
  };
}

function formatGenerationFooter(
  metadata: MessageGenerationMetadata | null,
  role: string,
): string | null {
  if (!metadata) return null;
  if (role !== 'assistant' && role !== 'tool_result') return null;
  const parts: string[] = [];
  const hasResolvedModel = Boolean(metadata.resolvedModel && metadata.resolvedModel.trim());
  if (!hasResolvedModel) {
    return null;
  }
  if (hasResolvedModel) {
    parts.push(metadata.resolvedModel!.trim());
  }
  if (metadata.provider) parts.push(metadata.provider);
  if (typeof metadata.temperature === 'number') parts.push(`temp ${Number(metadata.temperature.toFixed(2))}`);
  if (typeof metadata.maxIterations === 'number' && Number.isFinite(metadata.maxIterations)) {
    parts.push(`max ${Math.trunc(metadata.maxIterations)}`);
  }
  return parts.length ? parts.join(' · ') : null;
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

const SOURCE_CHIP_RENDERERS: Record<string, (metadata: Record<string, unknown>) => JSX.Element> = {
  telegram: (metadata) => {
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
  },
  web: () => (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 text-[9px] font-bold uppercase tracking-wide">
      <Globe size={9} />
      Web
    </span>
  ),
  trigger: (metadata) => {
    const triggerName = typeof metadata.trigger_name === 'string' ? metadata.trigger_name.trim() : '';
    return (
      <span className="inline-flex max-w-[260px] items-center gap-1 px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-300 text-[9px] font-bold uppercase tracking-wide">
        <Clock3 size={9} />
        <span className="truncate">{triggerName ? `Trigger · ${triggerName}` : 'Trigger'}</span>
      </span>
    );
  },
};

const SourceChip = memo(({ metadata }: { metadata: Record<string, unknown> }) => {
  const rawSource = metadata?.source as string | undefined;
  const source = typeof rawSource === 'string' ? rawSource.trim().toLowerCase() : '';
  if (!source) return null;
  const render = SOURCE_CHIP_RENDERERS[source];
  return render ? render(metadata) : null;
});
SourceChip.displayName = 'SourceChip';

export const SessionMessageCard = memo(({
  message,
  toolArgumentsByCallId,
  onResolveApproval,
  resolvingApprovalKey = null,
}: {
  message: Message;
  toolArgumentsByCallId: Map<string, string>;
  onResolveApproval?: (approval: ApprovalRef, decision: 'approve' | 'reject') => void;
  resolvingApprovalKey?: string | null;
}) => {
  const isUser = message.role === 'user';
  const isToolResult = message.role === 'tool_result';
  const toolMetadata = isObjectRecord(message.metadata) ? message.metadata : {};
  const isTelegramGroupResponse = !isUser && !isToolResult && isTelegramGroupAuditMessage(message);
  const telegramGroupLabel = parseTelegramGroupResponseLabel(message.content ?? '');
  const renderedAssistantContent = isTelegramGroupResponse
    ? (message.content ?? '').replace(/^TG Group Response[^\n]*\n?/i, '').trimStart()
    : message.content;
  const userAttachments = isUser ? extractImageAttachments(message.metadata) : [];
  const attachments = (message.metadata?.attachments as Array<{ base64: string }> | undefined) ?? [];
  const screenshotBase64 = isToolResult ? (attachments.find((a) => a.base64)?.base64 ?? null) : null;
  const isScreenshotTool =
    isToolResult &&
    (Boolean(screenshotBase64) || String(message.tool_name ?? '').toLowerCase().includes('screenshot'));
  const toolInputRaw =
    isToolResult && message.tool_call_id
      ? (toolArgumentsByCallId.get(message.tool_call_id) ?? '')
      : '';
  const toolFailed = Boolean(isToolResult && message.metadata?.is_error);
  const pendingApproval = Boolean(
    isToolResult &&
    isWaitingApproval(toolMetadata),
  );
  const generationFooter = formatGenerationFooter(parseMessageGenerationMetadata(message), message.role);
  const approvalRef = pendingApproval ? approvalRefFromMetadata(toolMetadata) : null;
  const canResolveApproval = Boolean(
    pendingApproval &&
    approvalRef?.canResolve === true &&
    onResolveApproval,
  );
  const approvalActionBusy = approvalRef ? resolvingApprovalKey === approvalKey(approvalRef) : false;
  const [toolExpanded, setToolExpanded] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

  function openLightbox() { setLightboxOpen(true); setZoom(1); setPan({ x: 0, y: 0 }); }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    setZoom((z) => Math.min(10, Math.max(0.5, z * (e.deltaY < 0 ? 1.1 : 0.9))));
  }

  function onMouseDown(e: React.MouseEvent) {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  }

  function onMouseMove(e: React.MouseEvent) {
    if (!dragRef.current) return;
    setPan({
      x: dragRef.current.panX + e.clientX - dragRef.current.startX,
      y: dragRef.current.panY + e.clientY - dragRef.current.startY,
    });
  }

  function onMouseUp() { dragRef.current = null; }

  useEffect(() => {
    if (isScreenshotTool) {
      setToolExpanded(true);
    }
  }, [isScreenshotTool]);

  const cardWidthClass = isToolResult
    ? (toolExpanded ? 'w-full max-w-[90%]' : 'w-fit max-w-[90%]')
    : 'max-w-[90%]';

  return (
    <div className={`flex w-full flex-col gap-1 animate-in ${isUser ? 'items-end' : 'items-start'}`}>
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
        className={`${isToolResult ? `${cardWidthClass} inline-flex flex-col` : cardWidthClass} rounded-2xl px-4 py-1.5 text-xs shadow-sm border ${
          isUser
            ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent rounded-tr-none font-medium'
            : isToolResult
              ? pendingApproval
                ? 'bg-rose-500/10 border-rose-500/35 font-mono text-[12px] rounded-tl-none'
                : 'bg-sky-500/5 border-sky-500/20 font-mono text-[12px] rounded-tl-none'
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
                <Wrench size={12} className={`${pendingApproval ? 'text-rose-400' : 'text-sky-600 dark:text-sky-400'} shrink-0`} />
                <span className={`font-bold uppercase tracking-wide truncate ${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-400'}`}>
                  {message.tool_name || 'tool_result'}
                </span>
                {pendingApproval ? (
                  <span className="inline-flex items-center rounded-full border border-rose-500/35 bg-rose-500/15 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-rose-300">
                    Waiting Approval
                  </span>
                ) : null}
              </div>
              <ChevronDown size={14} className={`${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-400'} shrink-0 transition-transform ${toolExpanded ? 'rotate-180' : ''}`} />
            </button>
            {toolExpanded ? (
              <div className={`mt-3 border-t border-sky-500/10 pt-3 grid ${isScreenshotTool ? 'grid-cols-1' : 'grid-cols-2'} gap-3 animate-in fade-in duration-200`}>
                {!isScreenshotTool ? (
                  <div className="min-w-0">
                    <p className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1">Input</p>
                    <ToolPayloadView
                      raw={toolInputRaw}
                      emptyLabel="No input payload."
                      toolName={message.tool_name || 'tool_result'}
                      payloadKind="input"
                    />
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
                            onClick={(e) => e.stopPropagation()}
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
                                top: '50%',
                                left: '50%',
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
                    <div className="space-y-2">
                      <ToolPayloadView
                        raw={message.content}
                        emptyLabel="No output payload."
                        showRawJson={!isScreenshotTool}
                        toolName={message.tool_name || 'tool_result'}
                        payloadKind="output"
                      />
                      {canResolveApproval && approvalRef && onResolveApproval ? (
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
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <ToolPayloadCompactSummary
                toolName={message.tool_name || 'tool_result'}
                inputRaw={toolInputRaw}
                outputRaw={message.content}
                outputEmptyLabel="No output payload."
                outputError={toolFailed}
                hideInput={isScreenshotTool}
              />
            )}
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
      {generationFooter ? (
        <div className={`${cardWidthClass} -mt-2 pl-2 pr-1 ${isUser ? 'text-right' : 'text-left'}`}>
          <span className="text-[10px] leading-none text-[color:var(--text-muted)] opacity-75">{generationFooter}</span>
        </div>
      ) : null}
    </div>
  );
});

SessionMessageCard.displayName = 'SessionMessageCard';
