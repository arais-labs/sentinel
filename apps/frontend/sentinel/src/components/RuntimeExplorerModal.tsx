import { X } from 'lucide-react';

import type { RuntimeLiveView, RuntimeStatusResponse, Session } from '../types/api';

interface RuntimeExplorerModalProps {
  open: boolean;
  session: Session | null;
  runtime: RuntimeLiveView | RuntimeStatusResponse | null;
  onClose: () => void;
}

export function RuntimeExplorerModal({ open, session, runtime, onClose }: RuntimeExplorerModalProps) {
  if (!open) return null;
  const provider = runtime && 'provider' in runtime ? runtime.provider : null;
  const runtimeStatus = runtime && 'status' in runtime ? runtime : null;
  const status = runtimeStatus?.status ?? provider?.status ?? null;
  const targetItems = runtimeStatus
    ? [
        { key: 'runtime', label: 'Runtime', value: runtimeStatus.runtime.name || runtimeStatus.runtime.host || '-' },
        { key: 'host', label: 'Host', value: runtimeStatus.runtime.host || '-' },
        { key: 'user', label: 'User', value: runtimeStatus.runtime.username || '-' },
        { key: 'workspaces', label: 'Workspaces', value: runtimeStatus.runtime.workspaces_dir || '-' },
        { key: 'os', label: 'OS', value: runtimeStatus.os },
        { key: 'sandbox', label: 'Sandbox', value: runtimeStatus.sandbox },
      ]
    : [];
  const items = provider?.items ?? targetItems;

  return (
    <div className="fixed inset-0 z-[900] flex items-center justify-center bg-black/60 p-6">
      <div className="w-full max-w-2xl overflow-hidden rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[color:var(--border-subtle)] px-4 py-3">
          <div>
            <div className="text-xs font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              Runtime
            </div>
            <div className="mt-1 text-sm font-semibold text-[color:var(--text-primary)]">
              {session?.title || session?.id || 'Session runtime'}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-2 text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)]"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3 p-4">
          <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3">
            <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              Provider
            </div>
            <div className="mt-2 text-sm font-semibold text-[color:var(--text-primary)]">
              {provider?.label || runtimeStatus?.runtime.name || 'Runtime'}
            </div>
            <div className="mt-1 text-xs leading-relaxed text-[color:var(--text-secondary)]">
              {provider?.summary || runtimeStatus?.summary || `Runtime is ${status || 'available'}.`}
            </div>
          </div>
          {items.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {items.map((item) => (
                <div key={item.key} className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3">
                  <div className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                    {item.label}
                  </div>
                  <div className="mt-1 truncate font-mono text-xs text-[color:var(--text-primary)]">
                    {item.value || '-'}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
