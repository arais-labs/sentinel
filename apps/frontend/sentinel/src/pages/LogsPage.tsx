import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  Clock,
  Layers,
  Loader2,
  Search,
  Shield,
  Terminal,
  Wrench,
  X,
  Zap,
  BadgeCheck,
  Cpu,
  MessageSquare,
  Network,
  Send,
  Users,
} from 'lucide-react';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { RuntimeExplorerModal } from '../components/RuntimeExplorerModal';
import { SessionHistorySidebar } from '../components/session/SessionHistorySidebar';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Markdown } from '../components/ui/Markdown';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate, truncate } from '../lib/format';
import type {
  Message,
  MessageListResponse,
  Session,
  SessionListResponse,
  SessionRuntimeStatus,
} from '../types/api';

type SidebarTab = 'sessions' | 'sub_agents';

type ArchitectureLayer =
  | 'ingress'
  | 'orchestration'
  | 'tools'
  | 'memory'
  | 'integration'
  | 'runtime';

type OperationalLens = 'input' | 'logic' | 'action' | 'recall' | 'bridge';

type ArchitectureEvent = {
  id: string;
  message: Message;
  layer: ArchitectureLayer;
  lens: OperationalLens;
  label: string;
  summary: string;
  source: string | null;
  tools: string[];
  timestamp: string;
  payload: unknown;
};

type PromptSectionCategory = 'core' | 'policy' | 'memory' | 'pinned' | 'integration';
type PromptSection = {
  title: string;
  content: string;
  category: PromptSectionCategory;
};

type RuntimeContextPayload = {
  contextMessageId: string;
  timestamp: string;
  runContext: Record<string, unknown>;
  structured: Record<string, unknown> | null;
};

const CONTEXT_AUTOFETCH_MAX_PAGES = 10;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeJsonParse(content: string): unknown | null {
  if (!content) return null;
  const trimmed = content.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return tryRepairTruncatedJson(trimmed);
  }
}

/**
 * Attempt to repair JSON that was truncated by the backend
 * (`\n...[TRUNCATED - N bytes total]` suffix).
 */
