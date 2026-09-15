import { create } from 'zustand';
import type { AppNotification, NotificationInput, NotificationPublication, NotificationSeverity } from '../../../../desktop/sentinel/src/shared/notifications';
import type { NotificationSettings } from '../../../../desktop/sentinel/src/shared/ipc';
import { playCompletionSound } from './completion-sound';

const defaults: NotificationSettings = { forms: true, completionSounds: true, sound: 'soft', banners: true, inAppBanners: true, alertSounds: true };
const localKey = 'sentinel.notifications.pending';
const settingsKey = 'sentinel.notifications.settings';
const pending = new Map<string, AppNotification>();
let localLoaded = false;
const replaying = new Set<string>();
const identity = (item: Pick<NotificationInput, 'source' | 'key'>) => JSON.stringify([item.source, item.key]);

export const useNotificationStore = create<{
  items: AppNotification[];
  settings: NotificationSettings | null;
  loading: boolean;
  error: string;
  bannerIds: string[];
  inboxOpen: boolean;
}>()(() => ({ items: [], settings: null, loading: true, error: '', bannerIds: [], inboxOpen: false }));

function loadLocal() {
  if (localLoaded) return;
  localLoaded = true;
  try {
    const items = JSON.parse(localStorage.getItem(localKey) ?? '[]') as AppNotification[];
    for (const item of items.slice(0, 200)) {
      if (typeof item.id === 'string' && typeof item.key === 'string' && typeof item.updatedAt === 'string' && typeof item.source === 'string') pending.set(identity(item), item);
    }
  } catch { /* An unavailable browser store must not prevent notification delivery. */ }
}
function saveLocal() {
  try { localStorage.setItem(localKey, JSON.stringify([...pending.values()].slice(0, 200))); } catch { /* Keep the in-memory inbox. */ }
}
function receiveItems(items: AppNotification[]) {
  const keys = new Set(items.map(identity));
  const merged = [...items, ...[...pending.values()].filter(item => !keys.has(identity(item)))]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, 200);
  useNotificationStore.setState(state => ({ items: merged, loading: false,
    bannerIds: state.bannerIds.filter(id => merged.some(item => item.id === id && !item.read && !item.dismissed)) }));
}
function receivePublication({ item, meaningful }: NotificationPublication) {
  if (!meaningful || item.read || item.dismissed || replaying.has(identity(item)) || document.hidden || !document.hasFocus()) return;
  useNotificationStore.setState(state => {
    if (!state.settings?.inAppBanners) return state;
    return { bannerIds: [item.id, ...state.bannerIds.filter(id => id !== item.id)].slice(0, 3) };
  });
}

/** One subscription owns the inbox, live banners, preferences, and attention sounds. */
export function connectNotifications(): () => void {
  loadLocal();
  const api = window.sentinelDesktop;
  if (!api) {
    let settings = defaults;
    try { settings = { ...defaults, ...JSON.parse(localStorage.getItem(settingsKey) ?? '{}') }; } catch { /* Defaults. */ }
    useNotificationStore.setState({ settings });
    receiveItems([]);
    return () => {};
  }
  let disposed = false, changed = false, settingsChanged = false;
  const waiting: NotificationPublication[] = [];
  const applySettings = (settings: NotificationSettings) => {
    const next = { ...defaults, ...settings };
    useNotificationStore.setState({ settings: next, bannerIds: next.inAppBanners ? useNotificationStore.getState().bannerIds : [] });
    waiting.splice(0).forEach(receivePublication);
  };
  const off = [
    api.onNotifications(items => { changed = true; receiveItems(items); useNotificationStore.setState({ error: '' }); }),
    api.onNotificationSettings(settings => { settingsChanged = true; applySettings(settings); }),
    api.onCompletionSound(sound => { void playCompletionSound(sound).catch(() => {}); }),
  ];
  // During a development reload the preload can still belong to the previous
  // desktop process. Pending UI notices are delivered after it restarts.
  if (api.onNotificationPublished) off.push(api.onNotificationPublished(publication => {
    if (useNotificationStore.getState().settings) receivePublication(publication);
    else { waiting.push(publication); if (waiting.length > 3) waiting.shift(); }
  }));
  void api.getNotificationSettings().then(settings => {
    if (!disposed && !settingsChanged) applySettings(settings);
  }).catch(error => { if (!disposed) { applySettings(defaults); useNotificationStore.setState({ error: String(error) }); } });
  void api.getNotifications().then(async items => {
    if (disposed) return;
    if (!changed) receiveItems(items);
    if (!api.publishNotification) return;
    // Flush offline notices without replaying old banners on launch.
    for (const item of [...pending.values()]) {
      if (disposed) break;
      replaying.add(identity(item));
      try {
        const saved = await api.publishNotification(item);
        if (item.dismissed || item.read) await api.updateNotification(saved.id, item.dismissed ? 'dismiss' : 'read');
        pending.delete(identity(item));
        saveLocal();
      } catch { break; }
      finally { replaying.delete(identity(item)); }
    }
  }).catch(error => { if (!disposed) { receiveItems([]); useNotificationStore.setState({ error: String(error) }); } });
  return () => { disposed = true; off.forEach(unsubscribe => unsubscribe()); };
}

