import { useEffect, useState } from 'react';
import { AlertCircle, Terminal } from 'lucide-react';
import { ApprovalActions } from './ApprovalActions';
import { ToolCard } from './ToolCard';
import { ToolPayloadView } from './SessionMessageCard';
import { approvalKey, approvalRefFromMetadata, isWaitingApproval, type ApprovalRef, type ApprovalScope } from '../../lib/approvals';
import type { StreamingToolCall } from '../../pages/sessionStreaming';

export function StreamToolCard({
  call,
  sessionId,
  active,
  onResolveApproval,
  resolvingApprovalKey,
  onOpenPane,
}: {
  call: StreamingToolCall;
  sessionId?: string | null;
  active: boolean;
  onResolveApproval: (approval: ApprovalRef, decision: 'approve' | 'reject', scope?: ApprovalScope) => void;
  resolvingApprovalKey: string | null;
  onOpenPane?: (paneId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isScreenshotCall = call.name.toLowerCase().includes('screenshot');
  const pendingApproval = isWaitingApproval(call.metadata);
  const approvalRef = pendingApproval ? approvalRefFromMetadata(call.metadata) : null;
  const canResolveApproval = pendingApproval && approvalRef?.canResolve === true;
  const approvalLinkMissing = pendingApproval && !approvalRef;
  const approvalActionBusy = approvalRef ? resolvingApprovalKey === approvalKey(approvalRef) : false;
  // When the runtime tool result carries a pane ID, surface a chip in the
  // card header so the user can jump from "what did the agent run?" to "let
  // me see/control that terminal" in one click.
  const paneIdFromMetadata =
    typeof call.metadata?.pane_id === 'string' && call.metadata.pane_id.length > 0
      ? (call.metadata.pane_id as string)
      : null;

  useEffect(() => {
    if (pendingApproval) setExpanded(false);
  }, [pendingApproval]);

  return (
    <div className="flex w-full flex-col gap-1 animate-in items-start">
      <div className="flex items-center gap-2 px-1">
        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-(--text-muted)">
          tool_call
        </span>
        {pendingApproval ? (
          <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-rose-400">• waiting approval</span>
        ) : active ? (
          <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-sky-500/70">• running</span>
        ) : null}
      </div>
      <ToolCard
        name={call.name}
        inputRaw={call.argumentsJson}
        outputRaw={call.outputJson}
        failed={call.isError}
        active={active}
        pending={pendingApproval}
        expanded={expanded}
        onExpand={setExpanded}
        input={<ToolPayloadView raw={call.argumentsJson} emptyLabel="No input." toolName={call.name} payloadKind="input" showRawJson={false} />}
        result={<ToolPayloadView raw={call.outputJson} emptyLabel={active ? 'Running tool…' : 'No output payload.'} toolName={call.name} payloadKind="output" showRawJson={!isScreenshotCall} />}
        headerAction={paneIdFromMetadata && onOpenPane ? <button type="button" onClick={() => onOpenPane(paneIdFromMetadata)} className="tool-card-copy" title="Open terminal"><Terminal size={12} />Terminal</button> : undefined}
        actions={pendingApproval ? <>
                  {canResolveApproval && approvalRef ? (
                    <ApprovalActions busy={approvalActionBusy} sessionId={approvalRef.sessionId ?? sessionId} action={approvalRef.action}
                      onResolve={(decision, scope) => onResolveApproval(approvalRef, decision, scope)} />
                  ) : null}
                  {approvalLinkMissing ? (
                    <div className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/5 border border-amber-500/20">
                      <AlertCircle size={12} className="text-amber-500 shrink-0 mt-0.5" />
                      <p className="text-[9px] leading-relaxed text-amber-400/80 font-medium">
                        Action required but controls are detached.
                      </p>
                    </div>
                  ) : null}
        </> : undefined}
      />
    </div>
  );
}
