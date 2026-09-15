import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { AlertTriangle, Trash2 } from 'lucide-react';

type SessionDeleteConfirmRequest =
  | {
      kind: 'single';
      label: string;
    }
  | {
      kind: 'bulk';
      sessionCount: number;
    }
  | {
      kind: 'session_reset';
      label: string;
    }
  | {
      kind: 'trigger_targets';
      label?: string;
      sessionCount: number;
      triggerCount: number;
      triggerNames: string[];
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
  const confirmButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    if (!request) return;
    const previous = document.activeElement;
    confirmButton.current?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [request]);
  if (!request) return null;

  const isBulk = request.kind === 'bulk';
  const isWorkspaceWipe = request.kind === 'session_reset';
  const isTriggerTargets = request.kind === 'trigger_targets';
  const title = isWorkspaceWipe ? 'Reset session environment?' : isTriggerTargets ? 'Delete trigger target?' : isBulk ? 'Delete sessions?' : 'Delete session?';
  const description = isWorkspaceWipe
    ? `This resets terminals, browser data, and private session state for "${request.label}". Messages and shared workspace files are kept.`
    : isTriggerTargets
    ? `${request.sessionCount} session(s) are targeted by ${request.triggerCount} enabled trigger(s).`
    : isBulk ? `Permanently delete ${request.sessionCount} sessions and their messages?` : `Permanently delete "${request.label}" and its messages?`;
  const workspaceCopy = 'Shared workspace files and the workspace registration are kept.';
  const actionLabel = isWorkspaceWipe ? 'Reset environment' : isTriggerTargets ? 'Continue' : isBulk ? 'Delete sessions' : 'Delete session';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-xs" onClick={onCancel} />
      <div data-session-delete-confirm role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId}
        onKeyDown={event => {
          event.stopPropagation();
          if (event.key === 'Escape') { event.preventDefault(); onCancel(); }
          if (event.key === 'Enter' && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) {
            event.preventDefault();
            if (!event.repeat) {
              if (event.target instanceof HTMLElement && event.target.dataset.cancel !== undefined) onCancel();
              else onConfirm();
            }
          }
          if (event.key === 'Tab') {
            const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
            const first = buttons[0], last = buttons.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}
        className="relative z-10 w-full max-w-md overflow-hidden rounded-xl border border-rose-500/30 bg-(--surface-0) shadow-2xl animate-in zoom-in-95 duration-150">
        <header className="flex items-start gap-3 border-b border-(--border-subtle) bg-rose-500/5 px-5 py-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-400">
            <AlertTriangle size={18} />
          </div>
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-bold uppercase tracking-widest text-(--text-primary)">
              {title}
            </h2>
            <p id={descriptionId} className="mt-1 text-xs leading-relaxed text-(--text-secondary)">
              {description}
            </p>
          </div>
        </header>

        <div className="space-y-4 px-5 py-4">
          <div className="rounded-lg border border-rose-500/25 bg-rose-500/10 p-3">
            <div className="flex items-start gap-2.5">
              <Trash2 size={15} className="mt-0.5 shrink-0 text-rose-400" />
              <p className="text-xs font-semibold leading-relaxed text-rose-200">
                {isTriggerTargets
                  ? 'Affected triggers will not run until each trigger is edited to target another session.'
                  : workspaceCopy}
              </p>
            </div>
          </div>
          {isTriggerTargets ? (
            <div className="rounded-lg border border-(--border-subtle) bg-(--surface-1) p-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-(--text-muted)">
                Affected triggers
              </p>
              {request.triggerNames.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {request.triggerNames.map((entry) => (
                    <span
                      key={entry}
                      className="max-w-full truncate rounded border border-(--border-subtle) bg-(--surface-0) px-2 py-1 text-[11px] text-(--text-secondary)"
                    >
                      {entry}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-xs leading-relaxed text-(--text-muted)">
                  Trigger names could not be previewed.
                </p>
              )}
            </div>
          ) : null}
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-rose-300">
            This cannot be undone.
          </p>
        </div>

        <footer className="flex justify-end gap-2 border-t border-(--border-subtle) px-5 py-4">
          <button type="button" data-cancel onClick={onCancel} className="btn-secondary h-9 px-4 text-xs">
            Cancel <kbd className="ml-2 text-[10px] opacity-60">Esc</kbd>
          </button>
          <button
            type="button"
            ref={confirmButton}
            onClick={onConfirm}
            className="btn-primary h-9 border-rose-500 bg-rose-500 px-4 text-xs hover:bg-rose-600"
          >
            {actionLabel} <kbd className="ml-2 text-[10px] opacity-60">↵</kbd>
          </button>
        </footer>
      </div>
    </div>
  );
}

export function useSessionDeleteConfirmation() {
  const [request, setRequest] = useState<SessionDeleteConfirmRequest | null>(null);
  const resolverRef = useRef<((confirmed: boolean) => void) | null>(null);
  useEffect(() => () => { resolverRef.current?.(false); resolverRef.current = null; }, []);

  const confirmSessionDelete = useCallback((nextRequest: SessionDeleteConfirmRequest) => {
    resolverRef.current?.(false);
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
