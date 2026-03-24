import { AlertCircle, ArrowRight, Check, CheckCircle2, ChevronDown, Clock3, Globe, Hash, Loader2, Send, Terminal, Users, Wrench, X } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

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

// --- Syntax highlighting ---

type Lang = 'json' | 'shell' | 'plain';

// ---- language detection ----

const SHELL_KEYWORDS = /\b(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|export|source|echo|printf|cd|ls|grep|awk|sed|cat|rm|cp|mv|mkdir|chmod|chown|sudo|apt|brew|pip|npm|yarn|git|docker|kubectl|python|python3|node|bash|sh)\b/;

function detectLang(content: string): Lang {
  // JSON: must start with { [ " or a JSON primitive
  const trimmed = content.trimStart();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try { JSON.parse(content); return 'json'; } catch { /* fall through */ }
  }
  // Shell: shebang, or at least one shell keyword / operator pattern
  if (
    trimmed.startsWith('#!') ||
    trimmed.startsWith('$ ') ||
    SHELL_KEYWORDS.test(content) ||
    /\|\s*\w|\bsudo\b|&&|\|\||\bexport\b/.test(content)
  ) return 'shell';
  return 'plain';
}

// ---- JSON tokenizer ----

type JsonTok = 'key' | 'string' | 'number' | 'boolean' | 'null' | 'punct' | 'ws' | 'other';
const JSON_RE =
  /("(?:[^"\\]|\\.)*"(?=\s*:))|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|(true|false)|(null)|([{}\[\],:])|(\s+)|(.)/g;

function tokenizeJson(src: string) {
  JSON_RE.lastIndex = 0;
  const out: Array<{ text: string; type: JsonTok }> = [];
  let m: RegExpExecArray | null;
  while ((m = JSON_RE.exec(src)) !== null) {
    if      (m[1]) out.push({ text: m[1], type: 'key' });
    else if (m[2]) out.push({ text: m[2], type: 'string' });
    else if (m[3]) out.push({ text: m[3], type: 'number' });
    else if (m[4]) out.push({ text: m[4], type: 'boolean' });
    else if (m[5]) out.push({ text: m[5], type: 'null' });
    else if (m[6]) out.push({ text: m[6], type: 'punct' });
    else if (m[7]) out.push({ text: m[7], type: 'ws' });
    else           out.push({ text: m[8] ?? '', type: 'other' });
  }
  return out;
}

const JSON_CLASS: Record<JsonTok, string> = {
  key:     'text-sky-400 dark:text-sky-300',
  string:  'text-emerald-600 dark:text-emerald-300',
  number:  'text-violet-500 dark:text-violet-400',
  boolean: 'text-orange-500 dark:text-orange-400',
  null:    'text-rose-500 dark:text-rose-400',
  punct:   'text-[color:var(--text-muted)]',
  ws:      '',
  other:   'text-[color:var(--text-secondary)]',
};

// ---- Shell tokenizer ----
// processes line-by-line: comment > string > variable > flag > keyword > plain

type ShellTok = 'comment' | 'string' | 'variable' | 'flag' | 'keyword' | 'operator' | 'plain';

const SHELL_LINE_RE =
  /(#.*$)|("(?:[^"\\]|\\.)*"|'[^']*')|(\\$\{[^}]+\}|\$[\w@*#?$!0-9]+)|(--?[\w-][\w-]*)|(if|then|else|elif|fi|for|while|do|done|case|esac|function|return|export|echo|printf|cd|sudo|git|docker|kubectl|python3?|node|bash|sh|pip|npm|yarn|grep|awk|sed|cat|rm|cp|mv|mkdir|chmod|chown|apt|brew)\b|([|&;><]+)|([\s\S]+?(?=\#|"|'|\$|--|(?:if|then|else|elif|fi|for|while|do|done|case|esac|function|return|export|echo|printf|cd|sudo|git|docker|kubectl|python3?|node|bash|sh|pip|npm|yarn|grep|awk|sed|cat|rm|cp|mv|mkdir|chmod|chown|apt|brew)\b|[|&;><]|$))/gm;

function tokenizeShell(src: string) {
  SHELL_LINE_RE.lastIndex = 0;
  const out: Array<{ text: string; type: ShellTok }> = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = SHELL_LINE_RE.exec(src)) !== null) {
    if (m.index > last) out.push({ text: src.slice(last, m.index), type: 'plain' });
    if      (m[1]) out.push({ text: m[1], type: 'comment' });
    else if (m[2]) out.push({ text: m[2], type: 'string' });
    else if (m[3]) out.push({ text: m[3], type: 'variable' });
    else if (m[4]) out.push({ text: m[4], type: 'flag' });
    else if (m[5]) out.push({ text: m[5], type: 'keyword' });
    else if (m[6]) out.push({ text: m[6], type: 'operator' });
    else           out.push({ text: m[7] ?? m[0], type: 'plain' });
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push({ text: src.slice(last), type: 'plain' });
  return out;
}

