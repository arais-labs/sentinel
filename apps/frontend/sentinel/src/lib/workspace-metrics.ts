import { api } from './api';

export type WorkspaceMetrics = {
  state: string;
  uptime?: number;
  resources?: { cpus: number; memory_gib: number; disk_gib: number };
  cpu_percent?: number | null;
  memory_total_bytes?: number;
  memory_used_bytes?: number;
  disk_total_bytes?: number;
  disk_used_bytes?: number;
  network_receive_bytes_per_second?: number | null;
  network_send_bytes_per_second?: number | null;
};
export type MetricSample = { at: number; data: WorkspaceMetrics };
export type MetricsSnapshot = { data?: WorkspaceMetrics; history: MetricSample[]; updatedAt?: number; unavailable: boolean };
export const HISTORY_MS = 5 * 60_000;
const POLL_MS = 3000;
const EMPTY: MetricsSnapshot = { history: [], unavailable: false };

type Entry = {
  snapshot: MetricsSnapshot;
  listeners: Set<() => void>;
  timer?: ReturnType<typeof setTimeout>;
  pending: boolean;
  attemptedAt: number;
};
const entries = new Map<string, Entry>();
const keyFor = (instance: string, workspace: string) => JSON.stringify([instance, workspace]);

export function metricsSnapshot(instance: string, workspace: string): MetricsSnapshot {
  return entries.get(keyFor(instance, workspace))?.snapshot ?? EMPTY;
}

/** One sampler per observed workspace, independent of whether its popover is open. */
export function subscribeMetrics(instance: string, workspace: string, listener: () => void) {
  const key = keyFor(instance, workspace);
  let entry = entries.get(key);
  if (!entry) {
    // Retain recently visited workspaces, without accumulating unbounded history.
    for (const [oldKey, old] of entries) {
      if (entries.size < 16) break;
      if (!old.listeners.size && !old.pending) entries.delete(oldKey);
    }
    entry = { snapshot: EMPTY, listeners: new Set(), pending: false, attemptedAt: 0 };
    entries.set(key, entry);
  }
  const current = entry;
  const wasInactive = current.listeners.size === 0;
  current.listeners.add(listener);
  const schedule = () => {
    clearTimeout(current.timer);
    if (current.listeners.size && !document.hidden) {
      const delay = current.snapshot.unavailable ? 15_000 : POLL_MS;
      current.timer = setTimeout(poll, Math.max(0, delay - (Date.now() - current.attemptedAt)));
    }
  };
  const poll = async () => {
    if (!current.listeners.size || document.hidden || current.pending) return;
    current.pending = true;
    current.attemptedAt = Date.now();
    try {
      const data = await api.get<WorkspaceMetrics>(`/instances/${encodeURIComponent(instance)}/workspaces/${encodeURIComponent(workspace)}/metrics`, { timeoutMs: 6500 });
      const at = Date.now();
      const previous = current.snapshot;
      const rebooted = data.uptime != null && previous.data?.uptime != null && data.uptime < previous.data.uptime;
      const unavailable = data.state === 'unavailable';
      current.snapshot = {
        data: unavailable ? previous.data : data,
        updatedAt: unavailable ? previous.updatedAt : at,
        unavailable,
        history: [...(rebooted ? [] : previous.history).filter(sample => sample.at > at - HISTORY_MS), { at, data }].slice(-120),
      };
    } catch {
      const at = Date.now();
      current.snapshot = { ...current.snapshot, unavailable: true,
        history: [...current.snapshot.history.filter(sample => sample.at > at - HISTORY_MS), { at, data: { state: 'unavailable' } }].slice(-120) };
    } finally {
      current.pending = false;
      for (const notify of current.listeners) notify();
      schedule();
    }
  };
  const visibility = () => { if (document.hidden) clearTimeout(current.timer); else schedule(); };
  document.addEventListener('visibilitychange', visibility);
  if (wasInactive) schedule();
  return () => {
    current.listeners.delete(listener);
    document.removeEventListener('visibilitychange', visibility);
    if (!current.listeners.size) clearTimeout(current.timer);
  };
}
