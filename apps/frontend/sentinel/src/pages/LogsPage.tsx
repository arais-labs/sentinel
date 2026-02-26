import { useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate, truncate } from '../lib/format';
import type { Message, MessageListResponse, Session, SessionListResponse } from '../types/api';

type RoleFilter = 'all' | 'user' | 'assistant' | 'system' | 'tool_result';
type SessionKind = 'sub-agent' | 'session';

type PromptPopupData = {
  kind: 'prompt';
  sessionKind: SessionKind;
  initialPrompt: string | null;
  latestSystemPrompt: string | null;
};

type RuntimeContextPopupData = {
  kind: 'runtime_context';
  context: Record<string, unknown>;
};

type ContentPopupData = {
  kind: 'content';
  content: string;
};

type MetadataPopupData = {
  kind: 'metadata';
  metadata: Record<string, unknown>;
};

type PopupData = PromptPopupData | RuntimeContextPopupData | ContentPopupData | MetadataPopupData;
type PopupState = { title: string; subtitle?: string; data: PopupData } | null;

const ROLE_FILTERS: RoleFilter[] = ['all', 'assistant', 'tool_result', 'system', 'user'];

function toTimestamp(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sortMessagesDesc(items: Message[]): Message[] {
  return [...items].sort((a, b) => toTimestamp(b.created_at) - toTimestamp(a.created_at));
}

function roleTone(role: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (role === 'assistant') return 'good';
  if (role === 'tool_result') return 'warn';
  if (role === 'system') return 'info';
  if (role === 'user') return 'default';
  return 'default';
}

function extractSource(metadata: Record<string, unknown> | null | undefined): string | null {
  const source = metadata?.source;
  if (typeof source !== 'string') return null;
  const trimmed = source.trim();
  return trimmed || null;
}

function compactMetadata(metadata: Record<string, unknown>): Record<string, unknown> {
  const next: Record<string, unknown> = { ...metadata };
  const attachments = next.attachments;
  if (Array.isArray(attachments)) {
    next.attachments = attachments.map((entry) => {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return entry;
      const item = { ...(entry as Record<string, unknown>) };
      if (typeof item.base64 === 'string') {
        item.base64 = '[omitted]';
      }
      return item;
    });
  }
  return next;
}

function extractToolNames(message: Message): string[] {
  const names = new Set<string>();
  const direct = message.tool_name?.trim();
  if (direct) names.add(direct);

  const metadata = message.metadata ?? {};
  const metadataToolName = metadata.tool_name;
  if (typeof metadataToolName === 'string' && metadataToolName.trim()) {
    names.add(metadataToolName.trim());
  }

  const toolCalls = metadata.tool_calls;
  if (Array.isArray(toolCalls)) {
    toolCalls.forEach((call) => {
      if (!call || typeof call !== 'object' || Array.isArray(call)) return;
      const name = (call as Record<string, unknown>).name;
      if (typeof name === 'string' && name.trim()) {
        names.add(name.trim());
      }
    });
  }

  return Array.from(names);
}

function mergeMessages(existing: Message[], incoming: Message[]): Message[] {
  const byId = new Map<string, Message>();
  [...existing, ...incoming].forEach((message) => {
    byId.set(message.id, message);
  });
  return sortMessagesDesc(Array.from(byId.values()));
}

function sessionKind(session: Session): SessionKind {
  return session.parent_session_id ? 'sub-agent' : 'session';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function extractRunContext(metadata: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  if (!metadata) return null;
  if (extractSource(metadata) !== 'runtime_context') return null;
  const raw = metadata.run_context;
  return isRecord(raw) ? raw : null;
}

function toStringValue(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function toNumberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function toBooleanValue(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

type RuntimeTool = { name: string; description: string | null; parameters: Record<string, unknown> | null };
type RuntimeMemory = { title: string; content: string | null };

function parseRuntimeTools(context: Record<string, unknown>): RuntimeTool[] {
  const raw = context.tools;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((entry): entry is Record<string, unknown> => isRecord(entry))
    .map((entry) => ({
      name: toStringValue(entry.name) ?? 'unknown_tool',
      description: toStringValue(entry.description),
      parameters: isRecord(entry.parameters) ? entry.parameters : null,
    }));
}

function parseRuntimeMemories(context: Record<string, unknown>): RuntimeMemory[] {
  const raw = context.pinned_memories;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((entry): entry is Record<string, unknown> => isRecord(entry))
    .map((entry) => ({
      title: toStringValue(entry.title) ?? 'Untitled',
      content: toStringValue(entry.content),
    }));
}

function parseRuntimeSystemBlocks(context: Record<string, unknown>): string[] {
  const raw = context.system_messages;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => toStringValue(entry))
    .filter((entry): entry is string => Boolean(entry));
}

function renderPromptPopup(data: PromptPopupData) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <StatusChip label={data.sessionKind} tone={data.sessionKind === 'sub-agent' ? 'warn' : 'good'} />
      </div>
      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Initial Prompt</p>
        {data.initialPrompt?.trim() ? (
          <div className="markdown-body rounded-lg border border-sky-500/20 bg-[color:var(--surface-1)] p-3 text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.initialPrompt}</ReactMarkdown>
          </div>
        ) : (
          <p className="rounded-lg border border-sky-500/20 bg-[color:var(--surface-1)] p-3 text-xs text-[color:var(--text-muted)]">
            [not persisted yet]
          </p>
        )}
      </section>
      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">System Prompt</p>
        {data.latestSystemPrompt?.trim() ? (
          <div className="markdown-body rounded-lg border border-sky-500/20 bg-[color:var(--surface-1)] p-3 text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.latestSystemPrompt}</ReactMarkdown>
          </div>
        ) : (
          <p className="rounded-lg border border-sky-500/20 bg-[color:var(--surface-1)] p-3 text-xs text-[color:var(--text-muted)]">
            [not persisted yet]
          </p>
        )}
      </section>
    </div>
  );
}

function renderRuntimeContextPopup(context: Record<string, unknown>) {
  const modelHint = toStringValue(context.model_hint) ?? '[unknown]';
  const capturedAt = toStringValue(context.timestamp);
  const temperature = toNumberValue(context.temperature);
  const maxIterations = toNumberValue(context.max_iterations);
  const stream = toBooleanValue(context.stream);
  const tools = parseRuntimeTools(context);
  const memories = parseRuntimeMemories(context);
  const systemBlocks = parseRuntimeSystemBlocks(context);

  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Run Settings</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Model</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">{modelHint}</p>
          </div>
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Temperature</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">
              {temperature === null ? '[unknown]' : temperature}
            </p>
          </div>
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Max Iterations</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">
              {maxIterations === null ? '[unknown]' : maxIterations}
            </p>
          </div>
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Stream</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">
              {stream === null ? '[unknown]' : stream ? 'enabled' : 'disabled'}
            </p>
          </div>
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Pinned Memories</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">{memories.length}</p>
          </div>
          <div className="rounded-md border border-sky-500/25 bg-[color:var(--surface-1)] p-2">
            <p className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">Tools</p>
            <p className="mt-1 text-xs font-semibold text-[color:var(--text-primary)]">{tools.length}</p>
          </div>
        </div>
        {capturedAt ? (
          <p className="text-[11px] text-[color:var(--text-muted)]">Captured at: {formatCompactDate(capturedAt)}</p>
        ) : null}
      </section>

      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Pinned Memories</p>
        {memories.length === 0 ? (
          <p className="text-xs text-[color:var(--text-muted)]">No pinned memories in this snapshot.</p>
        ) : (
          <div className="space-y-2">
            {memories.map((memory, index) => (
              <details
                key={`${memory.title}-${index}`}
                className="rounded-md border border-sky-500/20 bg-[color:var(--surface-1)]"
              >
                <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-[color:var(--text-primary)]">
                  {memory.title}
                </summary>
                <pre className="border-t border-[color:var(--border-subtle)] px-3 py-2 whitespace-pre-wrap text-xs leading-relaxed text-[color:var(--text-secondary)]">
                  {memory.content || '[empty memory body]'}
                </pre>
              </details>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Tools</p>
        {tools.length === 0 ? (
          <p className="text-xs text-[color:var(--text-muted)]">No tool schema captured in this snapshot.</p>
        ) : (
          <div className="space-y-2">
            {tools.map((tool) => (
              <details
                key={tool.name}
                className="rounded-md border border-sky-500/20 bg-[color:var(--surface-1)]"
              >
                <summary className="cursor-pointer px-3 py-2">
                  <p className="text-xs font-semibold text-[color:var(--text-primary)]">{tool.name}</p>
                  <p className="mt-1 text-[11px] text-[color:var(--text-muted)]">
                    {truncate(tool.description || '[no description]', 120)}
                  </p>
                </summary>
                <div className="space-y-2 border-t border-[color:var(--border-subtle)] px-3 py-2">
                  {tool.description ? (
                    <p className="text-xs leading-relaxed text-[color:var(--text-secondary)]">{tool.description}</p>
                  ) : null}
                  <p className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">
                    Parameter Schema
                  </p>
                  <JsonBlock
                    value={tool.parameters ? JSON.stringify(tool.parameters, null, 2) : '{ }'}
                    className="max-h-[220px]"
                  />
                </div>
              </details>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-sky-500/30 bg-sky-500/5 p-3">
        <p className="text-xs font-bold uppercase tracking-wider text-[color:var(--text-muted)]">System Blocks</p>
        {systemBlocks.length === 0 ? (
          <p className="text-xs text-[color:var(--text-muted)]">No system blocks in this snapshot.</p>
        ) : (
          <div className="space-y-2">
            {systemBlocks.map((block, index) => (
              <details
                key={`system-block-${index}`}
                className="rounded-md border border-sky-500/20 bg-[color:var(--surface-1)]"
              >
                <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-[color:var(--text-primary)]">
                  {`Block ${index + 1}: ${truncate(block.split('\n')[0] || '[empty]', 96)}`}
                </summary>
                <div className="border-t border-[color:var(--border-subtle)] px-3 py-2">
                  <div className="markdown-body text-sm">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{block}</ReactMarkdown>
                  </div>
                </div>
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function renderPopupBody(data: PopupData) {
  if (data.kind === 'prompt') return renderPromptPopup(data);
  if (data.kind === 'runtime_context') return renderRuntimeContextPopup(data.context);
  if (data.kind === 'content') {
    return (
      <pre className="whitespace-pre-wrap rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3 text-xs leading-relaxed text-[color:var(--text-secondary)]">
        {data.content}
      </pre>
    );
  }
  return <JsonBlock value={JSON.stringify(data.metadata, null, 2)} className="max-h-[68vh]" />;
}

export function LogsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [toolFilter, setToolFilter] = useState<string>('all');
  const [toolOnly, setToolOnly] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [popup, setPopup] = useState<PopupState>(null);

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
    setLoadingSessions(true);
    try {
      const payload = await api.get<SessionListResponse>('/sessions?limit=100&offset=0&include_sub_agents=true');
      setSessions(payload.items);
      setSelectedSessionId((current) => current ?? payload.items[0]?.id ?? null);
    } catch {
      toast.error('Failed to load sessions');
    } finally {
      setLoadingSessions(false);
    }
  }

  async function loadMessages(sessionId: string, silent = false) {
    if (!silent) setLoadingMessages(true);
    try {
      const [payload, sessionDetail] = await Promise.all([
        api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`),
        api.get<Session>(`/sessions/${sessionId}`),
      ]);
      setMessages(sortMessagesDesc(payload.items));
      setHasMore(payload.has_more);
      setSessions((current) => current.map((item) => (item.id === sessionId ? sessionDetail : item)));
    } catch {
      if (!silent) toast.error('Failed to load logs for session');
    } finally {
      if (!silent) setLoadingMessages(false);
    }
  }

  async function refreshMessages(sessionId: string, silent = false) {
    if (!silent) setLoadingMessages(true);
    try {
      const [payload, sessionDetail] = await Promise.all([
        api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`),
        api.get<Session>(`/sessions/${sessionId}`),
      ]);
      setMessages((current) => mergeMessages(current, payload.items));
      setHasMore((current) => current || payload.has_more);
      setSessions((current) => current.map((item) => (item.id === sessionId ? sessionDetail : item)));
    } catch {
      if (!silent) toast.error('Failed to refresh logs for session');
    } finally {
      if (!silent) setLoadingMessages(false);
    }
  }

  async function loadMoreLogs() {
    if (!selectedSessionId || loadingMore || !hasMore || messages.length === 0) return;
    const oldest = messages[messages.length - 1];
    setLoadingMore(true);
    try {
      const payload = await api.get<MessageListResponse>(
        `/sessions/${selectedSessionId}/messages?limit=100&before=${oldest.id}`,
      );
      setMessages((current) => mergeMessages(current, payload.items));
      setHasMore(payload.has_more);
    } catch {
      toast.error('Failed to load older logs');
    } finally {
      setLoadingMore(false);
    }
  }

  const sourceOptions = useMemo(() => {
    const values = new Set<string>();
    messages.forEach((message) => {
      const source = extractSource(message.metadata);
      if (source) values.add(source);
    });
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [messages]);

  const toolOptions = useMemo(() => {
    const values = new Set<string>();
    messages.forEach((message) => {
      extractToolNames(message).forEach((name) => values.add(name));
    });
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [messages]);

  const filteredMessages = useMemo(() => {
    const query = search.trim().toLowerCase();
    return messages.filter((message) => {
      const toolNames = extractToolNames(message);
      if (roleFilter !== 'all' && message.role !== roleFilter) return false;
      const source = extractSource(message.metadata);
      if (sourceFilter !== 'all' && source !== sourceFilter) return false;
      if (toolOnly && toolNames.length === 0) return false;
      if (toolFilter !== 'all' && !toolNames.includes(toolFilter)) return false;
      if (!query) return true;
      const haystack = [
        message.content,
        message.role,
        message.tool_name ?? '',
        message.tool_call_id ?? '',
        source ?? '',
        toolNames.join(' '),
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [messages, roleFilter, search, sourceFilter, toolFilter, toolOnly]);

  const activeSession = sessions.find((session) => session.id === selectedSessionId) ?? null;

  const latestRunContextMessage = useMemo(
    () => messages.find((message) => extractRunContext(message.metadata) !== null) ?? null,
    [messages],
  );

  const latestRunContext = latestRunContextMessage
    ? extractRunContext(latestRunContextMessage.metadata)
    : null;

  return (
    <>
    <AppShell
      title="Logs"
      subtitle="Per-session runtime trace from DB messages"
      actions={
        <button
          onClick={() => {
            if (selectedSessionId) void refreshMessages(selectedSessionId);
          }}
          className="inline-flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-3 py-1.5 text-xs font-semibold hover:bg-[color:var(--surface-1)] transition-colors"
          disabled={!selectedSessionId || loadingMessages}
        >
          {loadingMessages ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
      }
      contentClassName="space-y-4"
    >
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Panel className="p-3 max-h-[calc(100vh-180px)] overflow-auto">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Sessions</h2>
            {loadingSessions ? <Loader2 size={14} className="animate-spin text-[color:var(--text-muted)]" /> : null}
          </div>
          <div className="space-y-1">
            {sessions.map((session) => {
              const active = session.id === selectedSessionId;
              return (
                <button
                  key={session.id}
                  onClick={() => setSelectedSessionId(session.id)}
                  className={`w-full rounded-md border px-2 py-2 text-left transition-colors ${
                    active
                      ? 'border-sky-500/40 bg-sky-500/10'
                      : 'border-transparent hover:border-[color:var(--border-subtle)] hover:bg-[color:var(--surface-1)]'
                  }`}
                >
                  <p className="text-xs font-semibold text-[color:var(--text-primary)]">
                    {truncate(session.title || session.id, 42)}
                  </p>
                  <div className="mt-1 flex items-center gap-2">
                    <StatusChip label={sessionKind(session)} tone={session.parent_session_id ? 'warn' : 'good'} />
                  </div>
                  <p className="mt-1 text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">
                    {formatCompactDate(session.started_at)}
                  </p>
                </button>
              );
            })}
            {!loadingSessions && sessions.length === 0 ? (
              <p className="text-xs text-[color:var(--text-muted)]">No sessions found.</p>
            ) : null}
          </div>
        </Panel>

        <Panel className="p-4">
          {activeSession ? (
            <Panel className="mb-3 p-3 bg-[color:var(--surface-1)]">
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip
                  label={sessionKind(activeSession)}
                  tone={activeSession.parent_session_id ? 'warn' : 'good'}
                />
                <span className="text-xs text-[color:var(--text-muted)]">{activeSession.id}</span>
                <button
                  onClick={() =>
                    setPopup({
                      title: 'Session Prompt Snapshot',
                      subtitle: activeSession.id,
                      data: {
                        kind: 'prompt',
                        sessionKind: sessionKind(activeSession),
                        initialPrompt: activeSession.initial_prompt || null,
                        latestSystemPrompt: activeSession.latest_system_prompt || null,
                      },
                    })
                  }
                  className="ml-auto inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)]"
                >
                  Open Prompts
                </button>
                {latestRunContext ? (
                  <button
                    onClick={() =>
                      setPopup({
                        title: 'Latest Runtime Context',
                        subtitle: latestRunContextMessage?.created_at
                          ? formatCompactDate(latestRunContextMessage.created_at)
                          : undefined,
                        data: {
                          kind: 'runtime_context',
                          context: latestRunContext,
                        },
                      })
                    }
                    className="inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)]"
                  >
                    Open Runtime Context
                  </button>
                ) : null}
              </div>
              <p className="mt-2 text-[11px] text-[color:var(--text-muted)]">
                Initial: {truncate(activeSession.initial_prompt?.trim() || '[not persisted yet]', 120)}
              </p>
              <p className="mt-1 text-[11px] text-[color:var(--text-muted)]">
                System: {truncate(activeSession.latest_system_prompt?.trim() || '[not persisted yet]', 120)}
              </p>
            </Panel>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search logs"
              className="h-9 min-w-[220px] flex-1 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-sm outline-none focus:border-sky-500/50"
            />
            <select
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value as RoleFilter)}
              className="h-9 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2 text-xs font-semibold uppercase"
            >
              {ROLE_FILTERS.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
            <select
              value={sourceFilter}
              onChange={(event) => setSourceFilter(event.target.value)}
              className="h-9 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2 text-xs font-semibold uppercase"
            >
              <option value="all">all sources</option>
              {sourceOptions.map((source) => (
                <option key={source} value={source}>
                  {source}
                </option>
              ))}
            </select>
            <select
              value={toolFilter}
              onChange={(event) => setToolFilter(event.target.value)}
              className="h-9 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2 text-xs font-semibold uppercase"
            >
              <option value="all">all tools</option>
              {toolOptions.map((tool) => (
                <option key={tool} value={tool}>
                  {tool}
                </option>
              ))}
            </select>
            <label className="inline-flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-2 py-1.5 text-[11px] uppercase tracking-wider text-[color:var(--text-muted)]">
              <input
                type="checkbox"
                checked={toolOnly}
                onChange={(event) => setToolOnly(event.target.checked)}
                className="h-3.5 w-3.5"
              />
              Tools
            </label>
            <label className="inline-flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-2 py-1.5 text-[11px] uppercase tracking-wider text-[color:var(--text-muted)]">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
                className="h-3.5 w-3.5"
              />
              Auto
            </label>
          </div>

          <div className="mt-3 flex items-center justify-between text-xs text-[color:var(--text-muted)]">
            <span>{activeSession ? `Session ${activeSession.id}` : 'Select a session'}</span>
            <span>{filteredMessages.length} log entries ({messages.length} loaded)</span>
          </div>

          <div className="mt-3 space-y-3 max-h-[calc(100vh-270px)] overflow-auto pr-1">
            {loadingMessages ? (
              <div className="flex items-center gap-2 text-sm text-[color:var(--text-muted)]">
                <Loader2 size={16} className="animate-spin" />
                Loading logs...
              </div>
            ) : null}

            {!loadingMessages && filteredMessages.length === 0 ? (
              <p className="text-sm text-[color:var(--text-muted)]">No logs match current filters.</p>
            ) : null}

            {filteredMessages.map((message) => {
              const source = extractSource(message.metadata);
              const toolNames = extractToolNames(message);
              const metadata = compactMetadata(message.metadata ?? {});
              const runtimeContext = extractRunContext(message.metadata);
              const contentText = message.content || '[empty]';
              const contentIsLong = contentText.length > 420;
              const contentPreview = contentIsLong ? `${contentText.slice(0, 420)}...` : contentText;
              return (
                <Panel key={message.id} className="p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusChip label={message.role} tone={roleTone(message.role)} />
                    {source ? <StatusChip label={source} tone="info" /> : null}
                    {toolNames.map((name) => (
                      <StatusChip key={`${message.id}-${name}`} label={name} tone="warn" />
                    ))}
                    <span className="ml-auto text-[11px] text-[color:var(--text-muted)]">{formatCompactDate(message.created_at)}</span>
                  </div>
                  <pre className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-[color:var(--text-secondary)]">
                    {contentPreview}
                  </pre>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() =>
                        setPopup({
                          title: 'Log Content',
                          subtitle: formatCompactDate(message.created_at),
                          data: {
                            kind: 'content',
                            content: contentText,
                          },
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]"
                    >
                      Open Content
                    </button>
                    <button
                      onClick={() =>
                        setPopup({
                          title: 'Log Metadata',
                          subtitle: formatCompactDate(message.created_at),
                          data: {
                            kind: 'metadata',
                            metadata,
                          },
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]"
                    >
                      Open Metadata
                    </button>
                    {runtimeContext ? (
                      <button
                        onClick={() =>
                          setPopup({
                            title: 'Runtime Context Snapshot',
                            subtitle: formatCompactDate(message.created_at),
                            data: {
                              kind: 'runtime_context',
                              context: runtimeContext,
                            },
                          })
                        }
                        className="inline-flex items-center gap-1 rounded-md border border-sky-500/30 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400 hover:bg-sky-500/10"
                      >
                        Open Runtime Context
                      </button>
                    ) : null}
                    {contentIsLong ? (
                      <span className="text-[10px] uppercase tracking-wider text-[color:var(--text-muted)]">
                        Preview truncated ({contentText.length} chars)
                      </span>
                    ) : null}
                  </div>
                </Panel>
              );
            })}

            {!loadingMessages && hasMore ? (
              <button
                onClick={() => void loadMoreLogs()}
                disabled={loadingMore}
                className="w-full rounded-md border border-[color:var(--border-subtle)] px-3 py-2 text-xs font-semibold uppercase tracking-wider text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] disabled:opacity-60"
              >
                {loadingMore ? 'Loading older logs...' : 'Load Older Logs'}
              </button>
            ) : null}
          </div>
        </Panel>
      </div>
    </AppShell>
    {popup ? (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
        <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setPopup(null)} />
        <Panel className="relative w-full max-w-5xl bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 max-h-[85vh] flex flex-col">
          <div className="px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex items-center justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider">{popup.title}</p>
              {popup.subtitle ? (
                <p className="text-[11px] text-[color:var(--text-muted)] mt-1">{popup.subtitle}</p>
              ) : null}
            </div>
            <button
              onClick={() => setPopup(null)}
              className="rounded-md border border-[color:var(--border-subtle)] p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-0)]"
            >
              <X size={16} />
            </button>
          </div>
          <div className="p-4 overflow-auto">
            {renderPopupBody(popup.data)}
          </div>
        </Panel>
      </div>
    ) : null}
    </>
  );
}
