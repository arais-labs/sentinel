import { useEffect, useId, useState } from 'react';
import { Popover } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { Bell, Check, CheckCheck, ChevronRight, Loader2, Play, Settings2, X } from 'lucide-react';
import { playCompletionSound } from '../lib/completion-sound';
import { openWorkspaceTab } from '../lib/workspace-navigation';
import { saveNotificationSettings, updateNotification, useNotificationStore } from '../lib/notifications';
import type { CompletionSound, NotificationSettings } from '../../../../desktop/sentinel/src/shared/ipc';
import type { AppNotification } from '../../../../desktop/sentinel/src/shared/notifications';
import './notifications.css';

export function NotificationControls() {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const unread = useNotificationStore(state => state.items.filter(item => !item.read && !item.dismissed).length);
  useEffect(() => {
    useNotificationStore.setState({ inboxOpen: Boolean(anchor) });
    return () => { useNotificationStore.setState({ inboxOpen: false }); };
  }, [anchor]);
  return <>
    <button className="desktop-settings-toggle notification-bell" title="Notifications" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`} aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={event => setAnchor(anchor ? null : event.currentTarget)}>
      <Bell size={15} />{unread > 0 && <span className="notification-badge">{unread > 99 ? '99+' : unread}</span>}
    </button>
    <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} disableScrollLock
      anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }} transformOrigin={{ vertical: 'top', horizontal: 'right' }} sx={{ zIndex: 12000 }}
      slotProps={{ paper: { ...{ 'data-pane-menu': true }, sx: { mt: 1, width: 430, maxWidth: 'calc(100vw - 24px)', borderRadius: '18px', background: 'var(--surface-0)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', backgroundImage: 'none' } } }}>
      <NotificationsPanel onNavigate={() => setAnchor(null)} />
    </Popover>
  </>;
}

export function NotificationsPanel({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const soundId = useId();
  const [tab, setTab] = useState<'inbox' | 'settings'>('inbox');
  const [filter, setFilter] = useState<'all' | 'unread'>('unread');
  const { items, loading, settings, error: connectionError } = useNotificationStore();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function save(next: NotificationSettings) {
    setSaving(true);
    try { await saveNotificationSettings(next); setError(''); }
    catch (e) { setError(String(e)); }
    finally { setSaving(false); }
  }
  async function update(id: string | null, action: 'read' | 'dismiss') {
    try { await updateNotification(id, action); setError(''); }
    catch (e) { setError(String(e)); }
  }
  function openNotification(item: AppNotification) {
    const target = item.target;
    if (!target?.instanceName) return;
    void update(item.id, 'read');
    openWorkspaceTab(navigate, target.instanceName, target.sessionId ? 'sessions' : 'workspaces', { sessionId: target.sessionId });
    onNavigate?.();
  }
  const visible = items.filter(item => !item.dismissed && (filter === 'all' || !item.read));
  const unread = items.filter(item => !item.dismissed && !item.read).length;
  return <section className="notifications-panel" role="region" aria-label="Notifications">
    <header className="notifications-header"><h2>Notifications {unread > 0 && <span>{unread}</span>}</h2><div className="notifications-tabs" role="tablist" aria-label="Notification view">
      <button role="tab" aria-selected={tab === 'inbox'} onClick={() => setTab('inbox')}><Bell size={14} />Inbox</button>
      <button role="tab" aria-selected={tab === 'settings'} onClick={() => setTab('settings')}><Settings2 size={14} />Settings</button>
    </div></header>
    {(error || connectionError) && <p role="alert" className="notification-error">{error || connectionError}</p>}
    {tab === 'inbox' ? <>
      <div className="notifications-toolbar"><div><button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All</button><button aria-pressed={filter === 'unread'} onClick={() => setFilter('unread')}>Unread</button></div><button disabled={!unread} onClick={() => void update(null, 'read')}><CheckCheck size={14} />Mark all read</button></div>
      <div className="notification-list" aria-busy={loading}>
        {!visible.length && <div className="notifications-empty">{loading ? <Loader2 className="animate-spin" size={24} /> : <Bell size={24} />}<p>{loading ? 'Loading notifications…' : filter === 'unread' ? 'You’re all caught up' : 'Nothing here yet'}</p><span>Agent alerts and workspace updates appear here.</span></div>}
        {visible.map(item => <article key={item.id} className={`notification-item severity-${item.severity}${item.read ? ' is-read' : ''}`}>
          <div className="notification-indicator">{item.progress ? <Loader2 size={15} className="animate-spin" /> : item.severity === 'success' ? <Check size={15} /> : <span />}</div>
          <div className="notification-content"><div className="notification-meta"><span>{item.source.replace(/[-_]/g, ' ')}{item.severity === 'urgent' ? ' · Urgent' : ''}</span><time dateTime={item.updatedAt} title={new Date(item.updatedAt).toLocaleString()}>{new Date(item.updatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div>
            <h3>{item.title}</h3>{item.message && <p>{item.message}</p>}
            {item.progress && <div className="notification-progress" aria-label="In progress"><span /></div>}
            <div className="notification-actions">{item.target?.instanceName && <button onClick={() => openNotification(item)}>{item.target.sessionId ? 'Open session' : 'Open workspace'}<ChevronRight size={13} /></button>}{!item.read && <button onClick={() => void update(item.id, 'read')}>Mark read</button>}</div>
          </div><button className="notification-dismiss" title="Dismiss" aria-label={`Dismiss ${item.title}`} onClick={() => void update(item.id, 'dismiss')}><X size={14} /></button>
        </article>)}
      </div>
      {visible.length > 0 && <footer className="notifications-footer"><span>Recent activity · saved on this Mac</span><button onClick={() => void update(null, 'dismiss')}>Dismiss all</button></footer>}
    </> : <div className="notification-settings">
      <p>Choose how Sentinel gets your attention. Notifications remain in your inbox when alerts are muted.</p>
      {!settings ? <Loader2 size={18} className="animate-spin" /> : <>
        {([
          { key: 'inAppBanners', title: 'In-app banners', detail: 'Show new notifications at the top right. They stay in your inbox after the banner closes.' },
          { key: 'banners', title: 'Desktop banners', detail: 'Show alerts through macOS when Sentinel is in the background.' },
          { key: 'alertSounds', title: 'Attention sounds', detail: 'Ring for urgent alerts, warnings, and failures.' },
          { key: 'forms', title: 'Floating forms', detail: 'Ask for input over other apps.' },
          { key: 'completionSounds', title: 'Completion sounds', detail: 'Play a sound when an agent finishes.' },
        ] as const).map(item => <label className="notification-setting" key={item.key}><span><strong>{item.title}</strong><small>{item.detail}</small></span><input type="checkbox" role="switch" checked={settings[item.key]} disabled={saving} onChange={() => void save({ ...settings, [item.key]: !settings[item.key] })} /></label>)}
        <div className="notification-sound"><label htmlFor={soundId}>Sound</label><select id={soundId} value={settings.sound} disabled={saving} onChange={event => void save({ ...settings, sound: event.target.value as CompletionSound })}><option value="soft">Soft</option><option value="chime">Chime</option><option value="glass">Glass</option></select><button title="Preview sound" aria-label="Preview sound" onClick={() => void playCompletionSound(settings.sound).catch(e => setError(String(e)))}><Play size={14} /></button></div>
      </>}
    </div>}
  </section>;
}
