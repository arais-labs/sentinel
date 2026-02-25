import { X, Terminal, Clock, Activity, Hash, Target, Wrench, Trash2, MessageSquare, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useEffect, useRef, useState } from 'react';

import { Panel } from './ui/Panel';
import { StatusChip } from './ui/StatusChip';
import { formatCompactDate } from '../lib/format';
import { api } from '../lib/api';
import type { Message, MessageListResponse, SubAgentTask } from '../types/api';

interface SubAgentTaskModalProps {
  task: SubAgentTask;
  onClose: () => void;
  onTerminate?: (taskId: string) => void;
  isTerminating?: boolean;
}

function sortMessages(items: Message[]) {
  return [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}

export function SubAgentTaskModal({ task, onClose, onTerminate, isTerminating }: SubAgentTaskModalProps) {
  const isRunning = task.status === 'running' || task.status === 'pending';
  const childSessionId = (task.result?.child_session_id as string) ?? null;

  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchMessages(sessionId: string) {
    try {
      const payload = await api.get<MessageListResponse>(`/sessions/${sessionId}/messages?limit=100`);
      const sorted = sortMessages(payload.items).filter(
        m => m.role !== 'system' && !(m.role === 'assistant' && !m.content?.trim() && !m.tool_name)
      );
      setMessages(sorted);
      setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }), 50);
    } catch { /* ignore */ }
  }

  useEffect(() => {
    if (!childSessionId) return;
    setLoadingMsgs(true);
    fetchMessages(childSessionId).finally(() => setLoadingMsgs(false));

    if (isRunning) {
      pollRef.current = setInterval(() => fetchMessages(childSessionId), 2000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [childSessionId, isRunning]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <Panel className="relative w-full max-w-4xl bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col" style={{ height: '80vh' }}>

        {/* Header */}
        <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)] flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-[color:var(--surface-2)] text-[color:var(--accent-solid)]">
              <Terminal size={18} />
            </div>
            <div className="flex flex-col">
              <h2 className="font-bold text-sm uppercase tracking-widest">{task.name}</h2>
              <span className="text-[9px] text-[color:var(--text-muted)] font-mono uppercase tracking-tighter">Sub-Agent Task Node</span>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <StatusChip
              label={task.status}
              tone={task.status === 'completed' ? 'good' : task.status === 'running' ? 'warn' : task.status === 'failed' ? 'danger' : 'default'}
            />
            <button type="button" onClick={onClose} className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Left: telemetry sidebar */}
          <div className="w-64 flex-shrink-0 border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-y-auto p-4 space-y-5">
            {/* Objective */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Target size={12} className="text-[color:var(--text-muted)]" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Objective</span>
              </div>
              <p className="text-xs leading-relaxed text-[color:var(--text-secondary)]">{task.scope || '—'}</p>
            </section>

            {/* Steps */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Activity size={12} className="text-[color:var(--text-muted)]" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Telemetry</span>
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-[10px]">
                  <span className="text-[color:var(--text-muted)] font-bold uppercase">Steps</span>
                  <span className="font-mono font-bold">{task.turns_used} / {task.max_steps}</span>
                </div>
                <div className="w-full bg-[color:var(--surface-2)] h-1 rounded-full overflow-hidden">
                  <div className="bg-[color:var(--accent-solid)] h-full transition-all duration-500" style={{ width: `${Math.min((task.turns_used / task.max_steps) * 100, 100)}%` }} />
                </div>
                <div className="flex justify-between text-[10px]">
                  <span className="text-[color:var(--text-muted)] font-bold uppercase">Tokens</span>
                  <span className="font-mono font-bold">{task.tokens_used.toLocaleString()}</span>
                </div>
              </div>
            </section>

            {/* Timeline */}
            <section className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Clock size={12} className="text-[color:var(--text-muted)]" />
                <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Timeline</span>
              </div>
              <div className="space-y-2 text-[10px]">
                <div>
                  <span className="text-[color:var(--text-muted)] font-bold uppercase block">Created</span>
                  <span className="font-mono">{formatCompactDate(task.created_at)}</span>
                </div>
                <div>
                  <span className="text-[color:var(--text-muted)] font-bold uppercase block">Finished</span>
                  <span className="font-mono">{task.completed_at ? formatCompactDate(task.completed_at) : '—'}</span>
                </div>
              </div>
            </section>

            {/* Allowed Tools */}
            {task.allowed_tools.length > 0 && (
              <section className="space-y-2">
                <div className="flex items-center gap-1.5">
                  <Wrench size={12} className="text-[color:var(--text-muted)]" />
                  <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Tools</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {task.allowed_tools.map(tool => (
                    <span key={tool} className="px-1.5 py-0.5 rounded bg-[color:var(--surface-2)] text-[9px] font-mono font-bold border border-[color:var(--border-subtle)]">{tool}</span>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Right: session transcript */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-4 py-2.5 border-b border-[color:var(--border-subtle)] flex items-center gap-2 bg-[color:var(--surface-0)] flex-shrink-0">
              <MessageSquare size={13} className="text-[color:var(--text-muted)]" />
              <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Session Transcript</span>
              {isRunning && <Loader2 size={11} className="animate-spin text-[color:var(--text-muted)] ml-auto" />}
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
              {loadingMsgs && messages.length === 0 ? (
                <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">
                  <Loader2 size={20} className="animate-spin" />
                </div>
              ) : !childSessionId ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-[11px] text-[color:var(--text-muted)] font-bold uppercase tracking-wider">Waiting for agent to start...</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-[11px] text-[color:var(--text-muted)] font-bold uppercase tracking-wider">No messages yet...</p>
                </div>
              ) : (
                messages.map(m => {
                  const isUser = m.role === 'user';
                  const isToolResult = m.role === 'tool_result';
                  return (
                    <div key={m.id} className={`flex w-full flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
                      <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)] px-1">{m.role}</span>
                      <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-xs border ${
                        isUser
                          ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] border-transparent rounded-tr-none font-medium'
                          : isToolResult
                          ? 'bg-sky-500/5 border-sky-500/20 font-mono rounded-tl-none text-[color:var(--text-secondary)]'
                          : 'bg-[color:var(--surface-2)] border-[color:var(--border-subtle)] rounded-tl-none font-medium'
                      }`}>
                        {isToolResult && m.tool_name && (
                          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wide text-sky-500">⚙ {m.tool_name}</div>
                        )}
                        <div className={`markdown-body text-[11px] ${isUser ? 'prose-invert' : ''}`}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || ''}</ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-[color:var(--surface-1)] border-t border-[color:var(--border-subtle)] flex items-center justify-between gap-3 flex-shrink-0">
          <div>
            {isRunning && onTerminate && (
              <button onClick={() => onTerminate(task.id)} disabled={isTerminating}
                className="btn-secondary h-9 px-4 text-rose-500 hover:bg-rose-500/10 hover:border-rose-500/20 gap-2 text-xs">
                {isTerminating ? <Hash size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Terminate
              </button>
            )}
          </div>
          <button type="button" onClick={onClose} className="btn-primary h-9 px-6 text-xs">Close</button>
        </div>
      </Panel>
    </div>
  );
}
