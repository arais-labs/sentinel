import { ApiError, api } from './api';
import type { SessionRuntimeFilesResponse } from '../types/api';

export type SessionDeleteWorkspaceSummary = {
  needsConfirmation: boolean;
  topLevelEntries: string[];
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
