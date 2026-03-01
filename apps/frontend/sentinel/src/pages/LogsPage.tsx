import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronRight,
  Clock,
  FileJson,
  HardDrive,
  History,
  Layers,
  Loader2,
  RefreshCw,
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
  SessionRuntimeCleanupResponse,
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

function lensToSide(lens: OperationalLens): 'left' | 'right' {
  if (lens === 'input' || lens === 'bridge') return 'left';
  return 'right';
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

function sessionChannelKind(session: Session): 'default' | 'telegram_group' | 'telegram_dm' {
  const title = (session.title ?? '').trim().toLowerCase();
  if (title.startsWith('tg group ·')) return 'telegram_group';
  if (title.startsWith('tg dm ·')) return 'telegram_dm';
  return 'default';
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
  let pending: RuntimeContextPayload | null = null;

  for (const message of ordered) {
    const context = extractRuntimeContextPayload(message);
    if (context) {
      pending = context;
      continue;
    }
    if (message.role === 'user' && pending) {
      mapped.set(message.id, pending);
      pending = null;
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

function isInjectedFullMemory(block: Record<string, unknown>): boolean {
  return block.injected_full === true;
}

function normalizedMemoryCategory(block: Record<string, unknown>): string {
  const category = block.category;
  if (typeof category === 'string' && category.trim()) return category;
  return 'uncategorized';
}

function estimateTokens(text: string | null | undefined): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

type ContextLayersSectionProps = {
  structured: Record<string, unknown> | null;
  label?: string;
  userMessage?: string | null;
  initialPrompt?: string | null;
  runConfig?: Record<string, unknown> | null;
  className?: string;
};

type ExplorerEntry =
  | { kind: 'user_message'; id: string; title: string; content: string }
  | { kind: 'initial_prompt'; id: string; title: string; content: string }
  | { kind: 'run_config'; id: string; title: string; config: Record<string, unknown> }
  | { kind: 'layer'; id: string; layer: Record<string, unknown>; index: number };

function ContextLayersSection({
  structured,
  label = 'Context Layers',
  userMessage,
  initialPrompt,
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
    if (typeof initialPrompt === 'string' && initialPrompt.trim()) {
      items.push({
        kind: 'initial_prompt',
        id: 'initial_prompt',
        title: 'Initial Prompt',
        content: initialPrompt.trim(),
      });
    }
    if (runConfig) {
      items.push({
        kind: 'run_config',
        id: 'run_config',
        title: 'Run Config',
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
  }, [initialPrompt, layers, runConfig, userMessage]);
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
  const selectedInjectedMemoryBlocks = selectedLayerMemoryBlocks.filter(isInjectedFullMemory);
  const selectedHasOnlyReferencedMemories =
    selectedLayerMemoryBlocks.length > 0 && selectedInjectedMemoryBlocks.length === 0;

  return (
    <section className={`p-4 flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <StatusChip label={label} tone="info" className="h-5" />
        <span className="text-[10px] font-mono text-[color:var(--text-muted)]">{entries.length}</span>
      </div>
      {entries.length === 0 ? (
        <p className="text-xs text-[color:var(--text-muted)]">No structured layers in this snapshot.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4 flex-1 overflow-hidden min-h-0">
          <div className="flex flex-col gap-1 overflow-y-auto pr-2 custom-scrollbar min-h-0 w-full">
            {entries.map((entry, index) => {
              const active = selectedIndex === index;
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
                      {entry.kind === 'user_message' && <StatusChip label="ingress" tone="info" className="h-4 text-[8px]" />}
                      {entry.kind === 'run_config' && <StatusChip label="runtime" tone="info" className="h-4 text-[8px]" />}
                      {entry.kind === 'initial_prompt' && <StatusChip label="core" tone="info" className="h-4 text-[8px]" />}
                      {entry.kind === 'layer' && <StatusChip label={String(entry.layer.layer ?? 'system')} tone="info" className="h-4 text-[8px]" />}
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

                  {selectedEntry.kind === 'initial_prompt' && (
                    <div className="space-y-4">
                      <div className="flex items-center gap-2 pb-2 border-b border-[color:var(--border-subtle)]">
                        <Terminal size={14} className="text-[color:var(--text-muted)]" />
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
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {Object.entries(selectedEntry.config)
                          .filter(([_, v]) => typeof v !== 'object' || v === null)
                          .map(([k, v]) => (
                          <div key={k} className="flex flex-col gap-1 p-3 rounded bg-[color:var(--surface-1)]">
                            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{k.replace(/_/g, ' ')}</span>
                            <span className="text-xs font-mono font-medium text-[color:var(--text-primary)] break-all">{String(v ?? '—')}</span>
                          </div>
                        ))}
                      </div>
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

                      {selectedInjectedMemoryBlocks.length > 0 ? (
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
                        </div>
                      ) : selectedHasOnlyReferencedMemories ? (
                        <p className="text-xs text-[color:var(--text-muted)] italic">
                          This layer contains memory references only.
                        </p>
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
  runtime: SessionRuntimeStatus | null;
  userMessage?: Message | null;
  context: RuntimeContextPayload | null;
  lens?: OperationalLens;
  onClose: () => void;
};

function UnifiedInspectorModal({
  open,
  session,
  runtime,
  userMessage,
  context,
  onClose,
}: UnifiedInspectorProps) {
  const [tab, setTab] = useState<'layers' | 'raw' | 'details'>('layers');

  const contextTokens = useMemo(() => {
    const system = session?.latest_system_prompt || '';
    const user = userMessage?.content || '';
    return estimateTokens(system + user);
  }, [session?.latest_system_prompt, userMessage?.content]);

  const tokenDotColor = contextTokens < 10000 ? 'bg-emerald-500' : contextTokens < 20000 ? 'bg-amber-500' : 'bg-rose-500';

  if (!open || !session) return null;

  const tabs = [
    { id: 'layers' as const, label: 'Layers' },
    { id: 'raw' as const, label: 'Raw Prompt' },
    { id: 'details' as const, label: 'Details' },
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
              {contextTokens.toLocaleString()} tokens
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
              label={userMessage ? "Message Context Layers" : "Latest Session Layers"}
              userMessage={userMessage?.content}
              initialPrompt={session.initial_prompt}
              runConfig={context?.runContext ?? null}
              className="h-full animate-in fade-in duration-150"
            />
          )}

          {tab === 'raw' && (
            <div className="h-full overflow-y-auto p-6 custom-scrollbar animate-in fade-in duration-150">
              <div className="rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/30 p-6">
                <Markdown content={session.latest_system_prompt || '[No system prompt captured]'} compact muted />
              </div>
            </div>
          )}

          {tab === 'details' && (
            <div className="h-full overflow-y-auto p-6 space-y-6 custom-scrollbar animate-in fade-in duration-150">
              {/* Environment State */}
              <section className="space-y-3">
                <div className="flex items-center gap-2 text-[color:var(--text-muted)]">
                  <Activity size={14} />
                  <h3 className="text-[10px] font-bold uppercase tracking-[0.2em]">Environment State</h3>
                </div>
                {runtime ? (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {[
                      { label: 'Status', value: runtimeStatusLabel(runtime), tone: (runtime.active ? 'good' : 'default') as 'good' | 'default' },
                      { label: 'Workspace', value: runtime.workspace_exists ? 'Ready' : 'Missing', tone: (runtime.workspace_exists ? 'good' : 'danger') as 'good' | 'danger' },
                      { label: 'Venv', value: runtime.venv_exists ? 'Ready' : 'Missing', tone: (runtime.venv_exists ? 'good' : 'danger') as 'good' | 'danger' },
                      { label: 'PID', value: runtime.active_pid?.toString() || 'None', tone: 'default' as 'default' },
                    ].map(stat => (
                      <div key={stat.label} className="flex items-center justify-between px-3 py-2 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
                        <span className="text-[10px] font-medium text-[color:var(--text-muted)]">{stat.label}</span>
                        <StatusChip label={stat.value} tone={stat.tone} className="h-5" />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="py-4 text-center rounded border border-dashed border-[color:var(--border-subtle)]">
                    <p className="text-[10px] uppercase tracking-widest text-[color:var(--text-muted)]">State unavailable</p>
                  </div>
                )}
              </section>

              {/* Session DB Record */}
              <details className="group">
                <summary className="cursor-pointer flex items-center gap-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] transition-colors py-1">
                  <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
                  <FileJson size={14} />
                  <h3 className="text-[10px] font-bold uppercase tracking-[0.2em]">Session DB Record</h3>
                </summary>
                <div className="mt-3 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-2">
                  <JsonBlock value={JSON.stringify(session, null, 2)} className="!border-0 !bg-transparent max-h-[300px] text-[10px]" />
                </div>
              </details>

              {/* Message DB Record */}
              {userMessage && (
                <details className="group">
                  <summary className="cursor-pointer flex items-center gap-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] transition-colors py-1">
                    <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
                    <History size={14} />
                    <h3 className="text-[10px] font-bold uppercase tracking-[0.2em]">Message DB Record</h3>
                  </summary>
                  <div className="mt-3 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-2">
                    <JsonBlock value={JSON.stringify(userMessage.metadata, null, 2)} className="!border-0 !bg-transparent max-h-[300px] text-[10px]" />
                  </div>
                </details>
              )}
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
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runtimeBySession, setRuntimeBySession] = useState<Record<string, SessionRuntimeStatus>>({});

  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingRuntimeAction, setLoadingRuntimeAction] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const [historyTab, setHistoryTab] = useState<SidebarTab>('sessions');
  const [search, setSearch] = useState('');
  const [activeLenses, setActiveLenses] = useState<Set<OperationalLens>>(new Set());
  const [sourceFilter, setSourceFilter] = useState('all');
  const [autoRefresh, setAutoRefresh] = useState(true);
  
  const [inspector, setInspector] = useState<{
    session: Session | null;
    runtime: SessionRuntimeStatus | null;
    userMessage?: Message | null;
    context: RuntimeContextPayload | null;
    lens?: OperationalLens;
  } | null>(null);
  const [detailEvent, setDetailEvent] = useState<ArchitectureEvent | null>(null);

  useEffect(() => {
    void loadSessions();
  }, []);

  useEffect(() => {
    if (!selectedSessionId) return;
    void loadMessages(selectedSessionId);
  }, [selectedSessionId]);

  useEffect(() => {
    if (!autoRefresh || !selectedSessionId) return;
    const timer = window.setInterval(() => {
      void refreshMessages(selectedSessionId, true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, selectedSessionId]);

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
      setSessions(ordered);
      setSelectedSessionId((current) => current ?? ordered[0]?.id ?? null);
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

  async function refreshRuntimeStatus(sessionId: string) {
    setLoadingRuntimeAction(true);
    try {
      const runtime = await api.get<SessionRuntimeStatus>(`/sessions/${sessionId}/runtime?action_limit=80`);
      setRuntimeBySession((current) => ({ ...current, [sessionId]: runtime }));
      toast.success('Runtime synced');
    } catch {
      toast.error('Failed to sync runtime');
    } finally {
      setLoadingRuntimeAction(false);
    }
  }

  async function cleanupRuntime(sessionId: string) {
    if (!window.confirm('Terminate and cleanup this session runtime workspace?')) return;
    setLoadingRuntimeAction(true);
    try {
      await api.post<SessionRuntimeCleanupResponse>(`/sessions/${sessionId}/runtime/cleanup`);
      toast.success('Runtime cleaned');
      await loadMessages(sessionId, true);
    } catch {
      toast.error('Cleanup failed');
    } finally {
      setLoadingRuntimeAction(false);
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

  const sessionsInTab = useMemo(() => {
    if (historyTab === 'sub_agents') return sessions.filter((item) => Boolean(item.parent_session_id));
    return sessions.filter((item) => !item.parent_session_id);
  }, [sessions, historyTab]);

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

  return (
    <AppShell
      title="Control Plane"
      subtitle="Operational Diagnostics"
      actions={
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer group px-3 py-1 rounded hover:bg-[color:var(--surface-1)] transition-colors">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
              className="w-3.5 h-3.5 rounded border-[color:var(--border-subtle)] text-[color:var(--text-primary)]"
            />
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] group-hover:text-[color:var(--text-primary)]">
              Live Tail
            </span>
          </label>
          <div className="h-4 w-px bg-[color:var(--border-subtle)] mx-1" />
          <button
            onClick={() => {
              if (selectedSessionId) void refreshMessages(selectedSessionId);
            }}
            className="btn-secondary h-8 px-4 text-[10px] font-bold uppercase tracking-widest flex items-center gap-2"
            disabled={!selectedSessionId}
          >
            <RefreshCw size={12} className={loadingMessages ? 'animate-spin' : ''} />
            Sync
          </button>
        </div>
      }
      contentClassName="h-full !p-0 overflow-hidden bg-[color:var(--app-bg)]"
    >
      <div className="flex h-full overflow-hidden">
        {/* IDE-Style Sidebar */}
        <aside className="w-64 border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex flex-col shrink-0">
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
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-0.5 custom-scrollbar">
            {loadingSessions ? (
              <div className="py-8 text-center">
                <Loader2 size={16} className="animate-spin mx-auto text-[color:var(--text-muted)]" />
              </div>
            ) : null}
            {sessionsInTab.map((session) => {
              const active = session.id === selectedSessionId;
              return (
                <button
                  key={session.id}
                  onClick={() => setSelectedSessionId(session.id)}
                  className={`w-full flex flex-col gap-1 p-3 rounded-lg text-left transition-colors duration-150 border ${
                    active
                      ? 'bg-[color:var(--surface-0)] shadow-sm border-[color:var(--border-strong)]'
                      : 'hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)] border-transparent'
                  }`}
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
              );
            })}
          </div>
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
                {/* Row 1: Identity & Primary Actions */}
                <div className="px-6 h-14 flex items-center justify-between border-b border-[color:var(--border-subtle)]/30">
                  <div className="flex items-center gap-4 min-w-0">
                    <div className="flex items-center gap-2 shrink-0">
                      <div className={`w-2 h-2 rounded-sm ${activeRuntime?.active ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.3)]' : 'bg-[color:var(--border-strong)]'}`} />
                      <h2 className="text-sm font-bold text-[color:var(--text-primary)] uppercase tracking-wider truncate max-w-[400px]">
                        {activeSession.title || 'Live Process'}
                      </h2>
                    </div>
                    <div className="flex items-center gap-3 text-[10px] font-mono text-[color:var(--text-muted)] border-l border-[color:var(--border-subtle)] pl-4 truncate">
                      <span className="opacity-70">id:{activeSession.id}</span>
                      <span className={`px-1.5 py-0.5 rounded border ${activeRuntime?.active ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-600' : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]'} uppercase font-bold tracking-widest text-[9px]`}>
                        {runtimeStatusLabel(activeRuntime)}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => setInspector({
                        session: activeSession,
                        runtime: activeRuntime,
                        context: latestRuntimeContext
                      })}
                      className="flex items-center gap-2 px-3 py-1.5 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-2)] text-[10px] font-bold uppercase tracking-widest transition-all"
                    >
                      <Cpu size={12} className="text-sky-500" />
                      Inspector
                    </button>
                    <div className="w-px h-4 bg-[color:var(--border-subtle)] mx-1" />
                    <button
                      onClick={() => void refreshRuntimeStatus(activeSession.id)}
                      className="p-1.5 rounded hover:bg-[color:var(--surface-1)] text-[color:var(--text-muted)] hover:text-sky-500 transition-colors"
                      disabled={loadingRuntimeAction}
                      title="Sync Runtime"
                    >
                      <RefreshCw size={14} className={loadingRuntimeAction ? 'animate-spin' : ''} />
                    </button>
                    <button
                      onClick={() => void cleanupRuntime(activeSession.id)}
                      className="p-1.5 rounded hover:bg-rose-500/10 text-[color:var(--text-muted)] hover:text-rose-500 transition-colors"
                      disabled={loadingRuntimeAction}
                      title="Cleanup Runtime"
                    >
                      <X size={16} />
                    </button>
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
                    {filteredEvents.map((event, idx) => {
                      const isJson = typeof event.payload === 'object' && event.payload !== null;
                      const cardToolCalls = Array.isArray(event.message.metadata.tool_calls)
                        ? event.message.metadata.tool_calls.filter((c): c is Record<string, unknown> => isRecord(c))
                        : [];

                      const eventLoopContext = event.message.role === 'user'
                        ? (loopContextByUserMessageId.get(event.message.id) ?? latestRuntimeContext)
                        : latestRuntimeContext;

                      const handleCardClick = (e?: React.MouseEvent | React.KeyboardEvent) => {
                        e?.preventDefault();
                        e?.stopPropagation();
                        if (event.lens === 'input') {
                          setInspector({
                            session: activeSession,
                            runtime: activeRuntime,
                            userMessage: event.message,
                            context: eventLoopContext,
                            lens: event.lens,
                          });
                        } else {
                          setDetailEvent(event);
                        }
                      };

                      const lensMap = LENSES.find(l => l.id === event.lens) || LENSES[1];
                      const side = lensToSide(event.lens);
                      const isRight = side === 'right';
                      const prevSide = idx > 0 ? lensToSide(filteredEvents[idx - 1].lens) : side;
                      const isOpposite = idx > 0 && side !== prevSide;

                      return (
                        <div
                          key={event.id}
                          className={`relative flex items-center justify-between md:justify-normal ${isRight ? 'md:flex-row-reverse' : ''} group`}
                          style={idx === 0 ? undefined : { marginTop: isOpposite ? '-40px' : '24px' }}
                        >
                          {/* Timeline Node */}
                          <div className={`flex items-center justify-center w-8 h-8 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shrink-0 md:order-1 ${isRight ? 'md:-translate-x-1/2' : 'md:translate-x-1/2'} z-10 shadow-sm transition-transform group-hover:scale-110`}>
                            <lensMap.icon size={12} className={lensMap.color} />
                          </div>

                          {/* Event Card */}
                          <div className="w-[calc(100%-4rem)] md:w-[calc(50%-2.5rem)] cursor-pointer" onClick={handleCardClick}>
                            <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shadow-sm hover:border-[color:var(--border-strong)] hover:shadow-md transition-all p-4">
                              <div className="flex items-center justify-between mb-3 border-b border-[color:var(--border-subtle)]/50 pb-2">
                                <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-primary)]">
                                  {event.label}
                                </span>
                                <span className="text-[9px] font-mono text-[color:var(--text-muted)]">
                                  {event.timestamp.split('T')[1].slice(0, 8)}
                                </span>
                              </div>

                              <div className="text-sm font-medium text-[color:var(--text-primary)] leading-relaxed mb-3">
                                {isJson && isRecord(event.payload) ? (
                                  <div className="space-y-1.5">
                                    {Object.entries(event.payload as Record<string, unknown>).slice(0, 4).map(([k, v]) => (
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
                                    {cardToolCalls.slice(0, 2).map((call, ci) => {
                                      const cName = typeof call.name === 'string' ? call.name : `call_${ci}`;
                                      const rawArgs = call.arguments ?? call.input ?? call.params ?? null;
                                      const argsObj = rawArgs && typeof rawArgs === 'string' ? safeJsonParse(rawArgs) : isRecord(rawArgs) ? rawArgs : null;
                                      const topKeys = isRecord(argsObj) ? Object.entries(argsObj).slice(0, 3) : [];
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
                                    {cardToolCalls.length > 2 && (
                                      <p className="text-[9px] text-[color:var(--text-muted)]">+{cardToolCalls.length - 2} more</p>
                                    )}
                                  </div>
                                ) : event.message.role === 'tool_result' && event.message.content.trimStart().startsWith('{') ? (
                                  <pre className="text-[10px] font-mono text-[color:var(--text-secondary)] bg-[color:var(--surface-2)] rounded p-2 overflow-hidden max-h-24 whitespace-pre-wrap break-all line-clamp-4">{event.summary}</pre>
                                ) : (
                                  <div className="line-clamp-4">
                                    <Markdown content={event.summary} compact />
                                  </div>
                                )}
                              </div>

                              <div className="flex items-center gap-1.5 flex-wrap">
                                {event.tools.map(t => (
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
                            </div>
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
        runtime={inspector?.runtime ?? null}
        userMessage={inspector?.userMessage ?? null}
        context={inspector?.context ?? null}
        onClose={() => setInspector(null)}
      />

      <EventDetailModal
        open={Boolean(detailEvent)}
        event={detailEvent}
        onClose={() => setDetailEvent(null)}
      />
    </AppShell>
  );
}