function publishLocal(input: NotificationInput): AppNotification {
  loadLocal();
  const key = input.key ?? crypto.randomUUID();
  const pendingKey = identity({ ...input, key });
  const previous = pending.get(pendingKey);
  const now = new Date().toISOString();
  const item: AppNotification = { ...input, key, id: previous?.id ?? `local-${crypto.randomUUID()}`, severity: input.severity ?? 'info', progress: input.progress ?? false,
    createdAt: previous?.createdAt ?? now, updatedAt: now, read: false, dismissed: false };
  pending.delete(pendingKey);
  pending.set(pendingKey, item);
  if (pending.size > 200) pending.delete(pending.keys().next().value!);
  saveLocal();
  const current = useNotificationStore.getState().items.filter(other => other.id !== item.id && identity(other) !== pendingKey);
  receiveItems([item, ...current]);
  receivePublication({ item, meaningful: true });
  return item;
}

/** All producers publish to the same durable inbox; banners are only a presentation. */
export async function publishNotification(input: NotificationInput): Promise<AppNotification> {
  const notification = { ...input, key: input.key ?? crypto.randomUUID() };
  if (window.sentinelDesktop?.publishNotification) {
    try { return await window.sentinelDesktop.publishNotification(notification); }
    catch { /* Keep the notice until desktop delivery is available again. */ }
  }
  return publishLocal(notification);
}

export function hideNotificationBanner(id: string): void {
  useNotificationStore.setState(state => ({ bannerIds: state.bannerIds.filter(value => value !== id) }));
}
export async function updateNotification(id: string | null, action: 'read' | 'dismiss'): Promise<void> {
  for (const [key, item] of pending) if (id === null || item.id === id) pending.set(key, { ...item, read: true, dismissed: action === 'dismiss' || item.dismissed });
  saveLocal();
  if (window.sentinelDesktop && (id === null || !id.startsWith('local-'))) await window.sentinelDesktop.updateNotification(id, action);
  receiveItems(useNotificationStore.getState().items.map(item => id === null || item.id === id ? { ...item, read: true, dismissed: action === 'dismiss' || item.dismissed } : item));
}
export async function saveNotificationSettings(settings: NotificationSettings): Promise<void> {
  const saved = window.sentinelDesktop ? await window.sentinelDesktop.setNotificationSettings(settings) : settings;
  if (!window.sentinelDesktop) try { localStorage.setItem(settingsKey, JSON.stringify(saved)); } catch { /* Memory still works. */ }
  useNotificationStore.setState({ settings: saved, bannerIds: saved.inAppBanners ? useNotificationStore.getState().bannerIds : [] });
}

/** Lightweight feedback publishers share the same API as agent and workspace notices. */
export function notificationPublisher(source: string) {
  const recent = new Map<string, number>();
  function send(severity: NotificationSeverity, message: string, options: { duration?: number; source?: string } = {}) {
    const text = String(message).trim() || 'Update';
    const fingerprint = `${options.source ?? source}:${severity}:${text}`;
    const previous = recent.get(fingerprint);
    if (previous && Date.now() - previous < 3000) return;
    const key = crypto.randomUUID();
    recent.set(fingerprint, Date.now());
    if (recent.size > 50) recent.delete(recent.keys().next().value!);
    void publishNotification({ source: options.source ?? source, key, severity,
      title: text.length <= 160 ? text : severity === 'error' ? 'Action needs attention' : 'Update',
      message: text.length <= 160 ? '' : text.slice(0, 4000),
      durationMs: options.duration === undefined ? undefined : Math.max(1000, Math.min(30000, options.duration)),
    });
  }
  return {
    success: (message: string, options?: { duration?: number; source?: string }) => send('success', message, options),
    error: (message: string, options?: { duration?: number; source?: string }) => send('error', message, options),
    info: (message: string, options?: { duration?: number; source?: string }) => send('info', message, options),
    warning: (message: string, options?: { duration?: number; source?: string }) => send('warning', message, options),
  };
}
