import {
  Bot,
  ChevronDown,
  CircleOff,
  Expand,
  History,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Square,
  Wand2,
  Wrench,
  X,
  Terminal,
  Globe,
  ExternalLink,
} from 'lucide-react';
import { FormEvent, useEffect, useMemo, useRef, useState, memo, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { SubAgentTaskModal } from '../components/SubAgentTaskModal';
import { SpawnSubAgentModal } from '../components/SpawnSubAgentModal';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { WS_BASE_URL } from '../lib/env';
import { formatCompactDate, toPrettyJson, truncate } from '../lib/format';
import { api } from '../lib/api';
import { useAuthStore } from '../store/auth-store';
import type {
  Message,
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

function statusTone(status: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  switch (status) {
    case 'active':
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
    case 'ended':
      return 'danger';
    default:
      return 'default';
  }
}

function sortMessages(items: Message[]) {
  return [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

function formatToolArguments(raw: string): string {
  if (!raw.trim()) return '';
  try {
    const parsed = JSON.parse(raw);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
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

// --- Memoized Components ---

const SessionRow = memo(({ session, isActive, onClick }: {
  session: Session; isActive: boolean; onClick: (id: string) => void;
}) => (
    <button
        onClick={() => onClick(session.id)}
        className={`w-full flex flex-col gap-1 p-3 rounded-lg text-left transition-colors duration-150 border ${
            isActive
                ? 'bg-[color:var(--surface-0)] shadow-sm border-[color:var(--border-strong)]'
                : 'hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)] border-transparent'
        }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold truncate">{session.title || 'Session'}</span>
        <StatusChip label={session.status} tone={
          session.status === 'active' ? 'good' : session.status === 'ended' ? 'danger' : 'default'
        } className="scale-75 origin-right" />
      </div>
      <span className="text-[10px] text-[color:var(--text-muted)]">{formatCompactDate(session.started_at)}</span>
    </button>
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

const MessageCard = memo(({ message }: { message: Message }) => {
  const isUser = message.role === 'user';
  const isToolResult = message.role === 'tool_result';
  const attachments = (message.metadata?.attachments as Array<{ base64: string }> | undefined) ?? [];
  const screenshotBase64 = isToolResult ? (attachments.find(a => a.base64)?.base64 ?? null) : null;
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
        className={`max-w-[90%] rounded-2xl px-4 py-1.5 text-xs shadow-sm border ${
          isUser
            ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent rounded-tr-none font-medium'
            : isToolResult
            ? 'bg-sky-500/5 border-sky-500/20 font-mono text-[12px] rounded-tl-none'
            : 'bg-[color:var(--surface-1)] border-[color:var(--border-subtle)] rounded-tl-none font-medium'
        }`}
      >
          {isToolResult && message.tool_name && (
              <div className="mb-2 flex items-center gap-1.5 border-b border-sky-500/10 pb-2">
                <Wrench size={12} className="text-sky-600 dark:text-sky-400" />
                <span className="font-bold uppercase tracking-wide text-sky-600 dark:text-sky-400">{message.tool_name}</span>
              </div>
          )}

          {isToolResult ? (
            screenshotBase64 ? (
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
              <details className="group">
                <summary className="cursor-pointer list-none flex items-center gap-2 text-sky-600 dark:text-sky-400">
                  <ChevronDown size={14} className="group-open:rotate-180 transition-transform" />
                  <span className="font-bold uppercase tracking-widest text-[10px]">Execution Telemetry</span>
                </summary>
                <div className="mt-3 overflow-auto">
                  <div className="markdown-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {message.content}
                    </ReactMarkdown>
                  </div>
                </div>
              </details>
            )
          ) : (
              <div className={`markdown-body ${isUser ? 'prose-invert' : ''}`}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
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
    return url
        .replace(/["']/g, '')
        .replace('localhost', '127.0.0.1')
        .trim();
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
  complete: boolean;
}

interface StreamingState {
  connection: WsConnectionState;
  isThinking: boolean;
  isStreaming: boolean;
  text: string;
  activeToolCalls: StreamingToolCall[];
  completedToolCalls: StreamingToolCall[];
  agentIteration: number;
  agentMaxIterations: number;
}

const defaultStreamingState: StreamingState = {
  connection: 'disconnected',
  isThinking: false,
  isStreaming: false,
  text: '',
  activeToolCalls: [],
  completedToolCalls: [],
  agentIteration: 0,
  agentMaxIterations: 0,
};

// --- Sub-Components ---

function StreamToolCard({ call, active }: { call: StreamingToolCall; active: boolean }) {
  const pretty = formatToolArguments(call.argumentsJson);

  return (
      <div className="flex flex-col gap-1.5 animate-in items-start w-full">
        <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
          tool_call • {active ? 'running' : 'complete'}
        </span>
        </div>
        <div className="w-full max-w-[90%] rounded-2xl rounded-tl-none border border-sky-500/20 bg-sky-500/5 px-4 py-1.5 text-[12px] shadow-sm">
          <div className="mb-2 flex items-center justify-between border-b border-sky-500/10 pb-2">
            <div className="flex items-center gap-2 font-mono font-bold text-sky-600 dark:text-sky-400">
              <Wrench size={14} />
              {call.name}
            </div>
            {active && <Loader2 size={12} className="animate-spin text-sky-500" />}
          </div>
          {pretty ? <JsonBlock value={pretty} className="bg-transparent border-none p-0 max-h-[200px]" /> : <p className="text-sky-500/60 italic">Preparing arguments...</p>}
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
  const [sessionFilter, setSessionFilter] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(routeSessionId ?? null);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem('sentinel-selected-model') ?? 'hint:normal',
  );
  const [maxIterations, setMaxIterations] = useState(50);

  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [composer, setComposer] = useState('');

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

  useEffect(() => {
    localStorage.setItem('sentinel-mode', mode);
  }, [mode]);

  useEffect(() => {
    localStorage.setItem('sentinel-selected-model', selectedModel);
  }, [selectedModel]);

  const [isCompacting, setIsCompacting] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [isResettingBrowser, setIsResettingBrowser] = useState(false);
  const [isBrowserFullscreen, setIsBrowserFullscreen] = useState(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const fullscreenFrameRef = useRef<HTMLIFrameElement | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const activeSessionIdRef = useRef<string | null>(routeSessionId ?? null);

  // Keep refs in sync so WS callbacks can read current values
  useEffect(() => { activeSessionIdRef.current = activeSessionId; }, [activeSessionId]);

  const streamBusy = streaming.isThinking || streaming.isStreaming || isCompacting;

  const activeSession = useMemo(
      () => sessions.find((session) => session.id === activeSessionId) ?? null,
      [sessions, activeSessionId],
  );

  const filteredSessions = useMemo(() => {
    const q = sessionFilter.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => `${s.title ?? ''} ${s.status}`.toLowerCase().includes(q));
  }, [sessions, sessionFilter]);

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
    if (liveView?.url) {
      console.log('--- DEBUG: RAW BROWSER URL FROM API ---');
      console.log(liveView.url);
      console.log('---------------------------------------');
    }
  }, [liveView?.url]);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      setTasks([]);
      setStreaming(defaultStreamingState);
      shouldAutoScrollRef.current = true;
      disconnectWs();
      return;
    }

    // Clear messages immediately to avoid showing stale content
    setMessages([]);
    setTasks([]);
    setStreaming(defaultStreamingState);

    shouldAutoScrollRef.current = true;
    void loadMessages(activeSessionId);
    void fetchTasks(activeSessionId);
    void connectWs(activeSessionId);

    return () => {
      disconnectWs();
    };
  }, [activeSessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (shouldAutoScrollRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, streaming.text, streaming.activeToolCalls.length, streaming.completedToolCalls.length]);

  // API Actions
  async function fetchSessions() {
    try {
      const [payload, defaultSession] = await Promise.all([
        api.get<SessionListResponse>('/sessions?limit=40&offset=0'),
        api.get<Session>('/sessions/default'),
      ]);
      setSessions((current) => {
        const exists = payload.items.find((s) => s.id === defaultSession.id);
        return exists ? payload.items : [defaultSession, ...payload.items];
      });
      if (!activeSessionId) {
        setActiveSessionId(defaultSession.id);
        navigate(`/sessions/${defaultSession.id}`, { replace: true });
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load sessions');
    }
  }

  async function fetchModels() {
    try {
      const payload = await api.get<ModelsResponse>('/models/');
      // Filter out hidden backward-compat aliases from the selector
      const visible = payload.models.filter(m => !m.hidden);
      setModels(visible);
      // Only use server default if user has no saved preference
      const saved = localStorage.getItem('sentinel-selected-model');
      if (!saved && payload.default) setSelectedModel(payload.default);
    } catch {
      setModels([
        { id: 'hint:fast', label: 'Fast', description: 'Quick responses' },
        { id: 'hint:normal', label: 'Normal', description: 'Balanced quality and speed' },
        { id: 'hint:hard', label: 'Deep Think', description: 'Extended reasoning' },
      ]);
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
      setSessions((current) =>
          [fresh, ...current.map((s) =>
              s.id === previousId ? { ...s, status: 'ended' } : s
          )]
      );
      setActiveSessionId(fresh.id);
      navigate(`/sessions/${fresh.id}`, { replace: true });
      toast.success('New session started. Memories preserved.');
    } catch {
      toast.error('Failed to reset session');
    }
  }

  async function loadMessages(sessionId: string, before?: string) {
    if (!before) setMessagesLoading(true);
    try {
      const path = before
          ? `/sessions/${sessionId}/messages?limit=50&before=${encodeURIComponent(before)}`
          : `/sessions/${sessionId}/messages?limit=50`;
      const payload = await api.get<MessageListResponse>(path);
      const fetched = sortMessages(payload.items);
      setHasMoreMessages(payload.has_more);

      setMessages((current) => {
        let next;
        if (!before) {
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
      if (!before) {
        setStreaming((prev) => ({
          ...prev,
          text: '',
          activeToolCalls: [],
          completedToolCalls: [],
          isThinking: false,
          isStreaming: false,
        }));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load messages');
    } finally {
      if (!before) setMessagesLoading(false);
    }
  }

  async function fetchTasks(sessionId: string) {
    setTasksLoading(true);
    try {
      const payload = await api.get<SubAgentTaskListResponse>(`/sessions/${sessionId}/sub-agents`);
      setTasks(payload.items);
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
        allowed_tools: [], // defaults to standard set in backend if empty or we can add logic to select tools
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
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
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

    const ws = new WebSocket(`${WS_BASE_URL}/ws/sessions/${sessionId}/stream?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptsRef.current = 0;
      setStreaming((current) => ({ ...current, connection: 'connected' }));
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as WsEvent;
        onStreamEvent(sessionId, payload);
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
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
        setMessages(sortMessages((event.history as Message[]) ?? []));
        break;
      case 'message_ack':
        setMessages((current) => {
          const messageId = (event.message_id as string | undefined)?.trim();
          if (!messageId) return current;
          if (current.some((item) => item.id === messageId)) return current;
          const createdAt = (event.created_at as string | undefined) || new Date().toISOString();
          const ackMessage: Message = {
            id: messageId,
            session_id: sessionId,
            role: 'user',
            content: (event.content as string) || '',
            metadata: { source: 'web' },
            token_count: null,
            tool_call_id: null,
            tool_name: null,
            created_at: createdAt,
          };
          return sortMessages([...current, ackMessage]);
        });
        break;
      case 'agent_thinking':
        setStreaming((current) => ({ ...current, isThinking: true, text: '', activeToolCalls: [], completedToolCalls: [], agentIteration: 0, agentMaxIterations: 0 }));
        break;
      case 'agent_progress':
        setStreaming((current) => ({ ...current, agentIteration: (event.iteration as number) ?? current.agentIteration, agentMaxIterations: (event.max_iterations as number) ?? current.agentMaxIterations }));
        break;
      case 'text_delta':
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: true, text: current.text + (event.delta ?? '') }));
        break;
      case 'toolcall_start':
        const call = { id: (event.tool_call as any)?.id ?? `tool-${Date.now()}`, name: (event.tool_call as any)?.name ?? 'unknown', argumentsJson: '', complete: false };
        setStreaming((current) => ({ ...current, isThinking: false, activeToolCalls: [...current.activeToolCalls, call] }));
        break;
      case 'toolcall_delta':
        setStreaming((current) => {
          const next = [...current.activeToolCalls];
          if (next.length) next[next.length - 1].argumentsJson += (event.delta ?? '');
          return { ...current, activeToolCalls: next };
        });
        break;
      case 'toolcall_end':
        setStreaming((current) => {
          if (!current.activeToolCalls.length) return current;
          const last = current.activeToolCalls[current.activeToolCalls.length - 1];
          return {
            ...current,
            activeToolCalls: current.activeToolCalls.slice(0, -1),
            completedToolCalls: [...current.completedToolCalls, { ...last, complete: true }],
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
        break;
      case 'done': {
        const stopReason = event.stop_reason as string | undefined;
        if (stopReason === 'tool_use') {
          // Intermediate done between iterations — just clear streaming text, keep progress bar
          setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, text: '' }));
        } else {
          // Final done — agent turn complete, reset everything
          setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false, agentIteration: 0, agentMaxIterations: 0 }));
          void loadMessages(sessionId);
        }
        break;
      }
      case 'error':
      case 'agent_error': {
        const raw = (event.error as string) || (event.message as string) || 'Stream error';
        toast.error(humanizeAgentError(raw), { duration: 8000 });
        // Reset streaming state so UI doesn't stay stuck in "thinking" mode
        setStreaming((current) => ({ ...current, isThinking: false, isStreaming: false }));
        break;
      }
    }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = composer.trim();
    if (!content || streamBusy || !activeSessionId) return;

    setComposer('');
    shouldAutoScrollRef.current = true;

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      // Prepend time context if conversation has been idle for >30 minutes
      const lastMsg = messages.at(-1);
      const idleMs = lastMsg ? Date.now() - new Date(lastMsg.created_at).getTime() : 0;
      const now = new Date().toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
      const idleNote = idleMs > 30 * 60 * 1000
        ? `[Resuming after ${Math.round(idleMs / 60000)} min — current time: ${now}]\n\n`
        : '';
      wsRef.current.send(JSON.stringify({ type: 'message', content: idleNote + content, model: selectedModel, max_iterations: maxIterations }));
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
        toast.success('Nothing to compact yet (fewer than 10 messages)');
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
          subtitle={activeSession ? `ID: ${activeSession.id.slice(0, 8)} • ${activeSession.status}` : 'Operator Workspace'}
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

              {streamBusy && (
                  <button
                      onClick={stopCurrent}
                      disabled={isStopping}
                      className="btn-primary bg-rose-500 hover:bg-rose-600 h-9 px-3 gap-2 text-xs"
                  >
                    <Square size={14} fill="currentColor" />
                    Stop
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
                <div className="relative">
                  <History size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                  <input
                      className="input-field pl-8 h-8 text-xs"
                      placeholder="Search..."
                      value={sessionFilter}
                      onChange={(e) => setSessionFilter(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
                {filteredSessions.map((s) => (
                    <SessionRow key={s.id} session={s} isActive={s.id === activeSessionId} onClick={onSessionClick} />
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
              </div>

              <div className="flex items-center gap-4">
                {/* Context ring indicator */}
                {(() => {
                  // Estimate tokens: use stored token_count when available, fall back to chars/4
                  const estimatedTokens = messages.reduce((sum, m) => {
                    return sum + (m.token_count ?? Math.round((m.content?.length ?? 0) / 4));
                  }, 0);
                  // Practical usable context ceiling: ~150K tokens (200K window minus system prompts + tool schemas)
                  const CTX_CEILING = 150_000;
                  const fill = Math.min(estimatedTokens / CTX_CEILING, 1);
                  const pct = Math.round(fill * 100);
                  const r = 7;
                  const circ = 2 * Math.PI * r;
                  const dash = circ * fill;
                  const ringColor = fill < 0.5 ? '#10b981' : fill < 0.8 ? '#f59e0b' : '#ef4444';
                  const warn = fill >= 0.8;
                  const kTokens = estimatedTokens >= 1000 ? `${(estimatedTokens / 1000).toFixed(1)}k` : `${estimatedTokens}`;
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
                        <div><span className="font-bold" style={{ color: ringColor }}>~{kTokens}</span><span className="text-[color:var(--text-muted)]"> / 150k tokens</span></div>
                        <div className="text-[color:var(--text-muted)]">{messages.length} messages · {pct}% used</div>
                        {warn && <div className="text-amber-500 font-bold">Consider compacting context</div>}
                      </div>
                    </div>
                  );
                })()}

                <div className="w-px h-3 bg-[color:var(--border)]" />

                {/* --- Effort selector (Fast / Normal / Hard) --- */}
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Effort:</span>
                  <div className="flex items-center rounded-lg bg-[color:var(--surface-2)] p-0.5 gap-0.5">
                    {models.map(m => {
                      const active = selectedModel === m.id;
                      const tier = m.tier ?? 'normal';
                      const colors: Record<string, { text: string; bg: string }> = {
                        fast: { text: 'text-emerald-500', bg: 'bg-emerald-500/10 border-emerald-500/30' },
                        normal: { text: 'text-sky-500', bg: 'bg-sky-500/10 border-sky-500/30' },
                        hard: { text: 'text-amber-500', bg: 'bg-amber-500/10 border-amber-500/30' },
                      };
                      const c = colors[tier] ?? { text: 'text-[color:var(--text-primary)]', bg: 'bg-[color:var(--surface-0)]' };
                      return (
                        <button
                          key={m.id}
                          onClick={() => setSelectedModel(m.id)}
                          className={`px-2.5 h-6 text-[10px] font-bold uppercase tracking-widest rounded-md transition-all duration-200 border ${
                            active
                              ? `${c.bg} ${c.text} shadow-sm`
                              : 'border-transparent text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)]/50'
                          }`}
                        >
                          {m.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="w-px h-3 bg-[color:var(--border)]" />

                {/* --- Provider + Model (read-only, resolved from effort) --- */}
                {(() => {
                  const active = models.find(m => m.id === selectedModel);
                  if (!active?.primary_provider) return null;
                  const providerLabel = active.primary_provider === 'anthropic' ? 'Anthropic' : active.primary_provider === 'openai' ? 'OpenAI' : active.primary_provider;
                  const thinkLabel = active.thinking_budget ? `thinking:${(active.thinking_budget / 1000).toFixed(0)}k` : null;
                  const effortLabel = active.reasoning_effort ? `effort:${active.reasoning_effort}` : null;
                  return (
                    <div className="relative flex items-center gap-2 group cursor-default">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Provider:</span>
                        <span className="text-[10px] font-bold text-[color:var(--text-primary)]">{providerLabel}</span>
                        <span className="text-[9px] font-mono text-[color:var(--text-muted)] opacity-60">{active.primary_model}</span>
                        {thinkLabel && (
                          <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 border border-amber-500/20">{thinkLabel}</span>
                        )}
                      </div>
                      {/* Tooltip with full provider breakdown */}
                      <div className="absolute top-full right-0 mt-2 px-3 py-2.5 rounded-lg bg-[color:var(--surface-0)] border border-[color:var(--border)] text-[10px] font-mono whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-xl z-50 space-y-1.5">
                        <div className="font-bold uppercase tracking-wider text-[color:var(--text-muted)]">Provider Config</div>
                        <div><span className="text-[color:var(--text-muted)]">primary: </span><span className="font-bold text-[color:var(--text-primary)]">{active.primary_provider}/{active.primary_model}</span></div>
                        {active.fallback_provider && (
                          <div><span className="text-[color:var(--text-muted)]">fallback: </span><span className="font-bold text-[color:var(--text-primary)]">{active.fallback_provider}/{active.fallback_model}</span></div>
                        )}
                        {thinkLabel && <div><span className="text-[color:var(--text-muted)]">anthropic: </span><span className="text-amber-500 font-bold">{thinkLabel}</span></div>}
                        {effortLabel && <div><span className="text-[color:var(--text-muted)]">openai: </span><span className="text-amber-500 font-bold">{effortLabel}</span></div>}
                      </div>
                    </div>
                  );
                })()}

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

                    {messages
                      .filter(m => m.role !== 'system')
                      .filter(m => !(m.role === 'assistant' && !m.content?.trim() && !m.tool_name))
                      .map(m => <MessageCard key={m.id} message={m} />)}

                    {streaming.completedToolCalls.map(c => <StreamToolCard key={c.id} call={c} active={false} />)}
                    {streaming.activeToolCalls.map(c => <StreamToolCard key={c.id} call={c} active={true} />)}

                    {streaming.text && (
                        <div className="flex flex-col gap-1.5 animate-in items-start w-full">
                          <div className="flex items-center gap-2 px-1">
                        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-600 dark:text-sky-400">
                          assistant • streaming
                        </span>
                          </div>
                          <div className="max-w-[90%] rounded-2xl rounded-tl-none px-4 py-1.5 text-xs font-medium shadow-sm border bg-[color:var(--surface-1)] border-[color:var(--border-subtle)]">
                            <div className="markdown-body">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {streaming.text}
                              </ReactMarkdown>
                            </div>
                          </div>
                        </div>
                    )}

                    {streaming.isThinking && !streaming.text && streaming.activeToolCalls.length === 0 && (
                        <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] animate-pulse">
                          <Bot size={14} />
                          Sentinel is thinking...
                        </div>
                    )}
                  </>
              )}
            </div>

            {/* Composer */}
            <div className="p-4 border-t border-[color:var(--border-subtle)]">
              {activeSession?.status === 'ended' ? (
                  <div className="flex items-center justify-center gap-3 py-5 rounded-xl bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">
                    <CircleOff size={16} />
                    <span className="text-xs font-semibold">This session has ended — read only</span>
                  </div>
              ) : (
                  <>
                    <form onSubmit={sendMessage} className="relative group">
                    <textarea
                        value={composer}
                        onChange={(e) => setComposer(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendMessage(e as any);
                          }
                        }}
                        disabled={isCompacting}
                        placeholder={isCompacting ? 'Compacting context…' : 'Ask Sentinel anything...'}
                        className="input-field min-h-[100px] py-4 pr-14 resize-none text-[14px] leading-relaxed shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                      <button
                          type="submit"
                          disabled={!composer.trim() || streamBusy}
                          className={`absolute right-3 bottom-3 p-2 rounded-lg transition-all ${
                              composer.trim() && !streamBusy
                                  ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-md'
                                  : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] cursor-not-allowed'
                          }`}
                      >
                        <Send size={18} />
                      </button>
                    </form>
                    <div className="mt-2 flex items-center justify-between text-[10px] text-[color:var(--text-muted)] font-medium">
                      <p>Press Enter to send, Shift+Enter for new line</p>
                      <p>Realtime streaming enabled</p>
                    </div>
                  </>
              )}
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
                        <StatusChip label={t.status} tone={statusTone(t.status)} className="scale-75 origin-right" />
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
