import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../../lib/api';
import { Markdown } from '../ui/Markdown';
import { SessionPreviewApprovals } from './SessionPreviewApprovals';
import { useActiveSessionStore } from '../../store/active-session-store';
import type { MessageListResponse, Session } from '../../types/api';

const emptyIds: string[] = [];
const shortTitle = (title: string | null | undefined) => {
  const characters = Array.from(title?.trim() || 'Session');
  return characters.slice(0, 20).join('') + (characters.length > 20 ? '…' : '');
};

export function RecentSessionDots({ instanceName, sessions, onSelect, expanded }: {
  instanceName: string; sessions: Session[]; onSelect: (id: string) => void; expanded: boolean;
}) {
  const recentIds = useActiveSessionStore(s => s.recentByInstance[instanceName] ?? emptyIds);
  const dismiss = useActiveSessionStore(s => s.dismissRecentSession);
  const move = useActiveSessionStore(s => s.moveRecentSession);
  const activeId = useActiveSessionStore(s => s.byInstance[instanceName]);
  const [hover, setHover] = useState<{ id: string; left: number; top: number } | null>(null);
  const [preview, setPreview] = useState<{ id: string; text: string } | null>(null);
  const [failed, setFailed] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dots = useRef<HTMLDivElement>(null);
  const dragged = useRef<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: string; after: boolean } | null>(null);
  const positions = useRef(new Map<string, number>());
  const movements = useRef(new Map<string, Animation>());
  const recent = recentIds.map(id => sessions.find(session => session.id === id)).filter((session): session is Session => Boolean(session));
  const order = recent.map(session => session.id).join(',');
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (document.querySelector('[data-session-delete-confirm], [data-tour-welcome]')) return;
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      const ids = order ? order.split(',') : [];
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        if (!ids.length) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.repeat) return;
        const current = useActiveSessionStore.getState().byInstance[instanceName];
        const index = current ? ids.indexOf(current) : -1;
        const right = event.key === 'ArrowRight';
        const next = ids[index < 0 ? right ? 0 : ids.length - 1 : index + (right ? 1 : -1)];
        if (next) {
          setHover(null);
          onSelect(next);
          dots.current?.querySelector<HTMLButtonElement>(`[data-session-dot="${next}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
        return;
      }
      const number = /^Digit([1-9])$/.exec(event.code)?.[1] ?? (/^[1-9]$/.test(event.key) ? event.key : null);
      if (!number) return;
      const id = ids[Number(number) - 1];
      if (!id) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.repeat) return;
      setHover(null);
      onSelect(id);
    };
    window.addEventListener('keydown', keydown, true);
    return () => window.removeEventListener('keydown', keydown, true);
  }, [instanceName, order, onSelect]);
  useLayoutEffect(() => {
    positions.current.clear();
    clearTimeout(closeTimer.current);
    setHover(null);
  }, [expanded]);
  useLayoutEffect(() => {
    const next = new Map<string, number>();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    dots.current?.querySelectorAll<HTMLButtonElement>('[data-session-dot]').forEach(dot => {
      const id = dot.dataset.sessionDot!;
      const left = dot.offsetLeft;
      const previous = positions.current.get(id);
      const transform = getComputedStyle(dot).transform;
      const ongoingOffset = transform === 'none' ? 0 : new DOMMatrixReadOnly(transform).m41;
      movements.current.get(id)?.cancel();
      next.set(id, left);
      if (previous === undefined || reducedMotion) return;
      const delta = previous + ongoingOffset - left;
      if (Math.abs(delta) < 1) return;
      movements.current.set(id, dot.animate([
        { transform: `translateX(${delta}px)` },
        { transform: 'translateX(0)' },
      ], { duration: 300, easing: 'cubic-bezier(.22, 1, .36, 1)' }));
    });
    movements.current.forEach((animation, id) => {
      if (!next.has(id)) { animation.cancel(); movements.current.delete(id); }
    });
    positions.current = next;
  }, [order]);
  useEffect(() => () => { movements.current.forEach(animation => animation.cancel()); }, []);

  const hovered = recent.find(session => session.id === hover?.id);
  const hoveredIndex = recent.findIndex(session => session.id === hover?.id);
  const status = (session: Session) => session.awaiting_input ? (session.pending_form_id ? 'Waiting for your input' : 'Waiting for approval') : session.is_running ? 'Working' : session.has_unread ? 'Unread result' : 'Read';
  const keepOpen = () => { clearTimeout(closeTimer.current); };
  const closeSoon = () => { closeTimer.current = setTimeout(() => setHover(null), 120); };

  useEffect(() => () => clearTimeout(closeTimer.current), []);
  useEffect(() => {
    if (!hover) return;
    setFailed(false);
    setPreview(null);
    if (hovered?.is_running || hovered?.awaiting_input || !hovered?.completion_id) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const result = await api.get<MessageListResponse>(`/instances/${encodeURIComponent(instanceName)}/sessions/${hover.id}/messages?limit=1&final_only=true`);
        const latest = result.items.find(message => message.id === hovered.completion_id);
        if (!cancelled) setPreview({ id: hover.id, text: latest?.content ?? '' });
      } catch { if (!cancelled) setFailed(true); }
    };
    void refresh();
    return () => { cancelled = true; };
  }, [hover?.id, instanceName, hovered?.completion_id, hovered?.is_running, hovered?.awaiting_input]);

  if (!recent.length) return null;
  return <>
    <div ref={dots} className="recent-session-dots" data-expanded={expanded} aria-label="Watched sessions" onTransitionEnd={() => {
      dots.current?.querySelectorAll<HTMLButtonElement>('[data-session-dot]').forEach(dot => positions.current.set(dot.dataset.sessionDot!, dot.offsetLeft));
    }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null); }}>
      {recent.map((session, index) => <button key={session.id} data-session-dot={session.id} className="recent-session-dot" data-status={session.awaiting_input ? 'waiting' : session.is_running ? 'working' : session.has_unread ? 'unread' : 'read'} aria-label={`${session.title || 'Session'} — ${status(session)}`} aria-keyshortcuts={index < 9 ? `Meta+${index + 1}` : undefined} aria-current={session.id === activeId ? 'true' : undefined}
        draggable data-drop={dropTarget?.id === session.id ? dropTarget.after ? 'after' : 'before' : undefined}
        onDragStart={event => { dragged.current = session.id; setHover(null); clearTimeout(closeTimer.current); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('application/x-sentinel-session', session.id); }}
        onDragOver={event => {
          if (!dragged.current) return;
          event.preventDefault(); event.dataTransfer.dropEffect = 'move';
          const rect = event.currentTarget.getBoundingClientRect();
          setDropTarget({ id: session.id, after: event.clientX > rect.left + rect.width / 2 });
          const host = dots.current;
          if (host) { const bounds = host.getBoundingClientRect(); if (event.clientX < bounds.left + 20) host.scrollLeft -= 18; else if (event.clientX > bounds.right - 20) host.scrollLeft += 18; }
        }}
        onDrop={event => {
          if (!dragged.current) return;
          event.preventDefault(); event.stopPropagation();
          const rect = event.currentTarget.getBoundingClientRect();
          move(instanceName, dragged.current, session.id, event.clientX > rect.left + rect.width / 2);
          dragged.current = null; setDropTarget(null); setHover(null);
        }}
        onDragEnd={() => { dragged.current = null; setDropTarget(null); }}
        onClick={() => { setHover(null); onSelect(session.id); }}
        onMouseEnter={event => { if (dragged.current) return; keepOpen(); const rect = event.currentTarget.getBoundingClientRect(); setHover({ id: session.id, left: Math.max(12, Math.min(rect.left, window.innerWidth - 372)), top: rect.bottom + 10 }); }}
        onMouseLeave={closeSoon}
        onFocus={event => { keepOpen(); const rect = event.currentTarget.getBoundingClientRect(); setHover({ id: session.id, left: Math.max(12, Math.min(rect.left, window.innerWidth - 372)), top: rect.bottom + 10 }); }}
        onBlur={closeSoon} onKeyDown={event => {
          if (event.key === 'Escape') setHover(null);
          if (event.altKey && !event.metaKey && !event.ctrlKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
            const after = event.key === 'ArrowRight';
            const target = recent[index + (after ? 1 : -1)];
            if (target) { event.preventDefault(); move(instanceName, session.id, target.id, after); setHover(null); }
          }
        }}
      >
        <span className="recent-session-indicator" aria-hidden="true" />
        <span className="recent-session-details" aria-hidden={!expanded}><span>
          <span className="recent-session-label">{shortTitle(session.title)}</span>
          {index < 9 && <kbd className="recent-session-shortcut">⌘{index + 1}</kbd>}
        </span></span>
      </button>)}
    </div>
    {hover && hovered && createPortal(<>
      <div className="recent-session-preview-backdrop" aria-hidden="true" />
      <div className="recent-session-preview" role="dialog" aria-label="Session preview" data-status={hovered.awaiting_input ? 'waiting' : hovered.is_running ? 'working' : hovered.has_unread ? 'unread' : 'read'} style={{ left: hover.left, top: hover.top, maxHeight: `calc(100dvh - ${hover.top + 12}px)` }} onMouseEnter={keepOpen} onMouseLeave={closeSoon} onFocus={keepOpen} onBlur={closeSoon} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setHover(null); } }}>
        <header className="recent-session-preview-header">
          <button className="recent-session-preview-dismiss" title="Dismiss from tracking" aria-label="Dismiss from tracking" onClick={() => { dismiss(instanceName, hovered.id); setHover(null); }}>Dismiss</button>
          <strong title={hovered.title || 'Session'}>{hovered.title || 'Session'}</strong>
        </header>
        <div className="recent-session-preview-content">
          {hovered.awaiting_input && <p className="recent-session-preview-notice">{hovered.pending_form_id ? 'A form is waiting for your input. Open the session to respond.' : 'A tool is waiting for your approval.'}</p>}
          {hovered.awaiting_input && <SessionPreviewApprovals key={`${instanceName}:${hover.id}`} instanceName={instanceName} sessionId={hover.id} />}
          {!hovered.awaiting_input && (!hovered.is_running && preview?.id === hover.id && preview.text
            ? <Markdown compact className="recent-session-preview-markdown" content={preview.text} />
            : <p>{hovered.is_running ? 'Working — the final response will appear here when ready.' : hovered.awaiting_input ? 'Open the session to respond.' : failed ? 'Preview unavailable.' : !hovered.completion_id || preview?.id === hover.id ? 'No completed response yet.' : 'Loading response…'}</p>)}
        </div>
        <footer className="recent-session-preview-footer">
          <span className="recent-session-preview-status"><span aria-hidden="true" />{status(hovered)}</span>
          <button className="recent-session-preview-open" onClick={() => { setHover(null); onSelect(hovered.id); }}>Open session{hoveredIndex >= 0 && hoveredIndex < 9 && <kbd>⌘{hoveredIndex + 1}</kbd>}</button>
        </footer>
      </div>
    </>, document.body)}
  </>;
}