function tryRepairTruncatedJson(text: string): unknown | null {
  // Strip backend truncation marker
  const truncIdx = text.lastIndexOf('\n...[TRUNCATED');
  const base = truncIdx > 0 ? text.slice(0, truncIdx) : text;

  // Count unclosed structures (ignoring string interiors)
  function countOpen(s: string) {
    let braces = 0, brackets = 0, inString = false, escaped = false;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (escaped) { escaped = false; continue; }
      if (c === '\\' && inString) { escaped = true; continue; }
      if (c === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (c === '{') braces++;
      else if (c === '}') braces--;
      else if (c === '[') brackets++;
      else if (c === ']') brackets--;
    }
    return { braces, brackets, inString };
  }

  // Attempt 1: close string + brackets/braces at the end
  const info = countOpen(base);
  let suffix = '';
  if (info.inString) suffix += '"';
  if (info.brackets > 0) suffix += ']'.repeat(info.brackets);
  if (info.braces > 0) suffix += '}'.repeat(info.braces);
  try {
    return JSON.parse(base + suffix);
  } catch { /* proceed to fallback */ }

  // Attempt 2: trim back to the last top-level comma and close from there
  let lastTopComma = -1;
  let depth = 0, inStr = false, esc = false;
  for (let i = 0; i < base.length; i++) {
    const c = base[i];
    if (esc) { esc = false; continue; }
    if (c === '\\' && inStr) { esc = true; continue; }
    if (c === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (c === '{' || c === '[') depth++;
    if (c === '}' || c === ']') depth--;
    if (c === ',' && depth === 1) lastTopComma = i;
  }

  if (lastTopComma > 0) {
    const shorter = base.slice(0, lastTopComma);
    const s = countOpen(shorter);
    const close = ']'.repeat(Math.max(0, s.brackets)) + '}'.repeat(Math.max(0, s.braces));
    try {
      return JSON.parse(shorter + close);
    } catch { /* give up */ }
  }

  return null;
}

function extractSource(metadata: Record<string, unknown> | null | undefined): string | null {
  if (!metadata) return null;
  const source = metadata.source;
  if (typeof source !== 'string') return null;
  const trimmed = source.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function extractTools(message: Message): string[] {
  const names = new Set<string>();
  if (typeof message.tool_name === 'string' && message.tool_name.trim()) {
    names.add(message.tool_name.trim());
  }
  const metadata = message.metadata;
  if (typeof metadata.tool_name === 'string' && metadata.tool_name.trim()) {
    names.add(metadata.tool_name.trim());
  }
  const toolCalls = metadata.tool_calls;
  if (Array.isArray(toolCalls)) {
    for (const call of toolCalls) {
      if (!isRecord(call)) continue;
      const name = call.name;
      if (typeof name === 'string' && name.trim()) {
        names.add(name.trim());
      }
    }
  }
  return Array.from(names);
}

function toTimestamp(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function roleTone(role: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (role === 'assistant') return 'good';
  if (role === 'tool_result') return 'warn';
  if (role === 'system') return 'info';
  return 'default';
}

function classifyMessage(message: Message): ArchitectureEvent {
  const source = extractSource(message.metadata);
  const tools = extractTools(message);
  const parsedContent = safeJsonParse(message.content);

  let layer: ArchitectureLayer = 'orchestration';
  let lens: OperationalLens = 'logic';
  let label = 'Assistant step';
  let summary = truncate(message.content || '[empty]', 220);

  const lower = (message.content || '').toLowerCase();

  if (message.role === 'user') {
    layer = 'ingress';
    lens = 'input';
    label = source === 'telegram' ? 'Telegram ingress' : 'User ingress';
    summary = truncate(message.content || 'User message', 220);
  } else if (message.role === 'tool_result') {
    const toolName = tools[0] || 'unknown_tool';
    if (toolName.startsWith('memory_') || toolName.includes('memory')) {
      layer = 'memory';
      lens = 'recall';
      label = `Memory tool: ${toolName}`;
    } else if (toolName.startsWith('send_telegram') || toolName.startsWith('telegram_') || toolName.startsWith('trigger_')) {
      layer = 'integration';
      lens = 'bridge';
      label = `Integration tool: ${toolName}`;
    } else {
      layer = 'tools';
      lens = 'action';
      label = `Tool execution: ${toolName}`;
    }
    summary = truncate(message.content || 'Tool completed', 220);
  } else if (source === 'sub_agent' || lower.includes('[sub-agent report]')) {
    layer = 'orchestration';
    lens = 'logic';
    label = 'Sub-agent report';
    summary = truncate(message.content || 'Sub-agent completed', 220);
  } else if (source === 'telegram_audit' || source === 'telegram') {
    layer = 'integration';
    lens = 'bridge';
    label = 'Telegram bridge audit';
    summary = truncate(message.content || 'Telegram event', 220);
  } else if (message.role === 'system' && lower.includes('session summary:')) {
    layer = 'memory';
    lens = 'recall';
    label = 'Compaction/session summary';
    summary = truncate(message.content, 220);
  } else if (message.role === 'system' && lower.includes('runtime')) {
    layer = 'runtime';
    lens = 'logic';
    label = 'Runtime state';
    summary = truncate(message.content, 220);
  } else if (message.role === 'assistant' && Array.isArray(message.metadata.tool_calls)) {
    layer = 'orchestration';
    lens = 'logic';
    label = 'Planner step';
    const callCount = message.metadata.tool_calls.length;
    summary =
      callCount > 0
        ? `Planned ${callCount} tool call${callCount > 1 ? 's' : ''}: ${tools.join(', ')}`
        : truncate(message.content || 'Assistant reasoning', 220);
  }

  return {
    id: message.id,
    message,
    layer,
    lens,
    label,
    summary,
    source,
    tools,
    timestamp: message.created_at,
    payload: parsedContent ?? message.content,
  };
}

type ChipTone = 'good' | 'warn' | 'danger' | 'info' | 'default';

const TONE_CYCLE: ChipTone[] = ['info', 'good', 'warn', 'danger'];

function toolTone(name: string): ChipTone {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  return TONE_CYCLE[Math.abs(hash) % TONE_CYCLE.length];
}

function lensTone(lens: OperationalLens): ChipTone {
  if (lens === 'input') return 'info';
  if (lens === 'recall') return 'good';
  if (lens === 'bridge') return 'danger';
  if (lens === 'action') return 'warn';
  return 'default';
}

function contextLayerTone(layer: string | null | undefined): ChipTone {
  const normalized = (layer ?? '').trim().toLowerCase();
  if (!normalized) return 'default';
  if (normalized === 'core' || normalized.includes('core')) return 'danger';
  if (
    normalized === 'policy' ||
    normalized.includes('policy') ||
    normalized.includes('guardrail')
  ) {
    return 'warn';
  }
  if (normalized === 'memory' || normalized.includes('memory')) return 'good';
  if (normalized === 'runtime' || normalized.includes('runtime')) return 'info';
  if (normalized === 'ingress' || normalized.includes('ingress')) return 'info';
  if (normalized === 'history' || normalized.includes('history')) return 'info';
  return 'default';
}

function lensToSide(lens: OperationalLens): 'left' | 'right' {
  if (lens === 'input' || lens === 'bridge') return 'left';
  return 'right';
}

type TimelineEntry =
  | {
      kind: 'event';
      key: string;
      event: ArchitectureEvent;
      side: 'left' | 'right';
    }
  | {
      kind: 'cluster';
      key: string;
      representative: ArchitectureEvent;
      events: ArchitectureEvent[];
      side: 'left' | 'right';
    };

type RenderTimelineEntry =
  | TimelineEntry
  | {
      kind: 'event';
      key: string;
      event: ArchitectureEvent;
      side: 'left' | 'right';
      parentClusterKey: string;
      clusterIndex: number;
      clusterLength: number;
    };

function pickClusterRepresentative(events: ArchitectureEvent[]): ArchitectureEvent {
  const assistantWithText = events.find(
    (item) => item.message.role === 'assistant' && (item.message.content || '').trim().length > 0,
  );
  if (assistantWithText) return assistantWithText;
  const assistant = events.find((item) => item.message.role === 'assistant');
  if (assistant) return assistant;
  return events[0];
}

function sortMessagesDesc(items: Message[]): Message[] {
  return [...items].sort((a, b) => toTimestamp(b.created_at) - toTimestamp(a.created_at));
}

function mergeMessages(existing: Message[], incoming: Message[]): Message[] {
  const byId = new Map<string, Message>();
  for (const item of [...existing, ...incoming]) byId.set(item.id, item);
  return sortMessagesDesc(Array.from(byId.values()));
}

function runtimeStatusLabel(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'unavailable';
  if (!runtime.runtime_exists) return 'missing';
  return runtime.active ? 'active' : 'idle';
}

function extractRuntimeContextPayload(message: Message): RuntimeContextPayload | null {
  if (extractSource(message.metadata) !== 'runtime_context') return null;
  const runContext = message.metadata.run_context;
  if (!isRecord(runContext)) return null;
  return {
    contextMessageId: message.id,
    timestamp: message.created_at,
    runContext,
    structured: isRecord(message.runtime_context_structured) ? message.runtime_context_structured : null,
  };
}

function mapRuntimeContextToUserMessages(messages: Message[]): Map<string, RuntimeContextPayload> {
  const ordered = [...messages].sort((a, b) => toTimestamp(a.created_at) - toTimestamp(b.created_at));
  const mapped = new Map<string, RuntimeContextPayload>();
  let pendingUserMessageId: string | null = null;

  for (const message of ordered) {
    if (message.role === 'user') {
      pendingUserMessageId = message.id;
      continue;
    }

    const context = extractRuntimeContextPayload(message);
    if (context) {
      if (pendingUserMessageId && !mapped.has(pendingUserMessageId)) {
        mapped.set(pendingUserMessageId, context);
        pendingUserMessageId = null;
      }
    }
  }
  return mapped;
}

function promptCategoryFromTitle(title: string): PromptSectionCategory {
  const normalized = title.toLowerCase();
  if (normalized.includes('memory (pinned)')) return 'pinned';
  if (normalized.includes('memory') || normalized.includes('summary')) return 'memory';
  if (normalized.includes('telegram') || normalized.includes('integration')) return 'integration';
  if (normalized.includes('policy') || normalized.includes('guardrail') || normalized.includes('routing')) {
    return 'policy';
  }
  return 'core';
}

function parsePromptSections(prompt: string | null | undefined): PromptSection[] {
  if (!prompt) return [];
  const text = prompt.replace(/\r\n/g, '\n').trim();
  if (!text) return [];

  const sections: PromptSection[] = [];
  const parts = text.split('\n## ');

  const first = parts[0]?.trim();
  if (first) {
    sections.push({
      title: 'Core Context',
      content: first,
      category: 'core',
    });
  }

  for (let index = 1; index < parts.length; index += 1) {
    const block = parts[index].trim();
    if (!block) continue;
    const [rawTitle, ...bodyLines] = block.split('\n');
    const title = rawTitle.trim();
    const content = bodyLines.join('\n').trim();
    if (!title || !content) continue;
    sections.push({
      title,
      content,
      category: promptCategoryFromTitle(title),
    });
  }

  return sections;
}

function promptCategoryTone(category: PromptSectionCategory): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (category === 'pinned') return 'good';
  if (category === 'memory') return 'info';
  if (category === 'policy') return 'warn';
  if (category === 'integration') return 'default';
  return 'default';
}

function extractStructuredLayers(structured: Record<string, unknown> | null): Record<string, unknown>[] {
  const layers = structured?.layers;
  if (!Array.isArray(layers)) return [];
  return layers.filter((item): item is Record<string, unknown> => isRecord(item));
}

function extractLayerMemoryBlocks(layer: Record<string, unknown>): Record<string, unknown>[] {
  const memoryBlocks = layer.memory_blocks;
  if (!Array.isArray(memoryBlocks)) return [];
  return memoryBlocks.filter((item): item is Record<string, unknown> => isRecord(item));
}

function extractLayerHistoryMessages(layer: Record<string, unknown>): Record<string, unknown>[] {
  const historyMessages = layer.history_messages;
  if (!Array.isArray(historyMessages)) return [];
  return historyMessages.filter((item): item is Record<string, unknown> => isRecord(item));
}

function asCount(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const normalized = Math.trunc(value);
  return normalized >= 0 ? normalized : null;
}

function assembleSystemContext(runContext: Record<string, unknown> | null | undefined): string | null {
  const blocks = runContext?.system_messages;
  if (!Array.isArray(blocks)) return null;
  const lines = blocks
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  if (lines.length === 0) return null;
  return lines.join('\n\n---\n\n');
}

function runtimeContextErrorText(context: RuntimeContextPayload | null): string {
  if (!context) {
    return [
      'Runtime context snapshot is missing for this message.',
      'Raw prompt cannot be rendered in strict mode.',
      'No fallback is applied.',
    ].join('\n');
  }

  return [
    `Runtime context snapshot ${context.contextMessageId} is present but missing run_context.system_messages.`,
    'Raw prompt cannot be rendered in strict mode.',
    'No fallback is applied.',
  ].join('\n');
}

function isInjectedFullMemory(block: Record<string, unknown>): boolean {
  return block.injected_full === true;
}

function normalizedMemoryCategory(block: Record<string, unknown>): string {
  const category = block.category;
  if (typeof category === 'string' && category.trim()) return category;
  return 'uncategorized';
}

function renderMemoryReferenceCard(block: Record<string, unknown>, key: string): JSX.Element {
  const title =
    typeof block.title === 'string' && block.title.trim()
      ? block.title.trim()
      : 'Untitled Memory';
  const summary =
    typeof block.summary === 'string' && block.summary.trim()
      ? block.summary.trim()
      : null;
  const source =
    typeof block.source === 'string' && block.source.trim()
      ? block.source.trim().replace(/_/g, ' ')
      : 'memory reference';
  const depth =
    typeof block.depth === 'number' && Number.isFinite(block.depth)
      ? block.depth
      : null;
  const importance =
    typeof block.importance === 'number' && Number.isFinite(block.importance)
      ? block.importance
      : null;
  const memoryId =
    typeof block.memory_id === 'string' && block.memory_id.trim()
      ? block.memory_id.trim()
      : null;
  const rootId =
    typeof block.root_id === 'string' && block.root_id.trim()
      ? block.root_id.trim()
      : null;

  return (
    <div key={key} className="rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 p-3 space-y-2">
      <div className="flex items-center gap-2 min-w-0">
        <Brain size={12} className="text-[color:var(--text-muted)] shrink-0" />
        <span className="text-xs font-medium text-[color:var(--text-primary)] truncate">{title}</span>
        <StatusChip label={source} tone="default" className="h-4 text-[8px] ml-auto" />
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <StatusChip label={normalizedMemoryCategory(block)} tone="info" className="h-4 text-[8px]" />
        {depth !== null && (
          <StatusChip label={`depth ${depth}`} tone="default" className="h-4 text-[8px]" />
        )}
        {importance !== null && (
          <StatusChip label={`importance ${importance}`} tone="default" className="h-4 text-[8px]" />
        )}
      </div>
      <p className="text-xs text-[color:var(--text-secondary)] leading-relaxed">
        {summary ?? 'No summary available for this memory reference.'}
      </p>
      {(memoryId || rootId) && (
        <p className="text-[10px] font-mono text-[color:var(--text-muted)] break-all">
          {memoryId ? `memory=${memoryId}` : ''}
          {memoryId && rootId ? ' ' : ''}
          {rootId ? `root=${rootId}` : ''}
        </p>
      )}
    </div>
  );
}

type ContextLayersSectionProps = {
  structured: Record<string, unknown> | null;
  label?: string;
  userMessage?: string | null;
  runConfig?: Record<string, unknown> | null;
  className?: string;
};

type ExplorerEntry =
  | { kind: 'user_message'; id: string; title: string; content: string }
  | { kind: 'run_config'; id: string; title: string; config: Record<string, unknown> }
  | { kind: 'layer'; id: string; layer: Record<string, unknown>; index: number };

function ContextLayersSection({
  structured,
  label = 'Context Snapshot (Layered)',
  userMessage,
  runConfig,
  className = "",
}: ContextLayersSectionProps) {
  const layers = useMemo(() => extractStructuredLayers(structured), [structured]);
  const entries = useMemo<ExplorerEntry[]>(() => {
    const items: ExplorerEntry[] = [];
    if (typeof userMessage === 'string' && userMessage.trim()) {
      items.push({
        kind: 'user_message',
        id: 'user_message',
        title: 'User Message',
        content: userMessage.trim(),
      });
    }
    if (runConfig) {
      items.push({
        kind: 'run_config',
        id: 'run_config',
        title: 'Execution Settings',
        config: runConfig,
      });
    }
    for (let index = 0; index < layers.length; index += 1) {
      items.push({
        kind: 'layer',
        id: `layer-${index}`,
        layer: layers[index],
        index,
      });
    }
    return items;
  }, [layers, runConfig, userMessage]);
  const [selectedIndex, setSelectedIndex] = useState(0);

  useEffect(() => {
    if (entries.length === 0) {
      setSelectedIndex(0);
      return;
    }
    if (selectedIndex >= entries.length) {
      setSelectedIndex(0);
    }
  }, [entries.length, selectedIndex]);

  const selectedEntry = entries[selectedIndex] ?? null;
  const selectedLayer = selectedEntry?.kind === 'layer' ? selectedEntry.layer : null;
  const selectedLayerMemoryBlocks = selectedLayer
    ? extractLayerMemoryBlocks(selectedLayer)
    : [];
  const selectedLayerHistoryMessages = selectedLayer
    ? extractLayerHistoryMessages(selectedLayer)
    : [];
  const selectedLayerKind =
    typeof selectedLayer?.kind === 'string'
      ? selectedLayer.kind.trim().toLowerCase()
      : '';
  const isHistoryLayer = selectedLayerKind === 'conversation_history';
  const selectedInjectedMemoryBlocks = selectedLayerMemoryBlocks.filter(isInjectedFullMemory);
  const selectedReferencedMemoryBlocks = selectedLayerMemoryBlocks.filter(
    (block) => !isInjectedFullMemory(block),
  );

  return (
    <section className={`p-4 flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <StatusChip label={label} tone="info" className="h-5" />
        <span className="text-[10px] font-mono text-[color:var(--text-muted)]">{entries.length}</span>
      </div>
      <div className="mb-4 space-y-2">
        <p className="text-[10px] text-[color:var(--text-muted)] leading-relaxed">
          Layered snapshot of the exact context assembled at run start.
        </p>
      </div>
      {entries.length === 0 ? (
        <p className="text-xs text-[color:var(--text-muted)]">No structured layers in this snapshot.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4 flex-1 overflow-hidden min-h-0">
          <div className="flex flex-col gap-1 overflow-y-auto pr-2 custom-scrollbar min-h-0 w-full">
            {entries.map((entry, index) => {
              const active = selectedIndex === index;
              const layerLabel =
                entry.kind === 'layer'
                  ? String(entry.layer.layer ?? 'system')
                  : entry.kind === 'run_config'
                    ? 'runtime'
                    : 'ingress';
              return (
                <div key={entry.id} className="w-full">
                  <button
                    type="button"
                    onClick={() => setSelectedIndex(index)}
                    className={[
                      'w-full rounded-md px-3 py-2 text-left transition-all duration-150',
                      active
                        ? 'bg-[color:var(--surface-0)] border border-[color:var(--border-strong)] shadow-sm'
                        : 'border border-transparent hover:bg-[color:var(--surface-0)]/50',
                    ].join(' ')}
                  >
                    <div className="flex items-center gap-2 flex-wrap mb-1 min-w-0">
                      <StatusChip
                        label={layerLabel}
                        tone={contextLayerTone(layerLabel)}
                        className="h-4 text-[8px]"
                      />
                    </div>
                    <p className={`text-xs font-medium truncate w-full ${active ? 'text-[color:var(--text-primary)]' : 'text-[color:var(--text-secondary)]'}`}>
                      {entry.kind === 'layer' ? String(entry.layer.title ?? `Layer ${entry.index + 1}`) : entry.title}
                    </p>
                  </button>
                </div>
              );
            })}
          </div>

          <div className="flex flex-col overflow-hidden min-h-0">
            <div className="flex-1 overflow-y-auto pl-4 custom-scrollbar min-h-0 border-l border-[color:var(--border-subtle)]">
              {selectedEntry ? (
                <div className="space-y-6">
                  {selectedEntry.kind === 'user_message' && (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 pb-2 border-b border-[color:var(--border-subtle)]">
                        <MessageSquare size={14} className="text-[color:var(--text-muted)]" />
                        <h4 className="text-sm font-medium text-[color:var(--text-primary)]">{selectedEntry.title}</h4>
                      </div>
                      <Markdown content={selectedEntry.content} compact muted />
                    </div>
                  )}

                  {selectedEntry.kind === 'run_config' && (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 pb-2 border-b border-[color:var(--border-subtle)]">
                        <Cpu size={14} className="text-[color:var(--text-muted)]" />
                        <h4 className="text-sm font-medium text-[color:var(--text-primary)]">{selectedEntry.title}</h4>
                      </div>
                      {(() => {
                        const entries = Object.entries(selectedEntry.config);
                        const contextOwnedKeys = new Set([
                          'system_messages',
                          'structured_context',
                          'pinned_memories',
                        ]);
                        const tools = Array.isArray(selectedEntry.config.tools)
                          ? selectedEntry.config.tools.filter((item): item is Record<string, unknown> => isRecord(item))
                          : [];

                        const visibleEntries = entries.filter(
                          ([k]) => !contextOwnedKeys.has(k) && k !== 'tools',
                        );
                        const scalarEntries = visibleEntries.filter(([_, v]) => typeof v !== 'object' || v === null);
                        const nestedEntries = visibleEntries.filter(([_, v]) => typeof v === 'object' && v !== null);

                        return (
                          <div className="space-y-4">
                            {scalarEntries.length > 0 && (
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                {scalarEntries.map(([k, v]) => (
                                  <div key={k} className="flex flex-col gap-1 p-3 rounded bg-[color:var(--surface-1)]">
                                    <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{k.replace(/_/g, ' ')}</span>
                                    <span className="text-xs font-mono font-medium text-[color:var(--text-primary)] break-all">{String(v ?? '—')}</span>
                                  </div>
                                ))}
                              </div>
                            )}

                            {tools.length > 0 && (
                              <div className="space-y-3">
                                <p className="text-[10px] uppercase tracking-widest text-[color:var(--text-muted)]">
                                  Tools ({tools.length})
                                </p>
                                {tools.map((tool, index) => {
                                  const name =
                                    typeof tool.name === 'string' && tool.name.trim()
                                      ? tool.name.trim()
                                      : `tool_${index + 1}`;
                                  const description =
                                    typeof tool.description === 'string' && tool.description.trim()
                                      ? tool.description.trim()
                                      : 'No description';
                                  const parameters = isRecord(tool.parameters) ? tool.parameters : null;
                                  const parameterProperties = parameters?.properties;
                                  const parameterKeys = isRecord(parameterProperties)
                                    ? Object.keys(parameterProperties)
                                    : [];
                                  const required = Array.isArray(parameters?.required)
                                    ? parameters.required.filter((item): item is string => typeof item === 'string')
                                    : [];

                                  return (
                                    <details key={`${name}-${index}`} className="group rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 overflow-hidden">
                                      <summary className="list-none cursor-pointer px-3 py-2 hover:bg-[color:var(--surface-2)] transition-colors flex items-start gap-2">
                                        <ChevronRight size={12} className="mt-0.5 text-[color:var(--text-muted)] transition-transform group-open:rotate-90" />
                                        <div className="min-w-0 flex-1 space-y-1">
                                          <div className="flex items-center gap-2 flex-wrap">
                                            <StatusChip label={name} tone={toolTone(name)} className="h-4 text-[8px]" />
                                            <span className="text-[10px] font-mono text-[color:var(--text-muted)]">
                                              {parameterKeys.length} params
                                              {required.length > 0 ? ` · ${required.length} required` : ''}
                                            </span>
                                          </div>
                                          <p className="text-xs text-[color:var(--text-secondary)] leading-relaxed">
                                            {description}
                                          </p>
                                          {parameterKeys.length > 0 && (
                                            <p className="text-[10px] font-mono text-[color:var(--text-muted)] truncate">
                                              {parameterKeys.slice(0, 8).join(', ')}
                                              {parameterKeys.length > 8 ? ' ...' : ''}
                                            </p>
                                          )}
                                        </div>
                                      </summary>
                                      {parameters && (
                                        <div className="border-t border-[color:var(--border-subtle)] p-2">
                                          <JsonBlock
                                            value={JSON.stringify(parameters, null, 2)}
                                            className="!border-0 !bg-transparent max-h-[260px] text-[10px]"
                                          />
                                        </div>
                                      )}
                                    </details>
                                  );
                                })}
                              </div>
                            )}

                            {nestedEntries.length > 0 && (
                              <div className="space-y-3">
                                <p className="text-[10px] uppercase tracking-widest text-[color:var(--text-muted)]">
                                  Other Nested Config
                                </p>
                                {nestedEntries.map(([k, v]) => (
                                  <details key={k} className="group rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 overflow-hidden">
                                    <summary className="list-none cursor-pointer px-3 py-2 hover:bg-[color:var(--surface-2)] transition-colors flex items-center gap-2">
                                      <ChevronRight size={12} className="text-[color:var(--text-muted)] transition-transform group-open:rotate-90" />
                                      <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                                        {k.replace(/_/g, ' ')}
                                      </span>
                                    </summary>
                                    <div className="border-t border-[color:var(--border-subtle)] p-2">
                                      <JsonBlock
                                        value={JSON.stringify(v, null, 2)}
                                        className="!border-0 !bg-transparent max-h-[260px] text-[10px]"
                                      />
                                    </div>
                                  </details>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}

                  {selectedEntry.kind === 'layer' && selectedLayer && (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 pb-2 border-b border-[color:var(--border-subtle)]">
                        <Layers size={14} className="text-[color:var(--text-muted)]" />
                        <h4 className="text-sm font-medium text-[color:var(--text-primary)]">
                          {String(selectedLayer.title ?? `Layer ${selectedEntry.index + 1}`)}
                        </h4>
                      </div>
                      
                      {typeof selectedLayer.explanation === 'string' && selectedLayer.explanation.trim().length > 0 && (
                        <div className="p-4 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 text-xs text-[color:var(--text-secondary)] leading-relaxed italic">
                          {selectedLayer.explanation}
                        </div>
                      )}

                      {isHistoryLayer ? (
                        selectedLayerHistoryMessages.length > 0 ? (
                          <div className="space-y-2">
                            {selectedLayerHistoryMessages.map((historyMessage, idx) => {
                              const role =
                                typeof historyMessage.role === 'string' && historyMessage.role.trim()
                                  ? historyMessage.role.trim()
                                  : 'unknown';
                              const preview =
                                typeof historyMessage.preview === 'string' && historyMessage.preview.trim()
                                  ? historyMessage.preview.trim()
                                  : '';
                              const source =
                                typeof historyMessage.source === 'string' && historyMessage.source.trim()
                                  ? historyMessage.source.trim()
                                  : null;
                              const toolName =
                                typeof historyMessage.tool_name === 'string' && historyMessage.tool_name.trim()
                                  ? historyMessage.tool_name.trim()
                                  : null;
                              const toolCallCount = asCount(historyMessage.tool_call_count);
                              const imageCount = asCount(historyMessage.image_count);
                              const textBlockCount = asCount(historyMessage.text_block_count);
                              const isError = historyMessage.is_error === true;
                              const toolCalls = Array.isArray(historyMessage.tool_calls)
                                ? historyMessage.tool_calls.filter((item): item is Record<string, unknown> => isRecord(item))
                                : [];

                              return (
                                <div
                                  key={`history-${idx}`}
                                  className="rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 p-3 space-y-2"
                                >
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <StatusChip
                                      label={role.replace(/_/g, ' ')}
                                      tone={roleTone(role)}
                                      className="h-4 text-[8px]"
                                    />
                                    {source && <StatusChip label={source} tone="default" className="h-4 text-[8px]" />}
                                    {toolName && <StatusChip label={toolName} tone={toolTone(toolName)} className="h-4 text-[8px]" />}
                                    {toolCallCount !== null && toolCallCount > 0 && (
                                      <StatusChip
                                        label={`${toolCallCount} tool call${toolCallCount === 1 ? '' : 's'}`}
                                        tone="warn"
                                        className="h-4 text-[8px]"
                                      />
                                    )}
                                    {isError && <StatusChip label="error" tone="danger" className="h-4 text-[8px]" />}
                                  </div>

                                  {preview ? (
                                    <p className="text-xs text-[color:var(--text-secondary)] leading-relaxed whitespace-pre-wrap break-words">
                                      {preview}
                                    </p>
                                  ) : (
                                    <p className="text-[11px] text-[color:var(--text-muted)] italic">
                                      No text preview available.
                                    </p>
                                  )}

                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    {textBlockCount !== null && (
                                      <StatusChip
                                        label={`${textBlockCount} text block${textBlockCount === 1 ? '' : 's'}`}
                                        tone="default"
                                        className="h-4 text-[8px]"
                                      />
                                    )}
                                    {imageCount !== null && imageCount > 0 && (
                                      <StatusChip
                                        label={`${imageCount} image${imageCount === 1 ? '' : 's'}`}
                                        tone="info"
                                        className="h-4 text-[8px]"
                                      />
                                    )}
                                  </div>

                                  {toolCalls.length > 0 && (
                                    <div className="text-[10px] font-mono text-[color:var(--text-muted)] break-words">
                                      {toolCalls
                                        .map((call) => {
                                          const name = typeof call.name === 'string' ? call.name.trim() : '';
                                          const id = typeof call.id === 'string' ? call.id.trim() : '';
                                          if (name && id) return `${name} (${id})`;
                                          return name || id || null;
                                        })
                                        .filter((item): item is string => Boolean(item))
                                        .join(' • ')}
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <p className="text-xs text-[color:var(--text-muted)]">
                            No conversation history entries were captured for this run.
                          </p>
                        )
                      ) : selectedInjectedMemoryBlocks.length > 0 ? (
                        <div className="space-y-3">
                          {selectedInjectedMemoryBlocks.map((block, bIdx) => (
                            <details key={bIdx} className="group bg-[color:var(--surface-1)]/50 rounded border border-[color:var(--border-subtle)] overflow-hidden">
                              <summary className="list-none cursor-pointer px-4 py-3 hover:bg-[color:var(--surface-2)] transition-colors flex items-center justify-between">
                                <div className="flex items-center gap-3 min-w-0">
                                  <Brain size={14} className="text-[color:var(--text-muted)] shrink-0" />
                                  <span className="text-xs font-medium text-[color:var(--text-primary)] truncate">{String(block.title ?? 'Untitled Memory')}</span>
                                </div>
                                <ChevronRight size={14} className="text-[color:var(--text-muted)] transition-transform group-open:rotate-90" />
                              </summary>
                              <div className="p-5 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]">
                                <Markdown content={String(block.content ?? '')} compact muted />
                              </div>
                            </details>
                          ))}
                          {selectedReferencedMemoryBlocks.length > 0 && (
                            <div className="space-y-2 pt-2">
                              <p className="text-[10px] uppercase tracking-widest text-[color:var(--text-muted)]">
                                Linked Memory References ({selectedReferencedMemoryBlocks.length})
                              </p>
                              {selectedReferencedMemoryBlocks.map((block, bIdx) =>
                                renderMemoryReferenceCard(block, `linked-${bIdx}`),
                              )}
                            </div>
                          )}
                        </div>
                      ) : selectedReferencedMemoryBlocks.length > 0 ? (
                        <div className="space-y-2">
                          <p className="text-[10px] uppercase tracking-widest text-[color:var(--text-muted)]">
                            Memory References ({selectedReferencedMemoryBlocks.length})
                          </p>
                          {selectedReferencedMemoryBlocks.map((block, bIdx) =>
                            renderMemoryReferenceCard(block, `ref-${bIdx}`),
                          )}
                        </div>
                      ) : (
                        <Markdown content={typeof selectedLayer.content === 'string' ? selectedLayer.content : ''} compact muted />
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="h-full flex flex-col items-center justify-center text-[color:var(--text-muted)] gap-4 opacity-40">
                  <Layers size={48} />
                  <p className="text-sm font-bold uppercase tracking-widest">Select a trace layer</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

type UnifiedInspectorProps = {
  open: boolean;
  session: Session | null;
  userMessage?: Message | null;
  context: RuntimeContextPayload | null;
  lens?: OperationalLens;
  onClose: () => void;
};

function UnifiedInspectorModal({
  open,
  session,
  userMessage,
  context,
  onClose,
}: UnifiedInspectorProps) {
  const [tab, setTab] = useState<'layers' | 'raw'>('layers');

  useEffect(() => {
    if (open) setTab('layers');
  }, [open]);

  const rawPrompt = useMemo(() => assembleSystemContext(context?.runContext ?? null), [context]);

  const contextUsage = useMemo(() => {
    const runContext = context?.runContext;
    if (!runContext) {
      return { tokens: null as number | null, budget: null as number | null, percent: null as number | null };
    }

    const rawTokens = runContext.estimated_context_tokens;
    const rawBudget = runContext.context_token_budget;
    const rawPercent = runContext.estimated_context_percent;

    const tokens =
      typeof rawTokens === 'number' && Number.isFinite(rawTokens) && rawTokens >= 0
        ? Math.floor(rawTokens)
        : null;
    const budget =
      typeof rawBudget === 'number' && Number.isFinite(rawBudget) && rawBudget > 0
        ? Math.floor(rawBudget)
        : null;

    let percent: number | null = null;
    if (typeof rawPercent === 'number' && Number.isFinite(rawPercent)) {
      percent = Math.max(0, Math.min(100, Math.round(rawPercent)));
    } else if (tokens !== null && budget !== null) {
      percent = Math.max(0, Math.min(100, Math.round((tokens / budget) * 100)));
    }

    return { tokens, budget, percent };
  }, [context]);

  const tokenDotColor =
    contextUsage.percent === null
      ? 'bg-[color:var(--text-muted)]'
      : contextUsage.percent < 70
        ? 'bg-emerald-500'
        : contextUsage.percent < 90
          ? 'bg-amber-500'
          : 'bg-rose-500';
  const tokenLabel =
    contextUsage.tokens === null
      ? 'context tokens unavailable'
      : contextUsage.budget === null
        ? `${contextUsage.tokens.toLocaleString()} tokens`
        : `${contextUsage.tokens.toLocaleString()} / ${contextUsage.budget.toLocaleString()} tokens${contextUsage.percent !== null ? ` (${contextUsage.percent}%)` : ''}`;

  if (!open || !session) return null;

  const tabs = [
    { id: 'layers' as const, label: 'Context Snapshot' },
    { id: 'raw' as const, label: 'Raw Prompt' },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-5xl h-[85vh] rounded-xl border border-[color:var(--border-strong)] bg-[color:var(--surface-1)] shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-200">
        {/* Header */}
        <header className="px-6 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-4 min-w-0">
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] leading-none mb-1">
                {userMessage ? 'Execution Context' : 'Session Inspector'}
              </p>
              <p className="text-xs font-mono font-medium text-[color:var(--text-primary)] truncate">
                {userMessage ? `msg_${userMessage.id.slice(0, 8)}` : `session_${session.id.slice(0, 8)}`}
              </p>
            </div>
            <span className="flex items-center gap-1.5 text-[9px] font-mono text-[color:var(--text-muted)]">
              <span className={`w-1.5 h-1.5 rounded-full ${tokenDotColor}`} />
              {tokenLabel}
            </span>
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] inline-flex items-center justify-center transition-colors text-[color:var(--text-secondary)]"
            aria-label="Close inspector"
          >
            <X size={16} />
          </button>
        </header>

        {/* Tabs */}
        <div className="px-6 py-2 border-b border-[color:var(--border-subtle)] flex items-center gap-1 shrink-0">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest rounded transition-colors ${
                tab === t.id
                  ? 'bg-[color:var(--surface-1)] text-[color:var(--text-primary)] shadow-sm border border-[color:var(--border-subtle)]'
                  : 'border border-transparent text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]/50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden min-h-0">
          {tab === 'layers' && (
            <ContextLayersSection
              structured={context?.structured ?? null}
              label={userMessage ? "Message Context Snapshot (Layered)" : "Session Context Snapshot (Layered)"}
              userMessage={userMessage?.content}
              runConfig={context?.runContext ?? null}
              className="h-full animate-in fade-in duration-150"
            />
          )}

          {tab === 'raw' && (
            <div className="h-full overflow-y-auto p-6 custom-scrollbar animate-in fade-in duration-150">
              <div className="rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/30 p-6">
                {rawPrompt ? (
                  <Markdown content={rawPrompt} compact muted />
                ) : (
                  <div className="space-y-3">
                    <div className="inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-rose-500">
                      <AlertTriangle size={12} />
                      Raw Context Unavailable
                    </div>
                    <pre className="text-xs font-mono text-rose-400 whitespace-pre-wrap break-words">
                      {runtimeContextErrorText(context)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}

type EventDetailModalProps = {
  open: boolean;
  event: ArchitectureEvent | null;
  onClose: () => void;
};

function EventDetailModal({ open, event, onClose }: EventDetailModalProps) {
  if (!open || !event) return null;

  const payloadText = typeof event.payload === 'string' ? event.payload : JSON.stringify(event.payload, null, 2);
  const isJson = typeof event.payload === 'object' && event.payload !== null;
  const toolCalls = Array.isArray(event.message.metadata.tool_calls)
    ? event.message.metadata.tool_calls.filter((c): c is Record<string, unknown> => isRecord(c))
    : [];
  const lensInfo = [
    { id: 'input', label: 'Input', color: 'text-sky-500 border-sky-500/20 bg-sky-500/5' },
    { id: 'logic', label: 'Reasoning', color: 'text-indigo-500 border-indigo-500/20 bg-indigo-500/5' },
    { id: 'action', label: 'Actions', color: 'text-amber-500 border-amber-500/20 bg-amber-500/5' },
    { id: 'recall', label: 'Memory', color: 'text-emerald-500 border-emerald-500/20 bg-emerald-500/5' },
    { id: 'bridge', label: 'Bridges', color: 'text-rose-500 border-rose-500/20 bg-rose-500/5' },
  ].find(l => l.id === event.lens);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-3xl max-h-[80vh] rounded-xl border border-[color:var(--border-strong)] bg-[color:var(--surface-1)] shadow-2xl overflow-hidden flex flex-col">
        <header className="px-6 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm font-bold text-[color:var(--text-primary)]">{event.label}</span>
            <span className="text-[9px] font-mono text-[color:var(--text-muted)]">{formatCompactDate(event.timestamp)}</span>
            {lensInfo && (
              <span className={`px-2 py-0.5 rounded border text-[9px] font-bold uppercase tracking-widest ${lensInfo.color}`}>
                {lensInfo.label}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] inline-flex items-center justify-center transition-colors text-[color:var(--text-secondary)]"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-6 space-y-5 custom-scrollbar">
          {/* Tool calls — rendered as individual cards */}
          {toolCalls.length > 0 ? (
            <div className="space-y-3">
              {event.message.content && (
                <Markdown content={event.message.content} compact />
              )}
              {toolCalls.map((call, i) => {
                const name = typeof call.name === 'string' ? call.name : `call_${i}`;
                const rawArgs = call.arguments ?? call.input ?? call.params ?? null;
                const argsObj: Record<string, unknown> | null =
                  rawArgs && typeof rawArgs === 'string' ? safeJsonParse(rawArgs) as Record<string, unknown> | null
                  : isRecord(rawArgs) ? rawArgs
                  : null;
                return (
                  <div key={i} className="rounded-lg border border-[color:var(--border-subtle)] overflow-hidden">
                    <div className="px-4 py-2.5 bg-[color:var(--surface-1)] flex items-center gap-2 border-b border-[color:var(--border-subtle)]">
                      <Wrench size={12} className="text-[color:var(--text-muted)]" />
                      <StatusChip label={name} tone={toolTone(name)} className="text-[8px]" />
                    </div>
                    {argsObj ? (
                      <div className="px-4 py-3 space-y-2">
                        {Object.entries(argsObj).map(([k, v]) => (
                          <div key={k} className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{k}</span>
                            {typeof v === 'string' && v.length > 120 ? (
                              <p className="text-xs text-[color:var(--text-primary)] leading-relaxed whitespace-pre-wrap break-words">{v}</p>
                            ) : typeof v === 'object' && v !== null ? (
                              <pre className="text-[10px] font-mono text-[color:var(--text-secondary)] bg-[color:var(--surface-1)] rounded p-2 overflow-x-auto">{JSON.stringify(v, null, 2)}</pre>
                            ) : (
                              <span className="text-xs font-mono text-[color:var(--text-primary)]">{String(v ?? '—')}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : rawArgs ? (
                      <p className="px-4 py-3 text-xs text-[color:var(--text-secondary)] whitespace-pre-wrap break-words">{String(rawArgs)}</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : isJson && isRecord(event.payload) ? (
            <div className="space-y-3">
              {event.message.content && !isJson && (
                <Markdown content={event.message.content} compact />
              )}
              <div className="rounded-lg border border-[color:var(--border-subtle)] overflow-hidden">
                {event.tools.length > 0 && (
                  <div className="px-4 py-2.5 bg-[color:var(--surface-0)] flex items-center gap-2 border-b border-[color:var(--border-subtle)]">
                    <Zap size={12} className="text-[color:var(--text-muted)]" />
                    <StatusChip label={event.tools[0]} tone={toolTone(event.tools[0])} className="text-[8px]" />
                  </div>
                )}
                <div className="px-4 py-3 space-y-2">
                  {Object.entries(event.payload as Record<string, unknown>).map(([k, v]) => (
                    <div key={k} className="flex flex-col gap-0.5">
                      <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{k}</span>
                      {typeof v === 'string' && v.length > 120 ? (
                        <p className="text-xs text-[color:var(--text-primary)] leading-relaxed whitespace-pre-wrap break-words">{v}</p>
                      ) : typeof v === 'object' && v !== null ? (
                        <pre className="text-[10px] font-mono text-[color:var(--text-secondary)] bg-[color:var(--surface-0)] rounded p-2 overflow-x-auto max-h-[200px]">{JSON.stringify(v, null, 2)}</pre>
                      ) : (
                        <span className="text-xs font-mono text-[color:var(--text-primary)]">{String(v ?? '—')}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : event.message.role === 'tool_result' && event.message.content.trimStart().startsWith('{') ? (
            <div className="rounded-lg border border-[color:var(--border-subtle)] overflow-hidden">
              {event.tools.length > 0 && (
                <div className="px-4 py-2.5 bg-[color:var(--surface-0)] flex items-center gap-2 border-b border-[color:var(--border-subtle)]">
                  <Zap size={12} className="text-[color:var(--text-muted)]" />
                  <StatusChip label={event.tools[0]} tone={toolTone(event.tools[0])} className="text-[8px]" />
                </div>
              )}
              <pre className="px-4 py-3 text-[10px] font-mono text-[color:var(--text-secondary)] whitespace-pre-wrap break-all overflow-y-auto max-h-[400px]">{event.message.content}</pre>
            </div>
          ) : (
            <Markdown content={event.message.content || '[empty]'} compact />
          )}

          {/* Tool & source badges */}
          {(event.tools.length > 0 || event.source) && (
            <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-[color:var(--border-subtle)]">
              {event.tools.map(t => (
                <StatusChip key={t} label={t} tone={toolTone(t)} className="text-[8px]" />
              ))}
              {event.source && (
                <StatusChip label={event.source} tone={toolTone(event.source)} className="text-[8px]" />
              )}
            </div>
          )}

          <details className="group">
            <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] transition-colors flex items-center gap-2 py-2">
              <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
              Raw Metadata
            </summary>
            <div className="mt-2 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-2">
              <JsonBlock value={JSON.stringify(event.message.metadata, null, 2)} className="!border-0 !bg-transparent max-h-[300px] text-[10px]" />
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

export function LogsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [defaultSessionId, setDefaultSessionId] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runtimeBySession, setRuntimeBySession] = useState<Record<string, SessionRuntimeStatus>>({});

  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const [historyTab, setHistoryTab] = useState<SidebarTab>('sessions');
  const [sessionFilter, setSessionFilter] = useState('');
  const [isMultiSelectMode, setIsMultiSelectMode] = useState(false);
  const [selectedSessionIds, setSelectedSessionIds] = useState<string[]>([]);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [activeLenses, setActiveLenses] = useState<Set<OperationalLens>>(new Set());
  const [sourceFilter, setSourceFilter] = useState('all');
  
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingSessionTitle, setEditingSessionTitle] = useState('');
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);

  const sessionsInTab = useMemo(() => {
    return sessions.filter((s) => {
      if (historyTab === 'sub_agents') return Boolean(s.parent_session_id);
      return !s.parent_session_id;
    });
  }, [sessions, historyTab]);

  const filteredSessions = useMemo(() => {
    const f = sessionFilter.trim().toLowerCase();
    if (!f) return sessionsInTab;
    return sessionsInTab.filter((s) =>
      (s.title || '').toLowerCase().includes(f) || s.id.toLowerCase().includes(f),
    );
  }, [sessionsInTab, sessionFilter]);

  const selectableVisibleSessionIds = useMemo(() => {
    return filteredSessions
      .filter((session) => Boolean(defaultSessionId) && session.id !== defaultSessionId)
      .map((session) => session.id);
  }, [filteredSessions, defaultSessionId]);

  const allVisibleSelected = useMemo(() => {
    return (
      selectableVisibleSessionIds.length > 0 &&
      selectableVisibleSessionIds.every((id) => selectedSessionIds.includes(id))
    );
  }, [selectableVisibleSessionIds, selectedSessionIds]);

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
      setSessions((current) => current.filter((item) => item.id !== session.id));
      setSelectedSessionIds((current) => current.filter((id) => id !== session.id));
      if (selectedSessionId === session.id) {
        setSelectedSessionId(null);
      }
      toast.success('Session deleted');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete session');
    } finally {
      setDeletingSessionId(null);
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
        setSessions((current) => current.filter((session) => !deletedIds.includes(session.id)));
        setSelectedSessionIds((current) => current.filter((id) => !deletedIds.includes(id)));
        if (selectedSessionId && deletedIds.includes(selectedSessionId)) {
          setSelectedSessionId(null);
        }
      }

      if (failedCount === 0) {
        toast.success(`${deletedIds.length} sessions deleted`);
      } else {
        toast.error(`${failedCount} sessions could not be deleted`);
      }
    } finally {
      setDeletingSessionId(null);
    }
  }

  function startRenameSession(session: Session) {
    setEditingSessionId(session.id);
    setEditingSessionTitle((session.title || '').trim());
  }

  function cancelRenameSession() {
    setEditingSessionId(null);
    setEditingSessionTitle('');
  }

  async function submitRenameSession(session: Session) {
    const title = editingSessionTitle.trim();
    if (title === (session.title || '').trim()) {
      setEditingSessionId(null);
      return;
    }

    setRenamingSessionId(session.id);
    try {
      const updated = await api.patch<Session>(`/sessions/${session.id}`, {
        title: title.length > 0 ? title : null,
      });
      setSessions((current) =>
        current.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
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

  async function setMainSession(session: Session) {
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
      toast.success('Main session updated');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to set main session');
    }
  }

  const [inspector, setInspector] = useState<{
    session: Session | null;
    userMessage?: Message | null;
    context: RuntimeContextPayload | null;
    lens?: OperationalLens;
  } | null>(null);
  const [detailEvent, setDetailEvent] = useState<ArchitectureEvent | null>(null);
  const [runtimeExplorerOpen, setRuntimeExplorerOpen] = useState(false);
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const [collapsingClusters, setCollapsingClusters] = useState<Set<string>>(new Set());
  const collapseTimersRef = useRef<Record<string, number>>({});

  useEffect(() => {
    void loadSessions();
  }, []);

  useEffect(() => {
    if (!selectedSessionId) return;
    void loadMessages(selectedSessionId);
  }, [selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) return;
    const timer = window.setInterval(() => {
      void refreshMessages(selectedSessionId, true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selectedSessionId]);

  async function loadSessions() {
    const pageSize = 100;
    setLoadingSessions(true);
    try {
      let offset = 0;
      const all: Session[] = [];
      while (true) {
        const payload = await api.get<SessionListResponse>(
          `/sessions?limit=${pageSize}&offset=${offset}&include_sub_agents=true`,
        );
        const items = Array.isArray(payload?.items) ? payload.items : [];
        all.push(...items);
        if (items.length < pageSize) break;
        offset += pageSize;
      }

      const byId = new Map<string, Session>();
      for (const session of all) {
        if (!byId.has(session.id)) byId.set(session.id, session);
      }
      const ordered = Array.from(byId.values());
      const main = ordered.find((s) => s.is_main) || ordered[0];
      if (main) {
        setDefaultSessionId(main.id);
      }
      setSessions(ordered);
      setSelectedSessionId((current) => current ?? main?.id ?? null);
    } catch {
      toast.error('Failed to load sessions');
    } finally {
      setLoadingSessions(false);
    }
  }

  async function loadMessages(sessionId: string, silent = false) {
    if (!silent) setLoadingMessages(true);
    try {
      const [payload, runtimeStatus] = await Promise.all([
        api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`),
        api.get<SessionRuntimeStatus>(`/sessions/${sessionId}/runtime?action_limit=80`),
      ]);
      const items = Array.isArray(payload?.items) ? payload.items : [];
      setMessages(sortMessagesDesc(items));
      setHasMore(Boolean(payload?.has_more));
      setRuntimeBySession((current) => ({ ...current, [sessionId]: runtimeStatus }));
    } catch {
      if (!silent) toast.error('Failed to load logs');
    } finally {
      if (!silent) setLoadingMessages(false);
    }
  }

  async function refreshMessages(sessionId: string, silent = false) {
    try {
      const [payload, runtimeStatus] = await Promise.all([
        api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`),
        api.get<SessionRuntimeStatus>(`/sessions/${sessionId}/runtime?action_limit=80`),
      ]);
      const items = Array.isArray(payload?.items) ? payload.items : [];
      setMessages((current) => mergeMessages(current, items));
      setHasMore((current) => current || Boolean(payload?.has_more));
      setRuntimeBySession((current) => ({ ...current, [sessionId]: runtimeStatus }));
    } catch {
      if (!silent) toast.error('Failed to refresh logs');
    }
  }

  async function loadMoreLogs() {
    if (!selectedSessionId || !hasMore || loadingMore || messages.length === 0) return;
    const oldest = messages[messages.length - 1];
    setLoadingMore(true);
    try {
      const payload = await api.get<MessageListResponse>(
        `/sessions/${selectedSessionId}/messages?limit=100&before=${encodeURIComponent(oldest.id)}`,
      );
      const items = Array.isArray(payload?.items) ? payload.items : [];
      setMessages((current) => mergeMessages(current, items));
      setHasMore(Boolean(payload?.has_more));
    } catch {
      toast.error('Failed to load older logs');
    } finally {
      setLoadingMore(false);
    }
  }

  async function resolveContextForUserMessage(
    sessionId: string,
    userMessageId: string,
  ): Promise<RuntimeContextPayload | null> {
    let workingMessages = messages;
    let workingHasMore = hasMore;
    let context = mapRuntimeContextToUserMessages(workingMessages).get(userMessageId) ?? null;
    if (context) return context;
    if (!workingHasMore || workingMessages.length === 0) return null;

    setLoadingMore(true);
    try {
      let pagesLoaded = 0;
      while (
        !context &&
        workingHasMore &&
        workingMessages.length > 0 &&
        pagesLoaded < CONTEXT_AUTOFETCH_MAX_PAGES
      ) {
        const oldest = workingMessages[workingMessages.length - 1];
        const payload = await api.get<MessageListResponse>(
          `/sessions/${sessionId}/messages?limit=100&before=${encodeURIComponent(oldest.id)}`,
        );
        const items = Array.isArray(payload?.items) ? payload.items : [];
        workingHasMore = Boolean(payload?.has_more);
        if (items.length === 0) break;

        workingMessages = mergeMessages(workingMessages, items);
        context = mapRuntimeContextToUserMessages(workingMessages).get(userMessageId) ?? null;
        pagesLoaded += 1;
      }

      setMessages((current) => mergeMessages(current, workingMessages));
      setHasMore(workingHasMore);

      if (!context && workingHasMore && pagesLoaded >= CONTEXT_AUTOFETCH_MAX_PAGES) {
        toast.error('Context snapshot not found yet. Load more history and try again.');
      }
      return context;
    } catch {
      toast.error('Failed to load older logs for context lookup');
      return null;
    } finally {
      setLoadingMore(false);
    }
  }

  const activeSession = useMemo(
    () => sessions.find((item) => item.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );
  const activeRuntime = selectedSessionId ? runtimeBySession[selectedSessionId] ?? null : null;

  const loopContextByUserMessageId = useMemo(
    () => mapRuntimeContextToUserMessages(messages),
    [messages],
  );
  
  const allEvents = useMemo(
    () =>
      messages
        .filter((message) => extractSource(message.metadata) !== 'runtime_context')
        .map((message) => classifyMessage(message)),
    [messages],
  );

  const latestRuntimeContext = useMemo(() => {
    for (const message of messages) {
      const payload = extractRuntimeContextPayload(message);
      if (payload) return payload;
    }
    return null;
  }, [messages]);

  const sourceOptions = useMemo(() => {
    const set = new Set<string>();
    for (const event of allEvents) {
      if (event.source) set.add(event.source);
    }
    return Array.from(set).sort();
  }, [allEvents]);

  const filteredEvents = useMemo(() => {
    const query = search.trim().toLowerCase();
    return allEvents.filter((event) => {
      if (activeLenses.size > 0 && !activeLenses.has(event.lens)) return false;
      if (sourceFilter !== 'all' && event.source !== sourceFilter) return false;
      
      if (!query) return true;
      const haystack = [
        event.label,
        event.summary,
        event.message.content,
        event.source || '',
        event.tools.join(' '),
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [allEvents, activeLenses, search, sourceFilter]);

  const timelineEntries = useMemo<TimelineEntry[]>(() => {
    const entries: TimelineEntry[] = [];
    let index = 0;
    while (index < filteredEvents.length) {
      const current = filteredEvents[index];
      if (current.message.role === 'user') {
        entries.push({
          kind: 'event',
          key: current.id,
          event: current,
          side: lensToSide(current.lens),
        });
        index += 1;
        continue;
      }
      let end = index + 1;
      while (end < filteredEvents.length && filteredEvents[end].message.role !== 'user') {
        end += 1;
      }
      const block = filteredEvents.slice(index, end);
      if (block.length === 1) {
        entries.push({
          kind: 'event',
          key: block[0].id,
          event: block[0],
          side: lensToSide(block[0].lens),
        });
        index = end;
        continue;
      }
      const representative = pickClusterRepresentative(block);
      entries.push({
        kind: 'cluster',
        key: `cluster_${block[0].id}`,
        representative,
        events: block,
        side: lensToSide(representative.lens),
      });
      index = end;
    }
    return entries;
  }, [filteredEvents]);

  useEffect(() => {
    setExpandedClusters((current) => {
      if (current.size === 0) return current;
      const valid = new Set(
        timelineEntries
          .filter((entry): entry is Extract<TimelineEntry, { kind: 'cluster' }> => entry.kind === 'cluster')
          .map((entry) => entry.key),
      );
      const next = new Set(Array.from(current).filter((id) => valid.has(id)));
      if (next.size === current.size) {
        let unchanged = true;
        for (const id of next) {
          if (!current.has(id)) {
            unchanged = false;
            break;
          }
        }
        if (unchanged) return current;
      }
      return next;
    });
    setCollapsingClusters((current) => {
      if (current.size === 0) return current;
      const valid = new Set(
        timelineEntries
          .filter((entry): entry is Extract<TimelineEntry, { kind: 'cluster' }> => entry.kind === 'cluster')
          .map((entry) => entry.key),
      );
      const next = new Set(Array.from(current).filter((id) => valid.has(id)));
      if (next.size === current.size) return current;
      return next;
    });
  }, [timelineEntries]);

  const renderedTimelineEntries = useMemo<RenderTimelineEntry[]>(() => {
    const output: RenderTimelineEntry[] = [];
    for (const entry of timelineEntries) {
      if (
        entry.kind === 'cluster' &&
        (expandedClusters.has(entry.key) || collapsingClusters.has(entry.key))
      ) {
        entry.events.forEach((event, index) => {
          output.push({
            kind: 'event',
            key: `${entry.key}_${event.id}`,
            event,
            side: lensToSide(event.lens),
            parentClusterKey: entry.key,
            clusterIndex: index,
            clusterLength: entry.events.length,
          });
        });
        continue;
      }
      output.push(entry);
    }
    return output;
  }, [timelineEntries, expandedClusters, collapsingClusters]);

  useEffect(() => {
    return () => {
      for (const timer of Object.values(collapseTimersRef.current)) {
        window.clearTimeout(timer);
      }
      collapseTimersRef.current = {};
    };
  }, []);

  const lensCounts = useMemo(() => {
    const counts: Record<OperationalLens, number> = {
      input: 0,
      logic: 0,
      action: 0,
      recall: 0,
      bridge: 0,
    };
    for (const event of allEvents) {
      counts[event.lens] += 1;
    }
    return counts;
  }, [allEvents]);

  const toggleLens = (lens: OperationalLens) => {
    const next = new Set(activeLenses);
    if (next.has(lens)) next.delete(lens);
    else next.add(lens);
    setActiveLenses(next);
  };

  const LENSES: { id: OperationalLens; label: string; icon: any; color: string }[] = [
    { id: 'input', label: 'Input', icon: MessageSquare, color: 'text-sky-500' },
    { id: 'logic', label: 'Reasoning', icon: Cpu, color: 'text-indigo-500' },
    { id: 'action', label: 'Actions', icon: Wrench, color: 'text-amber-500' },
    { id: 'recall', label: 'Memory', icon: Brain, color: 'text-emerald-500' },
    { id: 'bridge', label: 'Bridges', icon: Network, color: 'text-rose-500' }
  ];

  const openEventDetails = async (
    event: ArchitectureEvent,
    e?: React.MouseEvent | React.KeyboardEvent,
  ) => {
    e?.preventDefault();
    e?.stopPropagation();
    if (event.lens === 'input') {
      let userContext = loopContextByUserMessageId.get(event.message.id) ?? null;
      if (!userContext && selectedSessionId) {
        userContext = await resolveContextForUserMessage(
          selectedSessionId,
          event.message.id,
        );
      }
      setInspector({
        session: activeSession,
        userMessage: event.message,
        context: userContext,
        lens: event.lens,
      });
      return;
    }
    setDetailEvent(event);
  };

  const toggleCluster = (clusterKey: string) => {
    const isOpen = expandedClusters.has(clusterKey);
    if (isOpen) {
      const cluster = timelineEntries.find(
        (entry): entry is Extract<TimelineEntry, { kind: 'cluster' }> =>
          entry.kind === 'cluster' && entry.key === clusterKey,
      );
      const maxStaggerSteps = Math.min(Math.max((cluster?.events.length ?? 1) - 1, 0), 6);
      const waitMs = 220 + maxStaggerSteps * 16 + 30;
      setCollapsingClusters((current) => {
        const next = new Set(current);
        next.add(clusterKey);
        return next;
      });
      const existing = collapseTimersRef.current[clusterKey];
      if (existing) window.clearTimeout(existing);
      collapseTimersRef.current[clusterKey] = window.setTimeout(() => {
        setExpandedClusters((current) => {
          const next = new Set(current);
          next.delete(clusterKey);
          return next;
        });
        setCollapsingClusters((current) => {
          const next = new Set(current);
          next.delete(clusterKey);
          return next;
        });
        delete collapseTimersRef.current[clusterKey];
      }, waitMs);
      return;
    }
    const existing = collapseTimersRef.current[clusterKey];
    if (existing) {
      window.clearTimeout(existing);
      delete collapseTimersRef.current[clusterKey];
    }
    setCollapsingClusters((current) => {
      if (!current.has(clusterKey)) return current;
      const next = new Set(current);
      next.delete(clusterKey);
      return next;
    });
    setExpandedClusters((current) => {
      const next = new Set(current);
      next.add(clusterKey);
      return next;
    });
  };

  const renderEventCard = (
    event: ArchitectureEvent,
    options?: {
      compact?: boolean;
      onClick?: (e?: React.MouseEvent | React.KeyboardEvent) => void;
      headerBadge?: JSX.Element | null;
    },
  ) => {
    const compact = options?.compact ?? false;
    const isJson = typeof event.payload === 'object' && event.payload !== null;
    const isUserIngressCard = event.message.role === 'user' && Boolean(options?.onClick);
    const cardToolCalls = Array.isArray(event.message.metadata.tool_calls)
      ? event.message.metadata.tool_calls.filter((c): c is Record<string, unknown> => isRecord(c))
      : [];
    const lensMap = LENSES.find((l) => l.id === event.lens) || LENSES[1];

    return (
      <div
        className={`rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shadow-sm hover:border-[color:var(--border-strong)] hover:shadow-md transition-all ${compact ? 'p-3' : 'p-4'} ${options?.onClick ? 'cursor-pointer' : ''}`}
        onClick={options?.onClick ? (e) => options.onClick?.(e) : undefined}
      >
        <div className={`flex items-center justify-between border-b border-[color:var(--border-subtle)]/50 ${compact ? 'mb-2 pb-2' : 'mb-3 pb-2'}`}>
          <span className={`font-bold uppercase tracking-widest text-[color:var(--text-primary)] ${compact ? 'text-[9px]' : 'text-[10px]'}`}>
            {event.label}
          </span>
          <div className="flex items-center gap-2">
            {options?.headerBadge}
            <span className="text-[9px] font-mono text-[color:var(--text-muted)]">
              {event.timestamp.split('T')[1].slice(0, 8)}
            </span>
          </div>
        </div>

        <div className={`font-medium text-[color:var(--text-primary)] leading-relaxed ${compact ? 'text-xs mb-2' : 'text-sm mb-3'}`}>
          {isJson && isRecord(event.payload) ? (
            <div className="space-y-1.5">
              {Object.entries(event.payload as Record<string, unknown>).slice(0, compact ? 3 : 4).map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-2 text-xs">
                  <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] shrink-0">{k}</span>
                  <span className="font-mono text-[11px] text-[color:var(--text-secondary)] truncate">{typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—')}</span>
                </div>
              ))}
            </div>
          ) : cardToolCalls.length > 0 ? (
            <div className="space-y-2">
              {event.message.content && (
                <p className="text-xs text-[color:var(--text-secondary)]">{event.summary}</p>
              )}
              {cardToolCalls.slice(0, compact ? 1 : 2).map((call, ci) => {
                const cName = typeof call.name === 'string' ? call.name : `call_${ci}`;
                const rawArgs = call.arguments ?? call.input ?? call.params ?? null;
                const argsObj = rawArgs && typeof rawArgs === 'string' ? safeJsonParse(rawArgs) : isRecord(rawArgs) ? rawArgs : null;
                const topKeys = isRecord(argsObj) ? Object.entries(argsObj).slice(0, compact ? 2 : 3) : [];
                return (
                  <div key={ci} className="rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/50 p-2 space-y-1">
                    <StatusChip label={cName} tone={toolTone(cName)} className="text-[8px]" />
                    {topKeys.map(([k, v]) => (
                      <div key={k} className="flex items-baseline gap-2 text-[11px]">
                        <span className="text-[8px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] shrink-0">{k}</span>
                        <span className="font-mono text-[10px] text-[color:var(--text-secondary)] truncate">{typeof v === 'string' ? truncate(v, 80) : String(v ?? '—')}</span>
                      </div>
                    ))}
                  </div>
                );
              })}
              {cardToolCalls.length > (compact ? 1 : 2) && (
                <p className="text-[9px] text-[color:var(--text-muted)]">+{cardToolCalls.length - (compact ? 1 : 2)} more</p>
              )}
            </div>
          ) : event.message.role === 'tool_result' && event.message.content.trimStart().startsWith('{') ? (
            <pre className="text-[10px] font-mono text-[color:var(--text-secondary)] bg-[color:var(--surface-2)] rounded p-2 overflow-hidden max-h-24 whitespace-pre-wrap break-all line-clamp-4">{event.summary}</pre>
          ) : (
            <div className={compact ? 'line-clamp-3' : 'line-clamp-4'}>
              <Markdown content={event.summary} compact />
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            {event.tools.map((t) => (
              <StatusChip key={t} label={t} tone={toolTone(t)} className="text-[8px]" />
            ))}
            {event.source && (
              <StatusChip label={event.source} tone={toolTone(event.source)} className="text-[8px]" />
            )}
            {event.tools.length === 0 && !event.source && (
              <StatusChip
                label={lensMap.label}
                tone={lensTone(event.lens)}
                className="text-[8px]"
              />
            )}
          </div>
          {isUserIngressCard && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                options?.onClick?.(e);
              }}
              className="inline-flex items-center gap-1 rounded border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-sky-400 hover:bg-sky-500/15"
            >
              <Layers size={10} />
              Explore Context
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <AppShell
      title="Control Plane"
      subtitle="Operational Diagnostics"
      actions={
        <div className="flex items-center gap-2">
          {activeSession && (
            <>
              <button
                onClick={() => setInspector({
                  session: activeSession,
                  context: latestRuntimeContext
                })}
                className="inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm"
              >
                <Cpu size={14} className="text-sky-500/80" />
                Latest Snapshot
              </button>
              <button
                onClick={() => setRuntimeExplorerOpen(true)}
                className="inline-flex h-9 items-center gap-2.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-secondary)] transition-all hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] active:scale-95 shadow-sm"
              >
                <Terminal size={14} className="text-amber-500/80" />
                Explore Runtime
              </button>
            </>
          )}
        </div>
      }
      contentClassName="h-full !p-0 overflow-hidden bg-[color:var(--app-bg)]"
    >
      <div className="flex h-full overflow-hidden">
        {/* IDE-Style Sidebar */}
        <aside className="w-64 border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex flex-col shrink-0">
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
            activeSessionId={selectedSessionId}
            onSessionClick={(id) => setSelectedSessionId(id)}
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
            loadingSessions={loadingSessions}
          />
        </aside>

        {/* Command Center Main */}
        <main className="flex-1 flex flex-col min-w-0 bg-[color:var(--app-bg)] relative">
          {!activeSession ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-[color:var(--text-muted)] gap-4">
              <Terminal size={32} className="opacity-20" />
              <p className="text-[10px] font-mono uppercase tracking-widest opacity-50">Awaiting Target Selection</p>
            </div>
          ) : (
            <>
              {/* Control Ribbon */}
              <header className="shrink-0 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] z-10">
                {/* Row 1: Identity & Primary Status */}
                <div className="px-6 h-14 flex items-center justify-between border-b border-[color:var(--border-subtle)]/30">
                  <div className="flex items-center gap-4 min-w-0">
                    <div className="flex items-center gap-2 shrink-0">
                      <div className={`w-2 h-2 rounded-full transition-all duration-500 ${activeRuntime?.active ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-[color:var(--border-strong)]'}`} />
                      <h2 className="text-sm font-bold text-[color:var(--text-primary)] uppercase tracking-wider truncate max-w-[400px]">
                        {activeSession.title || 'Live Process'}
                      </h2>
                    </div>
                    <div className="flex items-center gap-3 text-[10px] font-mono font-bold text-[color:var(--text-muted)] border-l border-[color:var(--border-subtle)] pl-4 truncate">
                      <span className="opacity-40">ID: {activeSession.id.slice(0, 12)}…</span>
                      <span className={`px-2 py-0.5 rounded-full border transition-colors ${activeRuntime?.active ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-600' : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]'} uppercase font-bold tracking-widest text-[8px]`}>
                        {runtimeStatusLabel(activeRuntime)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Row 2: Operational Lenses & Search */}
                <div className="px-6 py-2 bg-[color:var(--surface-1)]/30 flex items-center justify-between gap-6">
                  {/* Lenses Switcher */}
                  <div className="flex items-center gap-1 bg-[color:var(--surface-0)] border border-[color:var(--border-subtle)] p-0.5 rounded-md shadow-sm shrink-0">
                    {LENSES.map(lens => {
                      const isActive = activeLenses.has(lens.id);
                      return (
                        <button
                          key={lens.id}
                          onClick={() => toggleLens(lens.id)}
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded transition-all ${
                            isActive 
                              ? 'bg-[color:var(--surface-1)] text-[color:var(--text-primary)] shadow-sm border border-[color:var(--border-subtle)]' 
                              : 'border border-transparent text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]/50'
                          }`}
                        >
                          <lens.icon size={11} className={isActive ? lens.color : 'opacity-40'} />
                          <span className="text-[9px] font-bold uppercase tracking-wider">{lens.label}</span>
                          <span className={`text-[9px] font-mono ml-1 ${isActive ? 'text-[color:var(--text-primary)]' : 'opacity-30'}`}>{lensCounts[lens.id]}</span>
                        </button>
                      );
                    })}
                  </div>

                  {/* Search & Secondary Filters */}
                  <div className="flex-1 flex items-center gap-3 min-w-0 max-w-xl">
                    <div className="relative flex-1">
                      <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                      <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search trace activity..."
                        className="w-full h-8 pl-8 pr-3 text-[11px] bg-[color:var(--surface-0)] border border-[color:var(--border-subtle)] rounded-md outline-none focus:border-sky-500/40 transition-all font-mono"
                      />
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <select
                        value={sourceFilter}
                        onChange={(e) => setSourceFilter(e.target.value)}
                        className="h-8 px-2 bg-[color:var(--surface-0)] border border-[color:var(--border-subtle)] rounded text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-secondary)] outline-none cursor-pointer hover:border-[color:var(--border-strong)] transition-colors"
                      >
                        <option value="all">ALL SOURCES</option>
                        {sourceOptions.map(s => <option key={s} value={s}>{s}</option>)}
                      </select>

                      {(activeLenses.size > 0 || sourceFilter !== 'all' || search) && (
                        <button
                          onClick={() => {
                            setActiveLenses(new Set());
                            setSourceFilter('all');
                            setSearch('');
                          }}
                          className="h-8 px-3 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500/10 rounded-md transition-colors flex items-center gap-1.5"
                        >
                          <X size={12} />
                          Reset
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </header>

              {/* Diagnostic Stream (Timeline) */}
              <div className="flex-1 overflow-y-auto bg-[color:var(--surface-0)] custom-scrollbar">
                <div className="max-w-[1000px] mx-auto py-8 px-6">
                  {loadingMessages && messages.length === 0 && (
                    <div className="flex flex-col items-center justify-center py-20 text-[color:var(--text-muted)] gap-3">
                      <Loader2 size={16} className="animate-spin" />
                      <p className="text-[10px] font-mono uppercase tracking-widest">Streaming Trace...</p>
                    </div>
                  )}

                  {!loadingMessages && filteredEvents.length === 0 && (
                    <div className="flex flex-col items-center justify-center py-20 text-[color:var(--text-muted)] opacity-50">
                      <Terminal size={24} className="mb-2" />
                      <p className="text-[10px] font-mono uppercase tracking-widest">No events in current view</p>
                    </div>
                  )}

                  <div className="relative before:absolute before:inset-0 before:ml-4 before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-px before:bg-[color:var(--border-subtle)]">
                    {renderedTimelineEntries.map((entry, idx) => {
                      const event = entry.kind === 'event' ? entry.event : entry.representative;
                      const lensMap = LENSES.find((l) => l.id === event.lens) || LENSES[1];
                      const side = entry.side;
                      const isRight = side === 'right';
                      const prevSide = idx > 0 ? renderedTimelineEntries[idx - 1].side : side;
                      const isOpposite = idx > 0 && side !== prevSide;
                      const isCluster = entry.kind === 'cluster';
                      const isUserEvent = entry.kind === 'event' && entry.event.message.role === 'user';
                      const isExpandedClusterEvent = 'parentClusterKey' in entry;
                      const isCollapsingExpandedEvent =
                        isExpandedClusterEvent && collapsingClusters.has(entry.parentClusterKey);
                      const showCollapsePill =
                        isExpandedClusterEvent && entry.clusterIndex === entry.clusterLength - 1;
                      const rowZClass = isCluster || showCollapsePill
                        ? 'z-50'
                        : isUserEvent
                          ? 'z-40'
                          : 'z-20';
                      const expandedEventAnimClass =
                        isExpandedClusterEvent ? 'will-change-transform will-change-opacity' : '';
                      const expandedEventAnimDelay =
                        isExpandedClusterEvent
                          ? isCollapsingExpandedEvent
                            ? Math.min(entry.clusterLength - entry.clusterIndex - 1, 6) * 16
                            : Math.min(entry.clusterIndex, 6) * 28
                          : 0;
                      const expandedEventAnimStyle: React.CSSProperties | undefined = isExpandedClusterEvent
                        ? {
                            animation: isCollapsingExpandedEvent
                              ? 'logsClusterOut 220ms cubic-bezier(0.22,1,0.36,1) forwards'
                              : 'logsClusterIn 260ms cubic-bezier(0.22,1,0.36,1) both',
                            animationDelay: `${expandedEventAnimDelay}ms`,
                          }
                        : undefined;

                      return (
                        <div
                          key={entry.key}
                          className={`relative flex items-center justify-between md:justify-normal ${isRight ? 'md:flex-row-reverse' : ''} group pointer-events-none ${rowZClass}`}
                          style={idx === 0 ? undefined : { marginTop: isOpposite ? '-40px' : '24px' }}
                        >
                          <div className={`flex items-center justify-center w-8 h-8 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shrink-0 md:order-1 ${isRight ? 'md:-translate-x-1/2' : 'md:translate-x-1/2'} z-10 shadow-sm transition-transform group-hover:scale-110`}>
                            <lensMap.icon size={12} className={lensMap.color} />
                          </div>

                          <div className="w-[calc(100%-4rem)] md:w-[calc(50%-2.5rem)]">
                            {entry.kind === 'event' ? (
                              <div
                                className={`space-y-2 pointer-events-auto ${expandedEventAnimClass}`}
                                style={expandedEventAnimStyle}
                              >
                                {renderEventCard(event, {
                                  onClick: (e) => void openEventDetails(event, e),
                                  headerBadge:
                                    isExpandedClusterEvent && entry.clusterIndex === 0 ? (
                                      <span className="inline-flex items-center rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-emerald-300">
                                        Latest
                                      </span>
                                    ) : null,
                                })}
                                {showCollapsePill ? (
                                  <div className="relative z-50 flex justify-center -mt-1 pointer-events-auto">
                                    <button
                                      onClick={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        toggleCluster(entry.parentClusterKey);
                                      }}
                                      className="px-5 py-1.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] transition-all shadow-sm flex items-center gap-1.5 whitespace-nowrap"
                                    >
                                      <ChevronRight size={11} className="-rotate-90" />
                                      Collapse Trace
                                    </button>
                                  </div>
                                ) : null}
                              </div>
                            ) : (
                              <div className="space-y-2 pointer-events-auto">
                                <div className="relative pb-11">
                                  <div className="pointer-events-none absolute inset-x-0 top-0 z-0">
                                    {entry.events.slice(1, 4).map((shadowEvent, shadowIndex) => (
                                      <div
                                        key={shadowEvent.id}
                                        className="absolute inset-x-0"
                                        style={{
                                          top: `${(shadowIndex + 1) * 8}px`,
                                          left: `${(shadowIndex + 1) * 5}px`,
                                          right: `${(shadowIndex + 1) * 5}px`,
                                          opacity: Math.max(0.26, 0.54 - shadowIndex * 0.13),
                                          transform: `scale(${1 - (shadowIndex + 1) * 0.01})`,
                                        }}
                                      >
                                        <div className="h-24 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/70 shadow-sm" />
                                      </div>
                                    ))}
                                  </div>

                                  <div className="relative z-10 pointer-events-auto">
                                    {renderEventCard(entry.representative, {
                                      onClick: (e) => void openEventDetails(entry.representative, e),
                                      headerBadge: (
                                        <span className="inline-flex items-center rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-emerald-300">
                                          Latest
                                        </span>
                                      ),
                                    })}
                                  </div>

                                  <div className="absolute left-1/2 bottom-0 z-50 -translate-x-1/2 pointer-events-auto">
                                    <button
                                      onClick={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        toggleCluster(entry.key);
                                      }}
                                      className="px-5 py-1.5 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] transition-all shadow-sm flex items-center gap-1.5 whitespace-nowrap"
                                    >
                                      <ChevronRight size={11} className="rotate-90" />
                                      Expand {entry.events.length} Step Trace
                                    </button>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {!loadingMessages && hasMore && (
                    <div className="pt-12 pb-8 flex justify-center">
                      <button
                        onClick={() => void loadMoreLogs()}
                        disabled={loadingMore}
                        className="px-6 py-2 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] transition-all shadow-sm flex items-center gap-2"
                      >
                        {loadingMore ? <Loader2 size={12} className="animate-spin" /> : <Clock size={12} />}
                        Fetch History
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </main>
      </div>

      <UnifiedInspectorModal
        open={Boolean(inspector)}
        session={inspector?.session ?? null}
        userMessage={inspector?.userMessage ?? null}
        context={inspector?.context ?? null}
        onClose={() => setInspector(null)}
      />

      <EventDetailModal
        open={Boolean(detailEvent)}
        event={detailEvent}
        onClose={() => setDetailEvent(null)}
      />

      <RuntimeExplorerModal
        open={runtimeExplorerOpen}
        session={activeSession ?? null}
        runtime={activeRuntime ?? null}
        onClose={() => setRuntimeExplorerOpen(false)}
      />

    </AppShell>
  );
}
