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

export interface RuntimeCommandRow {
  entry: SessionRuntimeAction;
  command: string;
  isRunning: boolean;
  isStartEvent: boolean;
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
  const activeKeys = new Set<string>();
  const rows: Array<RuntimeCommandRow & { key: string | null }> = [];

  for (let index = 0; index < actions.length; index += 1) {
    const entry = actions[index];
    const command = runtimeActionCommand(entry);
    const key = actionKey(entry, index);
    const isStartEvent = START_ACTIONS.has(entry.action);

    if (isStartEvent && key) {
      activeKeys.add(key);
    } else if (FINISH_ACTIONS.has(entry.action) && key) {
      activeKeys.delete(key);
    }

    if (!command) continue;
    rows.push({
      entry,
      command,
      isRunning: false,
      isStartEvent,
      key,
    });
  }

  for (const row of rows) {
    row.isRunning = row.isStartEvent && row.key !== null && activeKeys.has(row.key);
  }

  const newestFirst = options.newestFirst !== false;
  const ordered = newestFirst ? rows.slice().reverse() : rows.slice();
  const normalizedLimit = Math.max(1, options.limit ?? 50);
  return ordered.slice(0, normalizedLimit).map(({ key: _ignored, ...row }) => row);
}