const SHELL_CLASS: Record<ShellTok, string> = {
  comment:  'text-[color:var(--text-muted)] italic',
  string:   'text-amber-600 dark:text-amber-300',
  variable: 'text-violet-500 dark:text-violet-400',
  flag:     'text-sky-500 dark:text-sky-400',
  keyword:  'text-rose-500 dark:text-rose-400',
  operator: 'text-[color:var(--text-muted)]',
  plain:    'text-[color:var(--text-primary)]',
};

// ---- PopupContent ----

function PopupContent({ content }: { content: string }) {
  const { lang, tokens } = useMemo(() => {
    const lang = detectLang(content);
    if (lang === 'json') {
      const pretty = JSON.stringify(JSON.parse(content), null, 2);
      return { lang, tokens: tokenizeJson(pretty) };
    }
    if (lang === 'shell') {
      return { lang, tokens: tokenizeShell(content) };
    }
    return { lang, tokens: null };
  }, [content]);

  if (!tokens) {
    return (
      <pre className="font-mono text-[12px] whitespace-pre-wrap break-words text-[color:var(--text-primary)] leading-relaxed">
        {content}
      </pre>
    );
  }

  if (lang === 'json') {
    const jsonTokens = tokens as ReturnType<typeof tokenizeJson>;
    return (
      <pre className="font-mono text-[12px] whitespace-pre-wrap break-words leading-relaxed">
        {jsonTokens.map((tok, i) =>
          tok.type === 'ws' ? tok.text : (
            <span key={i} className={JSON_CLASS[tok.type]}>{tok.text}</span>
          ),
        )}
      </pre>
    );
  }

  const shellTokens = tokens as ReturnType<typeof tokenizeShell>;
  return (
    <pre className="font-mono text-[12px] whitespace-pre-wrap break-words leading-relaxed">
      {shellTokens.map((tok, i) => (
        <span key={i} className={SHELL_CLASS[tok.type]}>{tok.text}</span>
      ))}
    </pre>
  );
}

function ToolFieldPopup({
  title,
  content,
  anchorRect,
  onClose,
}: {
  title: string;
  content: string;
  anchorRect: DOMRect;
  onClose: () => void;
}) {
  const [position, setPosition] = useState<{ top: number; left: number; visible: boolean }>({ top: 0, left: 0, visible: false });
  const popupRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!popupRef.current) return;
    const padding = 12;
    const { width, height } = popupRef.current.getBoundingClientRect();
    if (!width && !height) return; // not laid out yet
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let top = anchorRect.bottom + 8;
    let left = anchorRect.left;

    // flip above if going off the bottom
    if (top + height > vh - padding) {
      top = anchorRect.top - height - 8;
    }
    // clamp vertical (handles both "flipped above" going off top, and anchor near top)
    top = Math.max(padding, Math.min(top, vh - height - padding));

    // clamp horizontal
    left = Math.max(padding, Math.min(left, vw - width - padding));

    setPosition({ top, left, visible: true });
  }, [anchorRect]);

  return createPortal(
    <div
      className="fixed inset-0 z-[300]"
      onClick={onClose}
    >
      <div
        ref={popupRef}
        style={{ top: position.top, left: position.left, visibility: position.visible ? 'visible' : 'hidden' }}
        className="absolute w-fit max-w-[500px] min-w-[280px] rounded-xl border border-sky-500/30 bg-[color:var(--surface-0)] shadow-2xl animate-in fade-in zoom-in-95 duration-150 p-0 overflow-hidden backdrop-blur-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-3 py-2 border-b border-sky-500/10 bg-sky-500/5">
           <span className="text-[10px] font-black uppercase tracking-widest text-sky-500">{title}</span>
           <button onClick={onClose} className="p-1 hover:bg-sky-500/10 rounded-full transition-colors text-sky-500/50 hover:text-sky-500">
              <X size={12} strokeWidth={3} />
           </button>
        </div>
        <div className="p-4 overflow-auto max-h-[400px] selection:bg-sky-500/20">
          <PopupContent content={content} />
        </div>
      </div>
    </div>,
    document.body
  );
}

