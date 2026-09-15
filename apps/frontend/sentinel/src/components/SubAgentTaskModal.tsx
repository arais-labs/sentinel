import { SessionPreviewApprovals } from './session/SessionPreviewApprovals';
import { SessionPermissions } from './session/SessionPermissions';
import { SESSION_PERMISSIONS_CHANGED, type ApprovalScope } from '../lib/approvals';
import { X, Terminal, Clock, Activity, Hash, Target, Wrench, Trash2, MessageSquare, Loader2, ChevronDown } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notificationPublisher } from '../lib/notifications';

import { messageNotice } from '../lib/message-notice';
import { SessionMessageCard, buildToolArgumentsByCallId } from './session/SessionMessageCard';
import { createPortal } from 'react-dom';
import './sub-agent-task-modal.css';
import { StatusChip } from './ui/StatusChip';
import { approvalKey, type ApprovalRef } from '../lib/approvals';
import { formatCompactDate } from '../lib/format';
import { api } from '../lib/api';
import { formatAgentCost } from '../lib/agent-usage';
import type { Message, MessageListResponse, SessionContextUsage, SubAgentTask } from '../types/api';

const notify = notificationPublisher('Sub-agents');

interface SubAgentTaskModalProps {
  task: SubAgentTask;
  instanceName?: string | null;
  onClose: () => void;
  onTerminate?: (taskId: string) => void;
  isTerminating?: boolean;
}

