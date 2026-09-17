import { requestJson } from './api';
import type { ApprovalRef, ApprovalScope } from './approvals';

export const VOICE_SESSION_RESET = 'sentinel:voice-session-reset';

const voicePath = (instance: string) => `/instances/${encodeURIComponent(instance)}/voice`;

export async function fetchVoiceSession(instance: string, signal?: AbortSignal): Promise<string> {
  const { session_id } = await requestJson<{ session_id: string }>(`${voicePath(instance)}/session`, { signal });
  return session_id;
}

/** Discard the Voice conversation. Listeners re-resolve the session and clear captions. */
export async function resetVoiceSession(instance: string): Promise<string> {
  const { session_id } = await requestJson<{ session_id: string }>(`${voicePath(instance)}/session/reset`, { method: 'POST', timeoutMs: 20_000 });
  window.dispatchEvent(new CustomEvent(VOICE_SESSION_RESET, { detail: { instance, sessionId: session_id } }));
  return session_id;
}

export function stopVoiceRun(instance: string, sessionId: string): Promise<unknown> {
  return requestJson(`/instances/${encodeURIComponent(instance)}/sessions/${sessionId}/stop`, { method: 'POST', timeoutMs: 10_000 });
}

export function resolveVoiceApproval(instance: string, approval: ApprovalRef, decision: 'approve' | 'reject', scope: ApprovalScope = 'once'): Promise<unknown> {
  return requestJson(`/instances/${encodeURIComponent(instance)}/approvals/${encodeURIComponent(approval.provider)}/${encodeURIComponent(approval.approvalId)}/${decision}`, {
    method: 'POST', body: { scope, note: decision === 'approve' ? 'User approved from Voice.' : 'User rejected from Voice.' }, timeoutMs: 20_000,
  });
}

/** The message shape the session stream accepts; the server forces Voice mode for this session. */
export function voiceMessagePayload(content: string, selection: Record<string, unknown>): Record<string, unknown> {
  return { type: 'message', content, agent_mode: 'voice', ...selection };
}
