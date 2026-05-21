import { useCallback, useRef, useState } from 'react';
import { AlertTriangle, Trash2 } from 'lucide-react';

type SessionDeleteConfirmRequest =
  | {
      kind: 'single';
      label: string;
      topLevelEntries: string[];
    }
  | {
      kind: 'bulk';
      sessionCount: number;
      workspaceSessionCount: number;
      topLevelEntries: string[];
    }
  | {
      kind: 'workspace_wipe';
      label: string;
      topLevelEntries: string[];
    };

type SessionDeleteConfirmDialogProps = {
  request: SessionDeleteConfirmRequest | null;
  onCancel: () => void;
  onConfirm: () => void;
};

function SessionDeleteConfirmDialog({
  request,
  onCancel,
  onConfirm,
}: SessionDeleteConfirmDialogProps) {
  if (!request) return null;

  const isBulk = request.kind === 'bulk';
  const isWorkspaceWipe = request.kind === 'workspace_wipe';
  const title = isWorkspaceWipe
    ? 'Wipe runtime workspace?'
    : isBulk ? 'Delete sessions and workspaces?' : 'Delete session and workspace?';
  const description = isWorkspaceWipe
    ? `This will permanently delete runtime workspace files for "${request.label}". The chat session and messages will be kept.`
    : isBulk
    ? `This will permanently delete ${request.sessionCount} selected sessions, their messages, and runtime workspace files for ${request.workspaceSessionCount} session${request.workspaceSessionCount === 1 ? '' : 's'}.`
    : `This will permanently delete "${request.label}", all messages in the session, and this session's runtime workspace files.`;
  const workspaceCopy = isWorkspaceWipe
    ? 'Workspace files will be removed from disk. Any files the agent created, edited, cloned, downloaded, or generated in this session workspace will be deleted.'
    : isBulk
    ? `Workspace files will be removed from disk for ${request.workspaceSessionCount} selected session${request.workspaceSessionCount === 1 ? '' : 's'}. Any files the agent created, edited, cloned, downloaded, or generated in those workspaces will be deleted.`
    : 'Workspace files will be removed from disk. Any files the agent created, edited, cloned, downloaded, or generated in this session workspace will be deleted.';
  const actionLabel = isWorkspaceWipe
    ? 'Wipe workspace'
    : isBulk ? 'Delete sessions and workspaces' : 'Delete session and workspace';
  const previewLabel = isBulk
    ? 'Top-level workspace files and folders found'
    : 'Top-level workspace files and folders';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative z-10 w-full max-w-md overflow-hidden rounded-xl border border-rose-500/30 bg-[color:var(--surface-0)] shadow-2xl animate-in zoom-in-95 duration-150">
        <header className="flex items-start gap-3 border-b border-[color:var(--border-subtle)] bg-rose-500/5 px-5 py-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-400">
            <AlertTriangle size={18} />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-bold uppercase tracking-widest text-[color:var(--text-primary)]">
              {title}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-[color:var(--text-secondary)]">
              {description}
            </p>
          </div>
        </header>

        <div className="space-y-4 px-5 py-4">
          <div className="rounded-lg border border-rose-500/25 bg-rose-500/10 p-3">
            <div className="flex items-start gap-2.5">
              <Trash2 size={15} className="mt-0.5 shrink-0 text-rose-400" />
              <p className="text-xs font-semibold leading-relaxed text-rose-200">
                {workspaceCopy}
              </p>
            </div>
          </div>
          <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
              {previewLabel}
            </p>
            {request.topLevelEntries.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {request.topLevelEntries.map((entry) => (
                  <span
                    key={entry}
                    className="max-w-full truncate rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 py-1 font-mono text-[11px] text-[color:var(--text-secondary)]"
                  >
                    {entry}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-xs leading-relaxed text-[color:var(--text-muted)]">
                No top-level files or folders were found in the preview, but workspace files may still be deleted.
              </p>
            )}
          </div>
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-rose-300">
            This cannot be undone.
          </p>
        </div>

        <footer className="flex justify-end gap-2 border-t border-[color:var(--border-subtle)] px-5 py-4">
          <button type="button" onClick={onCancel} className="btn-secondary h-9 px-4 text-xs">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="btn-primary h-9 border-rose-500 bg-rose-500 px-4 text-xs hover:bg-rose-600"
          >
            {actionLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function useSessionDeleteConfirmation() {
  const [request, setRequest] = useState<SessionDeleteConfirmRequest | null>(null);
  const resolverRef = useRef<((confirmed: boolean) => void) | null>(null);

  const confirmSessionDelete = useCallback((nextRequest: SessionDeleteConfirmRequest) => {
    setRequest(nextRequest);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, []);

  const close = useCallback((confirmed: boolean) => {
    resolverRef.current?.(confirmed);
    resolverRef.current = null;
    setRequest(null);
  }, []);

  return {
    confirmSessionDelete,
    sessionDeleteConfirmDialog: (
      <SessionDeleteConfirmDialog
        request={request}
        onCancel={() => close(false)}
        onConfirm={() => close(true)}
      />
    ),
  };
}