function ToolFieldPreviewList({
  items,
  extraCount = 0,
  variant = 'input',
}: {
  items: Array<{ key: string; text: string; fullText?: string; truncated: boolean; redacted?: boolean }>;
  extraCount?: number;
  variant?: 'input' | 'output';
}) {
  const [hoveredField, setHoveredField] = useState<{ item: any; rect: DOMRect } | null>(null);
  const c = variant === 'output'
    ? { border: 'border-emerald-500/15', hover: 'hover:bg-emerald-500/5', bar: 'group-hover/field:bg-emerald-500/40', key: 'text-emerald-500/50', hint: 'text-emerald-500/40 group-hover/field:text-emerald-500', extra: 'text-emerald-500/30 bg-emerald-500/10' }
    : { border: 'border-sky-500/10',     hover: 'hover:bg-sky-500/5',     bar: 'group-hover/field:bg-sky-500/40',     key: 'text-sky-500/40',     hint: 'text-sky-500/40 group-hover/field:text-sky-500',     extra: 'text-sky-500/30 bg-sky-500/10' };

  return (
    <div className={`flex flex-col gap-0 border-l ${c.border} ml-2`}>
      {items.map((item) => (
        <div
          key={item.key}
          onClick={(e) => {
            if (item.truncated && item.fullText) {
              setHoveredField({ item, rect: e.currentTarget.getBoundingClientRect() });
            }
          }}
          className={`group/field relative flex gap-3 px-2.5 py-1.5 transition-all ${c.hover} hover:rounded-r-lg ${item.truncated ? 'cursor-pointer' : ''}`}
        >
          <div className={`absolute left-[-1px] top-0.5 bottom-0.5 w-0.5 bg-transparent ${c.bar} transition-colors`} />
          <div className="w-[85px] shrink-0 pt-0.5">
            <span className={`text-[9px] font-black uppercase tracking-widest ${c.key} font-mono`}>
              {item.key}
            </span>
          </div>
          <div className="flex-1 min-w-0">
            {item.redacted ? (
              <span className="text-[9px] font-black uppercase tracking-widest text-rose-400/60 bg-rose-500/5 px-1.5 py-0.5 rounded border border-rose-500/10 italic">
                Secret Masked
              </span>
            ) : (
              <div className="flex flex-col">
                <p className="font-mono text-[12px] break-words text-[color:var(--text-primary)] leading-normal whitespace-pre-wrap">
                  {item.text || <span className="italic opacity-30">null</span>}
                </p>
                {item.truncated && (
                  <span className={`text-[7px] font-black uppercase tracking-widest mt-0.5 transition-colors ${c.hint}`}>Click to expand</span>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
      {extraCount > 0 ? (
        <div className="flex items-center gap-3 pl-2.5 pt-1.5 cursor-default">
           <div className={`w-[85px] shrink-0 h-px ${c.extra}`} />
           <span className={`text-[8px] font-black uppercase tracking-[0.2em] ${c.key}`}>
              +{extraCount} more
           </span>
        </div>
      ) : null}

      {hoveredField && (
        <ToolFieldPopup
          title={hoveredField.item.key}
          content={hoveredField.item.fullText}
          anchorRect={hoveredField.rect}
          onClose={() => setHoveredField(null)}
        />
      )}
    </div>
  );
}

export function ToolPayloadView({
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

  const fieldVariant = payloadKind === 'output' ? 'output' : 'input';

  if (criticalOnly) {
    if (criticalFields.length > 0) {
      const extraCount = Math.max(0, topLevelPayloadFieldCount(raw) - criticalFields.length);
      return <ToolFieldPreviewList items={criticalFields} extraCount={extraCount} variant={fieldVariant} />;
    }
    const preview = previewPayloadValue(parsed ?? raw, 220);
    return (
      <ToolFieldPreviewList
        variant={fieldVariant}
        items={[{ key: payloadKind ?? 'payload', text: preview.text || '""', truncated: preview.truncated, redacted: false }]}
      />
    );
  }

  if (isObjectRecord(parsed)) {
    const entries = Object.entries(parsed).map(([key, value]) => {
      const preview = previewPayloadValue(value);
      const fullText = preview.truncated ? (typeof value === 'string' ? value : JSON.stringify(value, null, 2)) : undefined;
      return { key, text: preview.text, fullText, truncated: preview.truncated, redacted: false };
    });
    return (
      <div className="space-y-2">
        <ToolFieldPreviewList items={entries} variant={fieldVariant} />
        {showRawJson ? (
          <details className="group">
            <summary className="cursor-pointer list-none flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.1em] text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] transition-colors">
              <ChevronDown size={12} strokeWidth={3} className="group-open:rotate-180 transition-transform" />
              Raw JSON
            </summary>
            <JsonBlock value={JSON.stringify(parsed, null, 2)} className="mt-2 bg-transparent border-[color:var(--border-subtle)] p-2 max-h-[220px]" />
          </details>
        ) : null}
      </div>
    );
  }

  if (parsed !== null) {
    const preview = previewPayloadValue(parsed, 260);
    const fullText = preview.truncated ? (typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)) : undefined;
    return (
      <div className="space-y-2">
        <ToolFieldPreviewList
          variant={fieldVariant}
          items={[{ key: payloadKind ?? 'value', text: preview.text || '""', fullText, truncated: preview.truncated, redacted: false }]}
        />
        {showRawJson ? (
          <details className="group">
            <summary className="cursor-pointer list-none flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.1em] text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] transition-colors">
              <ChevronDown size={12} strokeWidth={3} className="group-open:rotate-180 transition-transform" />
              Raw JSON
            </summary>
            <JsonBlock value={JSON.stringify(parsed, null, 2)} className="mt-2 bg-transparent border-[color:var(--border-subtle)] p-2 max-h-[220px]" />
          </details>
        ) : null}
      </div>
    );
  }

  return (
    <details className="group">
      <summary className="cursor-pointer list-none flex items-center gap-2 text-sky-600/80 dark:text-sky-400/80 hover:text-sky-500 transition-colors">
        <ChevronDown size={14} strokeWidth={3} className="group-open:rotate-180 transition-transform" />
        <span className="font-black uppercase tracking-[0.1em] text-[10px]">Execution Telemetry</span>
      </summary>
      <div className="mt-3 overflow-auto">
        <Markdown content={raw} />
      </div>
    </details>
  );
}

export function ToolPayloadCompactSummary({
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
    if (trimmed.length <= 48) return trimmed;
    return `${trimmed.slice(0, 48)}…`;
  };

  return (
    <div className="mt-2.5 flex flex-col gap-1">
      {!hideInput && inputFields.length > 0 && (
        <div className="flex items-center gap-2 overflow-hidden px-1">
          <ArrowRight size={10} className="text-sky-500/60 shrink-0" />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 overflow-hidden">
            {inputFields.map(item => (
              <div key={item.key} className="flex items-center gap-1.5 shrink-0">
                <span className="text-[8px] font-black uppercase tracking-widest text-sky-500/60">{item.key}</span>
                <span className="text-[10px] font-mono text-[color:var(--text-primary)] truncate max-w-[180px]">
                   {item.redacted ? '****' : compactValue(item.text)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center gap-2 overflow-hidden px-1">
        {outputError ? (
          <AlertCircle size={10} className="text-rose-500/70 shrink-0" />
        ) : (
          <CheckCircle2 size={10} className="text-emerald-500/70 shrink-0" />
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 overflow-hidden">
          {outputFields.length > 0 ? outputFields.map(item => (
            <div key={item.key} className="flex items-center gap-1.5 shrink-0">
              <span className="text-[8px] font-black uppercase tracking-widest text-emerald-500/60">{item.key}</span>
              <span className="text-[10px] font-mono text-[color:var(--text-primary)] truncate max-w-[180px]">
                 {item.redacted ? '****' : compactValue(item.text)}
              </span>
            </div>
          )) : (
            <span className="text-[9px] text-[color:var(--text-muted)] italic">{outputEmptyLabel}</span>
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
  const approvalLinkMissing = pendingApproval && !approvalRef;
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
        onMouseEnter={() => !pendingApproval && setToolExpanded(true)}
        onMouseLeave={() => !pendingApproval && !isScreenshotTool && setToolExpanded(false)}
        className={`${isToolResult ? `${cardWidthClass} inline-flex flex-col` : cardWidthClass} rounded-2xl px-4 py-2 text-xs shadow-sm border transition-all duration-300 ease-in-out ${
          isUser
            ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent rounded-tr-none font-medium'
            : isToolResult
              ? pendingApproval
                ? 'bg-rose-500/8 border-rose-500/30 rounded-tl-none shadow-md ring-1 ring-rose-500/20'
                : `bg-[color:var(--surface-1)] border-[color:var(--border-subtle)] shadow-sm rounded-tl-none`
              : isTelegramGroupResponse
                ? 'bg-emerald-500/8 border-emerald-500/25 rounded-tl-none font-medium'
                : 'bg-[color:var(--surface-1)] border-[color:var(--border-subtle)] rounded-tl-none font-medium'
        }`}
      >
        {isToolResult ? (
          <>
            <div
              className={`${toolExpanded ? 'w-full mb-4' : 'w-auto'} flex items-center justify-between gap-4 text-left group/tool-btn py-0.5 cursor-default`}
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className={`flex items-center justify-center w-6 h-6 rounded-lg ${pendingApproval ? 'bg-rose-500/15 text-rose-400 border border-rose-500/25' : 'bg-sky-500/10 text-sky-400 border border-sky-500/20'} shrink-0`}>
                  <Wrench size={12} strokeWidth={2.5} />
                </div>
                <div className="flex flex-col min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-black uppercase tracking-[0.12em] truncate ${pendingApproval ? 'text-rose-300' : 'text-sky-600 dark:text-sky-300'}`}>
                      {message.tool_name || 'tool_result'}
                    </span>
                  </div>
                  {pendingApproval && (
                    <span className="text-[8px] font-bold uppercase tracking-widest text-rose-400 animate-pulse mt-0.5">
                      Action Required
                    </span>
                  )}
                </div>
              </div>
              <div className={`p-1 rounded-full ${pendingApproval ? 'text-rose-300' : 'text-sky-400'} transition-colors shrink-0`}>
                <ChevronDown size={14} strokeWidth={3} className={`transition-transform duration-500 ${toolExpanded ? 'rotate-180 opacity-40' : 'opacity-100'}`} />
              </div>
            </div>
            {toolExpanded ? (
              <div className="relative mt-3 border-t border-sky-500/20 pt-4 animate-in fade-in slide-in-from-top-1 duration-200">
                {/* Vertical trace line */}
                <div className="absolute left-[14px] top-4 bottom-0 w-[1px] border-l border-dashed border-sky-500/30" />

                <div className="space-y-6">
                  {!isScreenshotTool ? (
                    <div className="relative pl-10">
                      <div className="absolute left-0 top-0 z-10 flex items-center justify-center w-7 h-7 rounded-full bg-[color:var(--surface-1)] border border-sky-500/30 text-sky-500/60 shadow-sm">
                         <Terminal size={14} />
                      </div>
                      <div className="mb-2.5 flex items-center gap-2">
                        <p className="text-[10px] font-black uppercase tracking-[0.2em] text-sky-600 dark:text-sky-300">Arguments</p>
                        <div className="h-px flex-1 bg-gradient-to-r from-sky-500/30 to-transparent" />
                      </div>
                      <ToolPayloadView
                        raw={toolInputRaw}
                        emptyLabel="No input."
                        toolName={message.tool_name || 'tool_result'}
                        payloadKind="input"
                      />
                    </div>
                  ) : null}

                  <div className="relative pl-10 pb-2">
                    <div className={`absolute left-0 top-0 z-10 flex items-center justify-center w-7 h-7 rounded-full border ${toolFailed ? 'bg-rose-500/10 border-rose-500/20 text-rose-500/60' : 'bg-[color:var(--surface-1)] border-emerald-500/20 text-emerald-500/60'} shadow-sm`}>
                       {toolFailed ? <X size={14} strokeWidth={3} /> : <Check size={14} strokeWidth={3} />}
                    </div>
                    <div className="mb-2.5 flex items-center gap-2">
                      <p className={`text-[10px] font-black uppercase tracking-[0.2em] ${toolFailed ? 'text-rose-500/60' : 'text-emerald-500/60'}`}>Result</p>
                      <div className={`h-px flex-1 bg-gradient-to-r ${toolFailed ? 'from-rose-500/20' : 'from-emerald-500/20'} to-transparent`} />
                      {toolFailed && (
                        <span className="inline-flex items-center rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[7px] font-black uppercase tracking-widest text-rose-500">
                          Error
                        </span>
                      )}
                    </div>
                    {screenshotBase64 ? (
                      <div className="space-y-3">
                        <div className="relative group/screenshot">
                          <img
                            src={`data:image/png;base64,${screenshotBase64}`}
                            alt="Browser screenshot"
                            onClick={openLightbox}
                            className="rounded-xl max-w-full border border-sky-500/20 mt-0.5 cursor-zoom-in group-hover/screenshot:border-sky-500/40 transition-all shadow-md"
                            style={{ maxHeight: '400px', objectFit: 'contain' }}
                          />
                        </div>
                        <div className="flex items-center gap-2 text-[9px] text-[color:var(--text-muted)] italic px-1 opacity-60">
                           <Hash size={9} className="opacity-40" />
                           Frame captured
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <ToolPayloadView
                          raw={message.content}
                          emptyLabel="No output."
                          showRawJson={!isScreenshotTool}
                          toolName={message.tool_name || 'tool_result'}
                          payloadKind="output"
                        />
                        {canResolveApproval && approvalRef && onResolveApproval ? (
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
                    )}
                  </div>
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