function sortMessages(items: Message[]) {
  return [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

export function SubAgentTaskModal({ task, instanceName, onClose, onTerminate, isTerminating }: SubAgentTaskModalProps) {
  const [liveTask, setLiveTask] = useState<SubAgentTask>(task);
  const isRunning = liveTask.status === 'running' || liveTask.status === 'pending';
  const childSessionId = (liveTask.result?.child_session_id as string) ?? null;
  const toolAccessLabel = liveTask.allowed_tools.length > 0 ? `Scoped (${liveTask.allowed_tools.length})` : 'Full access';

  const [messages, setMessages] = useState<Message[]>([]);
  const [contextUsage, setContextUsage] = useState<SessionContextUsage | null>(null);
  const currentContext = contextUsage?.session_id === childSessionId ? contextUsage : null;
  const inputTokens = currentContext?.last_request_usage?.usage.input_tokens;
  const contextBudget = currentContext?.context_token_budget;
  const contextPercent = typeof inputTokens === 'number' && contextBudget && contextBudget > 0
    ? Math.round(inputTokens / contextBudget * 100) : null;
  const model = currentContext?.last_request_usage?.model || liveTask.model || '—';
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [resolvingApprovalKey, setResolvingApprovalKey] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taskPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const messagePollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const lastScrollTopRef = useRef(0);

  function isAtBottom(el: HTMLDivElement) {
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distance <= 20;
  }

  function onTranscriptScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const prevTop = lastScrollTopRef.current;
    const currentTop = el.scrollTop;
    const userScrolledUp = currentTop < prevTop - 2;
    lastScrollTopRef.current = currentTop;

    if (isAtBottom(el)) {
      shouldAutoScrollRef.current = true;
      return;
    }
    if (userScrolledUp) {
      shouldAutoScrollRef.current = false;
    }
  }

  const toolArgumentsByCallId = useMemo(() => buildToolArgumentsByCallId(messages), [messages]);
  const displayMessages = useMemo(
    () => messages
      .filter((m) => m.role !== 'system' || messageNotice(m))
      ,
    [messages],
  );

  useEffect(() => {
    setLiveTask(task);
  }, [task]);

  async function fetchTask() {
    try {
      const payload = await api.get<SubAgentTask>(`/sessions/${liveTask.session_id}/sub-agents/${liveTask.id}`);
      setLiveTask(payload);
    } catch {
      /* ignore */
    }
  }

  async function fetchMessages(sessionId: string) {
    try {
      const payload = await api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`);
      const sorted = sortMessages(payload.items);
      setMessages(sorted);
      if (shouldAutoScrollRef.current) {
        setTimeout(() => {
          const el = scrollRef.current;
          if (!el || !shouldAutoScrollRef.current) return;
          el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
          lastScrollTopRef.current = el.scrollTop;
        }, 50);
      }
    } catch { /* ignore */ }
  }

  const resolveApprovalInline = useCallback(async (approval: ApprovalRef, decision: 'approve' | 'reject', scope: ApprovalScope = 'once') => {
    const targetKey = approvalKey(approval);
    setResolvingApprovalKey(targetKey);
    try {
      await api.post(
        `/approvals/${encodeURIComponent(approval.provider)}/${encodeURIComponent(approval.approvalId)}/${decision}`,
        {
          scope,
          note: decision === 'approve'
            ? 'User approved action.'
            : 'User rejected action.',
        },
      );
      if (childSessionId) {
        await fetchMessages(childSessionId);
      }
      if (scope === 'session') window.dispatchEvent(new Event(SESSION_PERMISSIONS_CHANGED));
      notify.success(scope === 'session' ? 'Action allowed for this session' : decision === 'approve' ? 'Approved once' : 'Denied');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to resolve approval');
    } finally {
      setResolvingApprovalKey(null);
    }
  }, [childSessionId]);

  useEffect(() => {
    void fetchTask();
    if (taskPollRef.current) clearInterval(taskPollRef.current);
    if (isRunning || !childSessionId) {
      taskPollRef.current = setInterval(() => {
        void fetchTask();
      }, 1500);
    }
    return () => {
      if (taskPollRef.current) clearInterval(taskPollRef.current);
    };
  }, [liveTask.id, liveTask.session_id, isRunning, childSessionId]);

  useEffect(() => {
    if (!childSessionId) return;
    setLoadingMsgs(true);
    fetchMessages(childSessionId).finally(() => setLoadingMsgs(false));

    if (messagePollRef.current) clearInterval(messagePollRef.current);
    if (isRunning) {
      messagePollRef.current = setInterval(() => {
        void fetchMessages(childSessionId);
      }, 1500);
    }
    return () => {
      if (messagePollRef.current) clearInterval(messagePollRef.current);
    };
  }, [childSessionId, isRunning]);

  useEffect(() => {
    if (!childSessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refreshContext() {
      try {
        const usage = await api.get<SessionContextUsage>(`/sessions/${childSessionId}/context-usage`);
        if (!cancelled) setContextUsage(usage);
      } catch { /* Keep the last successful measurement. */ }
      if (!cancelled && isRunning) timer = setTimeout(refreshContext, 5000);
    }
    void refreshContext();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [childSessionId, isRunning]);

  return createPortal(
    <div className="sub-agent-overlay fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-xs" onClick={onClose} />
      <section role="dialog" aria-modal="true" aria-label="Sub-agent conversation" className="relative h-full max-h-[960px] w-full max-w-7xl rounded-3xl border border-(--border-subtle) bg-(--surface-0) shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col">

        {/* Header */}
        <div className="px-6 py-4 border-b border-(--border-subtle) bg-(--surface-1) shrink-0 space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="p-2 rounded-lg bg-(--surface-2) text-(--accent-solid)">
              <Terminal size={18} />
            </div>
              <div className="min-w-0 flex-1">
                <p className="text-[9px] text-(--text-muted) font-mono uppercase tracking-widest">Sub-Agent Run</p>
                <p className="mt-0.5 text-[10px] text-(--text-muted) font-mono">
                  task:{liveTask.id.slice(0, 8)} · parent:{liveTask.session_id.slice(0, 8)}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <StatusChip
                label={liveTask.status}
                tone={liveTask.status === 'completed' ? 'good' : liveTask.status === 'running' ? 'warn' : liveTask.status === 'failed' ? 'danger' : 'default'}
              />
              <button type="button" aria-label="Close sub-agent conversation" onClick={onClose} className="btn-secondary h-9 w-9 rounded-full p-0 text-(--text-muted)">
                <X size={20} />
              </button>
            </div>
          </div>

          <details className="group rounded-2xl border border-(--border-subtle) bg-(--surface-0) p-3">
            <summary className="flex cursor-pointer list-none items-center gap-1.5">
              <Target size={12} className="text-(--text-muted)" />
              <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Objective</span>
              <span className="min-w-0 flex-1 truncate text-xs text-(--text-secondary) group-open:hidden">{liveTask.name || '—'}</span>
              <ChevronDown size={14} className="ml-auto shrink-0 text-(--text-muted) transition-transform group-open:rotate-180" />
            </summary>
            <p tabIndex={0} aria-label="Objective" className="mt-2 max-h-[min(20vh,10rem)] overflow-y-auto overscroll-contain whitespace-pre-wrap text-sm leading-relaxed text-(--text-primary) wrap-anywhere">{liveTask.name || '—'}</p>
          </details>
        </div>

        <div className="flex flex-col md:flex-row flex-1 min-h-0">
          {/* Left: telemetry sidebar */}
          <aside aria-label="Run details" className="max-h-32 md:max-h-none md:w-64 shrink-0 border-b md:border-b-0 md:border-r border-(--border-subtle) bg-(--surface-1) overflow-y-auto p-4 space-y-5 wrap-anywhere">
            {/* Scope */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Target size={12} className="text-(--text-muted)" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Scope</span>
              </div>
              <p className="text-xs leading-relaxed text-(--text-secondary)">
                {liveTask.scope || 'No additional scope constraints.'}
              </p>
            </section>

            {/* Steps */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Activity size={12} className="text-(--text-muted)" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Telemetry</span>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-[10px]">
                  <span className="text-(--text-muted) font-bold uppercase">Steps</span>
                  <span className="font-mono font-bold">
                    {liveTask.turns_used}
                  </span>
                </div>
                <div className="flex justify-between gap-3 text-[10px]">
                  <span className="text-(--text-muted) font-bold uppercase">Cost</span>
                  <span className="text-amber-500 tabular-nums">{formatAgentCost(liveTask.usage)}</span>
                </div>
                <div className="flex justify-between text-[10px]">
                  <span className="text-(--text-muted) font-bold uppercase">Total tokens</span>
                  <span className="font-mono font-bold">{liveTask.tokens_used.toLocaleString()}</span>
                </div>
                <div className="flex justify-between gap-3 text-[10px]">
                  <span className="text-(--text-muted) font-bold uppercase">Model</span>
                  <span className="min-w-0 text-right font-mono">{model}</span>
                </div>
                <div className="space-y-1.5" title="Latest request input tokens as a share of the context budget">
                  <div className="flex justify-between gap-3 text-[10px]">
                    <span className="text-(--text-muted) font-bold uppercase">Context</span>
                    <span className="font-mono font-bold">{contextPercent === null ? '—' : `${contextPercent}%`}</span>
                  </div>
                  <div role="progressbar" aria-label="Context usage" aria-valuemin={0} aria-valuemax={100}
                    aria-valuenow={contextPercent === null ? undefined : Math.min(100, Math.max(0, contextPercent))}
                    aria-valuetext={contextPercent === null ? 'Unavailable' : `${contextPercent}% of context budget`}
                    className="h-1.5 overflow-hidden rounded-full bg-(--surface-2)">
                    <div className="h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none"
                      style={{ width: `${Math.min(100, Math.max(0, contextPercent ?? 0))}%`, backgroundColor: contextPercent !== null && contextPercent >= 80 ? '#f59e0b' : 'var(--accent-solid)' }} />
                  </div>
                  <div className="text-right text-[10px] text-(--text-muted) tabular-nums">
                    {typeof inputTokens === 'number' && contextBudget ? `${inputTokens.toLocaleString()} / ${contextBudget.toLocaleString()} tokens` : 'Awaiting first request'}
                  </div>
                </div>
                <div className="flex justify-between text-[10px]">
                  <span className="text-(--text-muted) font-bold uppercase">Tool Access</span>
                  <span className="font-mono font-bold">{toolAccessLabel}</span>
                </div>
              </div>
            </section>

            {/* Timeline */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Clock size={12} className="text-(--text-muted)" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Timeline</span>
              </div>
              <div className="space-y-2 text-[10px]">
                <div>
                  <span className="text-(--text-muted) font-bold uppercase block">Created</span>
                  <span className="font-mono">{formatCompactDate(liveTask.created_at)}</span>
                </div>
                <div>
                  <span className="text-(--text-muted) font-bold uppercase block">Started</span>
                  <span className="font-mono">{liveTask.started_at ? formatCompactDate(liveTask.started_at) : '—'}</span>
                </div>
                <div>
                  <span className="text-(--text-muted) font-bold uppercase block">Finished</span>
                  <span className="font-mono">{liveTask.completed_at ? formatCompactDate(liveTask.completed_at) : '—'}</span>
                </div>
                <div>
                  <span className="text-(--text-muted) font-bold uppercase block">Child Session</span>
                  <span className="font-mono">{childSessionId ? childSessionId.slice(0, 12) : '—'}</span>
                </div>
              </div>
            </section>

            {/* Allowed Tools */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Wrench size={12} className="text-(--text-muted)" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Tools</span>
              </div>
              {liveTask.allowed_tools.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {liveTask.allowed_tools.map(tool => (
                    <span key={tool} className="px-1.5 py-0.5 rounded bg-(--surface-2) text-[9px] font-mono font-bold border border-(--border-subtle)">{tool}</span>
                  ))}
                </div>
              ) : (
                <p className="text-xs leading-relaxed text-(--text-secondary)">All tools available for this run.</p>
              )}
            </section>
          </aside>

          {/* Right: session transcript */}
          <div className="flex-1 flex flex-col min-w-0 min-h-0">
            <div className="px-4 py-2.5 border-b border-(--border-subtle) flex items-center gap-2 bg-(--surface-0) shrink-0">
              <MessageSquare size={13} className="text-(--text-muted)" />
              <span className="text-[9px] font-bold uppercase tracking-widest text-(--text-muted)">Conversation</span>
              {isRunning && <Loader2 size={11} className="animate-spin text-(--text-muted) ml-auto" />}
            </div>

            {childSessionId && instanceName && <div className="max-h-[40%] overflow-y-auto shrink-0 border-b border-(--border-subtle) p-4 space-y-3">
              <SessionPreviewApprovals key={childSessionId} instanceName={instanceName} sessionId={childSessionId} />
              <SessionPermissions key={`permissions:${childSessionId}`} instanceName={instanceName} sessionId={childSessionId} />
            </div>}

            <div ref={scrollRef} onScroll={onTranscriptScroll} className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 space-y-6">
              {loadingMsgs && displayMessages.length === 0 ? (
                <div className="flex items-center justify-center h-full text-(--text-muted)">
                  <Loader2 size={20} className="animate-spin" />
                </div>
              ) : !childSessionId ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-[11px] text-(--text-muted) font-bold uppercase tracking-wider">Waiting for agent to start...</p>
                </div>
              ) : displayMessages.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-[11px] text-(--text-muted) font-bold uppercase tracking-wider">No messages yet...</p>
                </div>
              ) : (
                displayMessages.map((m) => (
                  <SessionMessageCard
                    key={m.id}
                    message={m}
                    toolArgumentsByCallId={toolArgumentsByCallId}
                    onResolveApproval={resolveApprovalInline}
                    resolvingApprovalKey={resolvingApprovalKey}
                  />
                ))
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-(--surface-1) border-t border-(--border-subtle) flex items-center justify-between gap-3 shrink-0">
          <div>
            {isRunning && onTerminate && (
              <button onClick={() => onTerminate(liveTask.id)} disabled={isTerminating}
                className="btn-secondary h-10 rounded-full px-5 text-rose-500 hover:bg-rose-500/10 hover:border-rose-500/20 gap-2 text-xs">
                {isTerminating ? <Hash size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Terminate
              </button>
            )}
          </div>
          <button type="button" onClick={onClose} className="btn-primary h-10 rounded-full px-6 text-xs">Close</button>
        </div>
      </section>
    </div>,
    document.querySelector('.desktop-frame') ?? document.body,
  );
}
