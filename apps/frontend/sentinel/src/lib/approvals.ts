import type { ApprovalListResponse } from '../types/api';

export interface ApprovalRef {
  provider: string;
  approvalId: string;
  status: string;
  pending: boolean;
  canResolve: boolean;
  label?: string;
  matchKey?: string | null;
}

export interface ApprovalMatchCandidate {
  provider: string;
  matchKey: string;
}

export function normalizeCommand(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLowerCase();
}

export function isWaitingApproval(metadata: Record<string, unknown>): boolean {
  const approval = metadata.approval;
  if (isObjectRecord(approval)) {
    const approvalId = stringValue(approval.approval_id) ?? stringValue(approval.id);
    const provider = stringValue(approval.provider) ?? stringValue(metadata.approval_provider);
    if (!approvalId || !provider) return false;
    if (approval.pending === true) return true;
    if (typeof approval.status === 'string' && approval.status.trim().toLowerCase() === 'pending') {
      return true;
    }
    return false;
  }
  const legacyId = stringValue(metadata.approval_id);
  const legacyProvider = stringValue(metadata.approval_provider);
  const legacyStatus = stringValue(metadata.approval_status) ?? 'pending';
  return Boolean(legacyId && legacyProvider && legacyStatus.toLowerCase() === 'pending');
}

export function approvalRefFromMetadata(metadata: Record<string, unknown>): ApprovalRef | null {
  const approval = metadata.approval;
  if (isObjectRecord(approval)) {
    const approvalId = stringValue(approval.approval_id) ?? stringValue(approval.id);
    const provider = stringValue(approval.provider) ?? stringValue(metadata.approval_provider);
    if (!approvalId || !provider) return null;
    const status = stringValue(approval.status) ?? 'pending';
    const pending = approval.pending === true || status.toLowerCase() === 'pending';
    const canResolve = approval.can_resolve === true || pending;
    return {
      provider,
      approvalId,
      status,
      pending,
      canResolve,
      label: stringValue(approval.label) ?? undefined,
      matchKey: stringValue(approval.match_key) ?? undefined,
    };
  }

  const legacyId = stringValue(metadata.approval_id);
  if (!legacyId) return null;
  const provider = stringValue(metadata.approval_provider) ?? 'git';
  const status = stringValue(metadata.approval_status) ?? 'pending';
  const pending = status.toLowerCase() === 'pending';
  return {
    provider,
    approvalId: legacyId,
    status,
    pending,
    canResolve: pending,
  };
}

export function approvalKey(ref: ApprovalRef): string {
  return `${ref.provider}:${ref.approvalId}`;
}

export function extractApprovalCandidateFromToolArgs(
  toolName: string,
  value: unknown,
): ApprovalMatchCandidate | null {
  if (toolName !== 'git_exec' || !isObjectRecord(value)) return null;
  const command = typeof value.command === 'string' ? value.command.trim() : '';
  if (!command || !isApprovalGatedGitExecCommand(command)) return null;
  return {
    provider: 'git',
    matchKey: normalizeCommand(command),
  };
}

export function extractApprovalCandidateFromSerializedArgs(
  toolName: string,
  raw: string,
): ApprovalMatchCandidate | null {
  const parsed = parseToolArgumentsObject(raw);
  return parsed ? extractApprovalCandidateFromToolArgs(toolName, parsed) : null;
}

export function selectMatchingPendingApproval(
  items: ApprovalListResponse['items'],
  options: {
    sessionId: string;
    candidate: ApprovalMatchCandidate;
  },
) {
  const { sessionId, candidate } = options;
  const normalizedKey = normalizeCommand(candidate.matchKey);
  const matches = items.filter((item) => {
    if (item.provider !== candidate.provider) return false;
    if (item.pending !== true) return false;
    if (normalizeCommand(item.match_key ?? '') !== normalizedKey) return false;
    if (item.session_id && item.session_id !== sessionId) return false;
    return true;
  });
  if (!matches.length) return null;
  const toEpoch = (value: string | null): number => {
    if (!value) return 0;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  matches.sort((a, b) => {
    const byCreated = toEpoch(b.created_at) - toEpoch(a.created_at);
    if (byCreated !== 0) return byCreated;
    return toEpoch(b.updated_at) - toEpoch(a.updated_at);
  });
  return matches[0];
}

function isApprovalGatedGitExecCommand(command: string): boolean {
  const normalized = normalizeCommand(command);
  if (!normalized) return false;
  if (/^git\s+push(?:\s|$)/i.test(normalized)) return true;
  if (/^gh\s+pr\s+create(?:\s|$)/i.test(normalized)) return true;
  if (/^gh\s+api(?:\s|$)/i.test(normalized)) {
    return /(?:^|\s)(?:-x|--method)(?:\s+|=)post(?:\s|$)/i.test(normalized);
  }
  return false;
}

function parseToolArgumentsObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return isObjectRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function stringValue(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
