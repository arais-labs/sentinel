import { ApiError, api } from './api';
import type { SessionRuntimeFilesResponse, TriggerListResponse } from '../types/api';

export type SessionDeleteWorkspaceSummary = {
  needsConfirmation: boolean;
  topLevelEntries: string[];
};

export type SessionDeleteTriggerSummary = {
  triggerCount: number;
  triggerNames: string[];
};

function hasUserWorkspaceEntries(payload: SessionRuntimeFilesResponse): boolean {
  if (!payload.workspace_exists) return false;
  return payload.entries.some((entry) => entry.name !== '.runtime') || payload.truncated;
}

function topLevelWorkspaceEntries(payload: SessionRuntimeFilesResponse): string[] {
  if (!payload.workspace_exists) return [];
  return payload.entries
    .filter((entry) => entry.name !== '.runtime')
    .map((entry) => (entry.kind === 'directory' ? `${entry.name}/` : entry.name))
    .slice(0, 10);
}

export async function getSessionDeleteWorkspaceSummary(
  sessionId: string,
): Promise<SessionDeleteWorkspaceSummary> {
  try {
    const payload = await api.get<SessionRuntimeFilesResponse>(
      `/sessions/${sessionId}/runtime/files?limit=200`,
      { timeoutMs: 5_000 },
    );
    return {
      needsConfirmation: hasUserWorkspaceEntries(payload),
      topLevelEntries: topLevelWorkspaceEntries(payload),
    };
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return { needsConfirmation: false, topLevelEntries: [] };
    }
    return { needsConfirmation: true, topLevelEntries: [] };
  }
}

function triggerTargetsSession(trigger: unknown, sessionIds: Set<string>): boolean {
  if (!trigger || typeof trigger !== 'object') return false;
  const item = trigger as {
    enabled?: unknown;
    action_type?: unknown;
    action_config?: unknown;
  };
  if (item.enabled !== true || item.action_type !== 'agent_message') return false;
  const actionConfig = item.action_config;
  if (!actionConfig || typeof actionConfig !== 'object') return false;
  const target = (actionConfig as { target_session_id?: unknown }).target_session_id;
  return typeof target === 'string' && sessionIds.has(target);
}

export async function getSessionDeleteTriggerSummary(
  sessionIds: string[],
): Promise<SessionDeleteTriggerSummary> {
  const targetIds = new Set(sessionIds);
  if (targetIds.size === 0) return { triggerCount: 0, triggerNames: [] };

  const names: string[] = [];
  let offset = 0;
  const limit = 100;
  while (true) {
    const payload = await api.get<TriggerListResponse>(
      `/triggers?enabled=true&limit=${limit}&offset=${offset}`,
    );
    const items = Array.isArray(payload.items) ? payload.items : [];
    for (const trigger of items) {
      if (triggerTargetsSession(trigger, targetIds)) {
        names.push(trigger.name || trigger.id);
      }
    }
    offset += items.length;
    if (items.length < limit || offset >= payload.total) break;
  }

  return {
    triggerCount: names.length,
    triggerNames: names.slice(0, 10),
  };
}
