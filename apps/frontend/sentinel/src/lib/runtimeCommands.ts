import type { SessionRuntimeAction, SessionRuntimeStatus } from '../types/api';

const START_ACTIONS = new Set(['command_started', 'detached_job_started']);
const FINISH_ACTIONS = new Set(['command_finished', 'detached_job_finished', 'detached_job_stopped']);

function actionKey(entry: SessionRuntimeAction, index: number): string | null {
  if (entry.action === 'command_started') return 'command:active';
  if (entry.action === 'detached_job_started') {
    const jobId = typeof entry.details.job_id === 'string' ? entry.details.job_id.trim() : '';
    if (jobId) return `detached:${jobId}`;
    const pid = typeof entry.details.pid === 'number' ? entry.details.pid : null;
    if (pid && Number.isFinite(pid) && pid > 0) return `detached:pid:${pid}`;
    return `detached:index:${index}`;
  }
  if (entry.action === 'detached_job_finished' || entry.action === 'detached_job_stopped') {
    const jobId = typeof entry.details.job_id === 'string' ? entry.details.job_id.trim() : '';
    if (jobId) return `detached:${jobId}`;
    const pid = typeof entry.details.pid === 'number' ? entry.details.pid : null;
    if (pid && Number.isFinite(pid) && pid > 0) return `detached:pid:${pid}`;
  }
  if (entry.action === 'command_finished') return 'command:active';
  return null;
}

export function runtimeActionCommand(entry: SessionRuntimeAction): string | null {
  if (!entry || !entry.details || typeof entry.details !== 'object') return null;
  const command = entry.details.command;
  return typeof command === 'string' && command.trim().length > 0 ? command.trim() : null;
}

export type RuntimeCommandState = 'running' | 'completed' | 'failed' | 'cancelled';

export interface RuntimeCommandRow {
  command: string;
  source: 'command' | 'detached_job';
  state: RuntimeCommandState;
  startedAt: string | null;
  endedAt: string | null;
}

type BuildRuntimeCommandRowsOptions = {
  newestFirst?: boolean;
  limit?: number;
};

export function buildRuntimeCommandRows(
  runtimeStatus: SessionRuntimeStatus | null,
  options: BuildRuntimeCommandRowsOptions = {},
): RuntimeCommandRow[] {
  if (!runtimeStatus) return [];
  const actions = Array.isArray(runtimeStatus.actions) ? runtimeStatus.actions : [];
  if (actions.length === 0) return [];

  const pendingByKey = new Map<string, RuntimeCommandRow[]>();
  const rows: RuntimeCommandRow[] = [];

  function queuePending(key: string, row: RuntimeCommandRow): void {
    const queue = pendingByKey.get(key);
    if (queue) {
      queue.push(row);
      return;
    }
    pendingByKey.set(key, [row]);
  }

  function takePending(key: string): RuntimeCommandRow | null {
    const queue = pendingByKey.get(key);
    if (!queue || queue.length === 0) return null;
    const row = queue.shift() ?? null;
    if (queue.length === 0) {
      pendingByKey.delete(key);
    }
    return row;
  }

  function finishState(entry: SessionRuntimeAction): RuntimeCommandState {
    if (entry.action === 'detached_job_stopped') return 'cancelled';
    if (entry.action === 'detached_job_finished') {
      const status = typeof entry.details.status === 'string' ? entry.details.status.trim().toLowerCase() : '';
      if (status === 'failed') return 'failed';
      if (status === 'cancelled') return 'cancelled';
      return 'completed';
    }
    return 'completed';
  }

  for (let index = 0; index < actions.length; index += 1) {
    const entry = actions[index];
    const key = actionKey(entry, index);
    if (START_ACTIONS.has(entry.action)) {
      if (!key) continue;
      const command = runtimeActionCommand(entry);
      if (!command) continue;
      queuePending(key, {
        command,
        source: entry.action === 'detached_job_started' ? 'detached_job' : 'command',
        state: 'running',
        startedAt: entry.timestamp,
        endedAt: null,
      });
      continue;
    }

    if (!FINISH_ACTIONS.has(entry.action) || !key) continue;
    const pending = takePending(key);
    if (pending) {
      pending.state = finishState(entry);
      pending.endedAt = entry.timestamp;
      rows.push(pending);
      continue;
    }

    const command = runtimeActionCommand(entry);
    if (!command) continue;
    rows.push({
      command,
      source: entry.action.startsWith('detached_job_') ? 'detached_job' : 'command',
      state: finishState(entry),
      startedAt: null,
      endedAt: entry.timestamp,
    });
  }

  for (const pendingRows of pendingByKey.values()) {
    for (const pending of pendingRows) {
      rows.push(pending);
    }
  }

  const newestFirst = options.newestFirst !== false;
  const ordered = newestFirst ? rows.slice().reverse() : rows.slice();
  const normalizedLimit = Math.max(1, options.limit ?? 50);
  return ordered.slice(0, normalizedLimit);
}
