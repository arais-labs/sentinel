import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Bell, Check, ChevronRight, Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { AppNotification } from '../../../../desktop/sentinel/src/shared/notifications';
import { connectNotifications, hideNotificationBanner, updateNotification, useNotificationStore } from '../lib/notifications';
import { openWorkspaceTab } from '../lib/workspace-navigation';
import './notifications.css';

function NotificationBanner({ item, paused }: { item: AppNotification; paused: boolean }) {
  const navigate = useNavigate();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [closing, setClosing] = useState(false);
  const duration = item.durationMs ?? (item.severity === 'error' || item.severity === 'urgent' ? 8000 : 5000);
  const remaining = useRef(duration);
  useEffect(() => { remaining.current = duration; setClosing(false); }, [item.updatedAt, duration]);
  useEffect(() => {
    if (closing || hovered || focused || paused) return;
    const started = performance.now();
    const timer = window.setTimeout(() => setClosing(true), remaining.current);
    return () => { window.clearTimeout(timer); remaining.current = Math.max(0, remaining.current - (performance.now() - started)); };
  }, [item.updatedAt, duration, hovered, focused, paused, closing]);
  useEffect(() => {
    if (!closing) return;
    const timer = window.setTimeout(() => hideNotificationBanner(item.id), 180);
    return () => window.clearTimeout(timer);
  }, [closing, item.id]);
  const Icon = item.progress ? Loader2 : item.severity === 'success' ? Check : item.severity === 'info' ? Bell : AlertCircle;
  const open = () => {
    const target = item.target;
    if (!target?.instanceName) return;
    void updateNotification(item.id, 'read').catch(() => {});
    hideNotificationBanner(item.id);
    openWorkspaceTab(navigate, target.instanceName, target.sessionId ? 'sessions' : 'workspaces', { sessionId: target.sessionId });
  };
  return <article className={`notification-banner severity-${item.severity}`} data-closing={closing}
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
    onFocusCapture={() => setFocused(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFocused(false); }}>
    <div className="notification-banner-icon"><Icon size={17} className={item.progress ? 'animate-spin' : undefined} /></div>
    <div className="notification-banner-content" role={item.severity === 'urgent' || item.severity === 'error' ? 'alert' : 'status'} aria-atomic="true">
      <span className="notification-banner-source">{item.source.replace(/[-_]/g, ' ')}</span>
      <h3>{item.title}</h3>
      {item.message && <p>{item.message}</p>}
      {item.progress && <div className="notification-progress" aria-label="In progress"><span /></div>}
      {item.target?.instanceName && <button className="notification-banner-open" onClick={open}>{item.target.sessionId ? 'Open session' : 'Open workspace'}<ChevronRight size={12} /></button>}
    </div>
    <button className="notification-banner-close" aria-label="Hide banner; keep in inbox" title="Hide banner" onClick={() => setClosing(true)}><X size={14} /></button>
  </article>;
}

export function NotificationHost() {
  const { items, bannerIds, inboxOpen } = useNotificationStore();
  const [hidden, setHidden] = useState(document.hidden);
  useEffect(connectNotifications, []);
  useEffect(() => {
    const changed = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  return createPortal(<section className="notification-banner-stack" aria-label="Recent notifications" hidden={inboxOpen}>
    {bannerIds.map(id => {
      const item = items.find(value => value.id === id);
      return item ? <NotificationBanner key={id} item={item} paused={hidden || inboxOpen} /> : null;
    })}
  </section>, document.body);
}
