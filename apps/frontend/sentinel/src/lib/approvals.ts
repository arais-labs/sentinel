export interface ApprovalRef {
  provider: string;
  approvalId: string;
  status: string;
  pending: boolean;
  canResolve: boolean;
  label?: string;
  matchKey?: string | null;
}

export function isWaitingApproval(metadata: Record<string, unknown>): boolean {
  const approval = metadata.approval;
  if (!isObjectRecord(approval)) return false;
  const provider = stringValue(approval.provider);
  if (!provider) return false;
  if (approval.pending === true) return true;
  if (typeof approval.status === 'string' && approval.status.trim().toLowerCase() === 'pending') {
    return true;
  }
  return false;
}

export function approvalRefFromMetadata(metadata: Record<string, unknown>): ApprovalRef | null {
  const approval = metadata.approval;
  if (!isObjectRecord(approval)) return null;
  const approvalId = stringValue(approval.approval_id);
  const provider = stringValue(approval.provider);
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

export function approvalKey(ref: ApprovalRef): string {
  return `${ref.provider}:${ref.approvalId}`;
}

function stringValue(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
