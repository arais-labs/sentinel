import { RecentSessionDots } from './RecentSessionDots';
import { createPortal } from 'react-dom';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, MessagesSquare, Plus, X } from 'lucide-react';
import { notificationPublisher } from '../../lib/notifications';
import { api as client } from '../../lib/api';
import { getSessionDeleteTriggerSummary } from '../../lib/sessionDeletion';
import { requestWorkspaceTab, sessionLayoutKey } from '../../store/workspace-store';
import { useActiveSessionStore } from '../../store/active-session-store';
import type { Session, SessionListResponse } from '../../types/api';
import { SessionHistorySidebar } from './SessionHistorySidebar';
import { useSessionDeleteConfirmation } from './SessionDeleteConfirmDialog';

const notify = notificationPublisher('Chats');

export function GlobalSessionSelector({ instanceName }: { instanceName: string }) {
  const activeSessionId = useActiveSessionStore(s => s.byInstance[instanceName] ?? null);
  const setSharedSession = useActiveSessionStore(s => s.setActiveSession);
  const setActiveSessionId = (id: string | null) => setSharedSession(instanceName, id);
  const prefix = `/instances/${encodeURIComponent(instanceName)}`;
  const api = {
    get: <T,>(path: string) => client.get<T>(prefix + path),
    post: <T,>(path: string, body: unknown) => client.post<T>(prefix + path, body),
    patch: <T,>(path: string, body: unknown) => client.patch<T>(prefix + path, body),
    delete: <T,>(path: string) => client.delete<T>(prefix + path),
  };
  const [sessions, setSessions] = useState<Session[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [commandHeld, setCommandHeld] = useState(false);
  const [sessionFilter, setSessionFilter] = useState('');
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const newChatButton = useRef<HTMLButtonElement>(null);
  const newChatQueue = useRef<Promise<void>>(Promise.resolve());
  const bar = useRef<HTMLDivElement>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const shortcut = navigator.platform.includes('Mac') ? '⌘K' : 'Ctrl+K';
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const { confirmSessionDelete, sessionDeleteConfirmDialog } = useSessionDeleteConfirmation();
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingSessionTitle, setEditingSessionTitle] = useState('');
  const [isMultiSelectMode, setIsMultiSelectMode] = useState(false);
  const [selectedSessionIds, setSelectedSessionIds] = useState<string[]>([]);
  const filteredSessions = useMemo(() => {
    const q = sessionFilter.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => `${s.title ?? ''}`.toLowerCase().includes(q));
  }, [sessions, sessionFilter]);

  useLayoutEffect(() => {
    const measure = () => {
      bar.current?.style.setProperty('--new-chat-width', `${newChatButton.current?.offsetWidth ?? 0}px`);
      bar.current?.style.setProperty('--session-trigger-width', `${trigger.current?.offsetWidth ?? 0}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (newChatButton.current) observer.observe(newChatButton.current);
    if (trigger.current) observer.observe(trigger.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const down = (event: KeyboardEvent) => { if (event.key === 'Meta' || event.metaKey) setCommandHeld(true); };
    const up = (event: KeyboardEvent) => { if (event.key === 'Meta' || !event.metaKey) setCommandHeld(false); };
    const reset = () => setCommandHeld(false);
    const visibility = () => { if (document.hidden) reset(); };
    window.addEventListener('keydown', down, true);
    window.addEventListener('keyup', up, true);
    window.addEventListener('blur', reset);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('keydown', down, true);
      window.removeEventListener('keyup', up, true);
      window.removeEventListener('blur', reset);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);

  const selectedSessionIdSet = useMemo(() => new Set(selectedSessionIds), [selectedSessionIds]);
  const selectableVisibleSessionIds = useMemo(
    () => filteredSessions.map((session) => session.id),
    [filteredSessions],
  );
  const allVisibleSelected = useMemo(
    () =>
      selectableVisibleSessionIds.length > 0 &&
      selectableVisibleSessionIds.every((id) => selectedSessionIdSet.has(id)),
    [selectableVisibleSessionIds, selectedSessionIdSet],
  );

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

  const [forkingSessionId, setForkingSessionId] = useState<string | null>(null);
  const forkPending = useRef(false);
  async function forkSession(session: Session) {
    if (forkPending.current) return;
    forkPending.current = true;
    setForkingSessionId(session.id);
    try {
      const fork = await api.post<Session>(`/sessions/${session.id}/fork`, {});
      setSessions(current => [fork, ...current]);
      requestWorkspaceTab(sessionLayoutKey(instanceName, fork.id), 'sessions');
      useActiveSessionStore.setState({ composerFocusRequest: { instanceName, sessionId: fork.id } });
      chooseSession(fork.id);
      notify.success('Session forked');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to fork session');
    } finally {
      forkPending.current = false;
      setForkingSessionId(null);
    }
  }

  async function deleteSession(session: Session) {
    if (deletingSessionId) return;
    setDeletingSessionId(session.id);
    try {
      const label = (session.title || 'Session').trim() || 'Session';
      const triggerSummary = await getSessionDeleteTriggerSummary([session.id], prefix);
      if (triggerSummary.triggerCount > 0) {
        const confirmed = await confirmSessionDelete({
          kind: 'trigger_targets',
          label,
          sessionCount: 1,
          triggerCount: triggerSummary.triggerCount,
          triggerNames: triggerSummary.triggerNames,
        });
        if (!confirmed) return;
      }
      if (!await confirmSessionDelete({ kind: 'single', label })) return;

      await api.delete<{ status: string }>(`/sessions/${session.id}`);
      const remaining = sessions.filter(item => item.id !== session.id);
      setSessions(current => current.filter(item => item.id !== session.id));
      setSelectedSessionIds((current) => current.filter((id) => id !== session.id));

      const selection = useActiveSessionStore.getState();
      if (selection.byInstance[instanceName] === session.id) selection.closeActiveSession(instanceName, remaining.map(item => item.id));
      else selection.dismissRecentSession(instanceName, session.id);
      notify.success('Session deleted');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to delete session');
    } finally {
      setDeletingSessionId(null);
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
      notify.success('Session renamed');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to rename session');
    } finally {
      setRenamingSessionId(null);
    }
  }

  async function deleteSelectedSessions() {
    if (deletingSessionId) return;
    const targetIds = [...selectedSessionIds];
    if (targetIds.length === 0) return;

    setDeletingSessionId('bulk');
    try {
      const triggerSummary = await getSessionDeleteTriggerSummary(targetIds, prefix);
      if (triggerSummary.triggerCount > 0) {
        const confirmed = await confirmSessionDelete({
          kind: 'trigger_targets',
          sessionCount: targetIds.length,
          triggerCount: triggerSummary.triggerCount,
          triggerNames: triggerSummary.triggerNames,
        });
        if (!confirmed) return;
      }
      if (!await confirmSessionDelete({ kind: 'bulk', sessionCount: targetIds.length })) return;

      const results = await Promise.allSettled(
        targetIds.map((id) => api.delete<{ status: string }>(`/sessions/${id}`)),
      );
      const deletedIds = targetIds.filter((_, index) => results[index]?.status === 'fulfilled');
      const failedCount = targetIds.length - deletedIds.length;

      if (deletedIds.length > 0) {
        const remaining = sessions.filter(session => !deletedIds.includes(session.id));
        setSessions(remaining);
        setSelectedSessionIds((current) => current.filter((id) => !deletedIds.includes(id)));

        if (activeSessionId && deletedIds.includes(activeSessionId)) {
          const fallbackId = remaining.find((session) => !session.parent_session_id)?.id ?? remaining[0]?.id ?? null;
          setActiveSessionId(fallbackId);

        }
      }

      if (failedCount === 0) {
        notify.success(`${deletedIds.length} session${deletedIds.length === 1 ? '' : 's'} deleted`);
      } else if (deletedIds.length > 0) {
        notify.error(`${failedCount} session${failedCount === 1 ? '' : 's'} could not be deleted`);
      } else {
        notify.error('Failed to delete selected sessions');
      }
    } finally {
      setDeletingSessionId(null);
    }
  }


  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const items: Session[] = [];
        let offset = 0;
        while (true) {
          const page = await api.get<SessionListResponse>(`/sessions?include_sub_agents=true&limit=100&offset=${offset}`);
          items.push(...page.items);
          offset += page.items.length;
          if (!page.items.length || offset >= page.total) break;
        }
        if (!cancelled) {
          const rootSessions = items.filter(session => !session.parent_session_id);
          setSessions(rootSessions);
          const viewedSessionId = Array.from(document.querySelectorAll<HTMLElement>('[data-session-view]'))
            .find(element => element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0)?.dataset.sessionView ?? null;
          await window.sentinelDesktop?.syncForms(instanceName, items.flatMap(session =>
            session.pending_form_id ? [{ sessionId: session.id, formId: session.pending_form_id, title: session.title || 'Session' }] : []
          ), viewedSessionId);
          // Child completions belong to the parent conversation, not the notification inbox.
          await window.sentinelDesktop?.syncCompletions(instanceName, rootSessions.map(session => ({
            sessionId: session.id, completionId: session.completion_id ?? null,
            running: session.is_running, awaitingInput: Boolean(session.awaiting_input),
          })));
        }
      } catch { /* Keep the last list during reconnects. */ }
      finally { if (!cancelled) setLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [instanceName, open, activeSessionId]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (document.querySelector('[data-session-delete-confirm], [data-tour-welcome]')) return;
      if (event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && !event.isComposing && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        if (!sessions.length) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.repeat) return;
        // The session list is newest first, independent of the draggable dots.
        const selected = useActiveSessionStore.getState().byInstance[instanceName];
        const index = sessions.findIndex(session => session.id === selected);
        const next = sessions[index < 0 ? 0 : index + (event.key === 'ArrowDown' ? 1 : -1)];
        if (next) chooseSession(next.id);
        return;
      }
      if (event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && event.key === 'Backspace') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat && !deletingSessionId) {
          const selected = useActiveSessionStore.getState().byInstance[instanceName];
          const session = sessions.find(item => item.id === selected);
          if (session) void deleteSession(session);
        }
        return;
      }
      if (event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'w') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) {
          const closingId = useActiveSessionStore.getState().byInstance[instanceName];
          const next = useActiveSessionStore.getState().closeActiveSession(instanceName, sessions.map(session => session.id));
          if (closingId) void api.post<{ status: string }>(`/sessions/${closingId}/close`, {}).then(result => {
            if (result.status === 'discarded') setSessions(current => current.filter(session => session.id !== closingId));
          }).catch(() => notify.error('Could not discard the empty session; it remains in history.'));
          setOpen(false);
          if (next) void client.post(`${prefix}/sessions/${next}/read`, {}).catch(() => {});
        }
        return;
      }
      if (event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'n') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) newChat();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setOpen(value => !value);
      }
    };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  }, [instanceName, prefix, sessions, deletingSessionId, activeSessionId]);

  useEffect(() => { setHighlightedIndex(0); }, [sessionFilter]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    setHighlightedIndex(Math.max(0, filteredSessions.findIndex(session => session.id === activeSessionId)));
    container.current?.querySelector('input')?.focus({ preventScroll: true });
    if (activeSessionId) revealSession(activeSessionId);
    return () => { if (previous?.isConnected) previous.focus(); else trigger.current?.focus(); };
  }, [open]);
  // Only explicit navigation may move the list; polling must preserve manual scrolling.
  function revealSession(id: string) {
    container.current?.querySelector(`[data-session-id="${id}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  function chooseSession(id: string) {
    setActiveSessionId(id);
    setOpen(false);
    void api.post(`/sessions/${id}/read`, {}).catch(() => {});
  }
  function newChat() {
    setOpen(false);
    // Every press creates a distinct chat. Queue requests so rapid shortcuts
    // preserve their order instead of dropping presses or racing selection.
    newChatQueue.current = newChatQueue.current.then(async () => {
      try {
        const session = await api.post<Session>('/sessions', {});
        setSessions(current => [session, ...current.filter(item => item.id !== session.id)]);
        useActiveSessionStore.setState({ composerFocusRequest: { instanceName, sessionId: session.id } });
        requestWorkspaceTab(sessionLayoutKey(instanceName, session.id), 'sessions');
        setActiveSessionId(session.id);
      } catch (error) {
        notify.error(error instanceof Error ? error.message : 'Could not create chat');
      }
    });
    return newChatQueue.current;
  }

  const current = sessions.find(s => s.id === activeSessionId);
  return <>
    <div ref={bar} className="global-session-selector">
      <button ref={trigger} className="global-session-trigger" aria-label={`All chats (${shortcut})`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <MessagesSquare size={14} className="shrink-0 text-(--accent-solid)" />
        <span className="truncate">{current?.title?.trim() || (activeSessionId ? 'Session' : 'All chats')}</span>
        <kbd className="shrink-0 text-[10px] text-(--text-muted)">{shortcut}</kbd>
        <ChevronDown size={13} className="shrink-0 text-(--text-muted)" />
      </button>
      <button ref={newChatButton} onClick={() => void newChat()} title="New chat (⌘N)" aria-keyshortcuts="Meta+N" className="global-new-chat">
        <Plus size={14} />New chat
        <kbd className="shrink-0 text-[10px] opacity-60">⌘N</kbd>
      </button>
      <RecentSessionDots instanceName={instanceName} sessions={sessions} onSelect={chooseSession} expanded={commandHeld} />
    </div>
    {open && createPortal(<div className="global-session-backdrop" onPointerDown={event => { if (event.target === event.currentTarget) setOpen(false); }}>
      <div ref={container} className="global-session-menu" role="dialog" aria-modal="true" aria-label="All chats" onKeyDown={event => {
        if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); }
        if (event.key === 'Tab') {
          const controls = Array.from(container.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? []).filter(el => el.getClientRects().length);
          const first = controls[0], last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
        if (editingSessionId || isMultiSelectMode || event.target !== container.current?.querySelector('input')) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          const next = Math.max(0, Math.min(filteredSessions.length - 1, highlightedIndex + (event.key === 'ArrowDown' ? 1 : -1)));
          setHighlightedIndex(next);
          if (filteredSessions[next]) revealSession(filteredSessions[next].id);
        } else if (event.key === 'Enter' && filteredSessions[highlightedIndex]) {
          event.preventDefault(); chooseSession(filteredSessions[highlightedIndex].id);
        }
      }}>
        <div className="flex items-center justify-between gap-4 px-5 pt-4 pb-1">
          <h2 className="text-sm font-bold uppercase tracking-widest text-(--text-primary)">Sessions</h2>
          <div className="flex items-center gap-3">
            <button aria-label="Close session selector" onClick={() => setOpen(false)} className="p-2 rounded-lg text-(--text-secondary) hover:bg-(--surface-2)"><X size={18} /></button>
          </div>
        </div>
        <div className="min-h-0 flex-1">
                <SessionHistorySidebar
                  showHeaderTitle={false}
                  historyTab="sessions"
                  hideTabs
                  setHistoryTab={() => {}}
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
                  highlightedSessionId={filteredSessions[highlightedIndex]?.id}
                  onSessionClick={chooseSession}
                  editingSessionId={editingSessionId}
                  editingSessionTitle={editingSessionTitle}
                  setEditingSessionTitle={setEditingSessionTitle}
                  submitRenameSession={submitRenameSession}
                  cancelRenameSession={cancelRenameSession}
                  startRenameSession={startRenameSession}
                  forkSession={forkSession}
                  forkingSessionId={forkingSessionId}
                  deleteSession={deleteSession}
                  renamingSessionId={renamingSessionId}
                  loadingSessions={loading}
                />
        </div>
        <div className="px-5 py-3 text-[10px] text-(--text-muted) border-t border-(--border-subtle)">↑ ↓ Navigate · Enter Open · Esc Close</div>
      </div>
    </div>, document.body)}
    {createPortal(<div className="global-session-confirmation">{sessionDeleteConfirmDialog}</div>, document.body)}
  </>;
}
