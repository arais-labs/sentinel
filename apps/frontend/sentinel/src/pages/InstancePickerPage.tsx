import { Activity, Check, Database, LayoutDashboard, Loader2, LogOut, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { Panel } from '../components/ui/Panel';
import { api } from '../lib/api';
import { useAuthStore } from '../store/auth-store';

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
}

interface AuditEvent {
  id: string;
  timestamp: string;
  user_id: string | null;
  action: string;
  status_code: number | null;
  ip_address: string | null;
}

interface AuditLogList {
  items: AuditEvent[];
  total: number;
}

declare global {
  interface Window {
    sentinelDesktop?: {
      showControlCenter(): Promise<void>;
    };
  }
}

export function InstancePickerPage() {
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);
  const desktopApi = typeof window !== 'undefined' ? window.sentinelDesktop : undefined;
  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  // Inline rename state.
  const [renamingName, setRenamingName] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [pendingRename, setPendingRename] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Delete-confirmation modal state.
  const [deleteTarget, setDeleteTarget] = useState<SentinelInstance | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [pendingDelete, setPendingDelete] = useState(false);

  // Recent manager-scoped audit events.
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditLoading, setAuditLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      setInstances(await api.get<SentinelInstance[]>('/instances'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load instances');
    } finally {
      setLoading(false);
    }
  };

  const loadAudit = async () => {
    setAuditLoading(true);
    try {
      const data = await api.get<AuditLogList>('/admin/audit?limit=15');
      setAuditEvents(data.items);
    } catch {
      // Silent — audit pane is non-critical; surface empty state instead.
      setAuditEvents([]);
    } finally {
      setAuditLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void loadAudit();
  }, []);

  useEffect(() => {
    if (renamingName && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingName]);

  const openInstance = (instanceName: string) => {
    navigate(`/instances/${encodeURIComponent(instanceName)}/sessions`);
  };

  const createInstance = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    try {
      const instance = await api.post<SentinelInstance>('/instances', { name: trimmed });
      setName('');
      setInstances((current) =>
        [...current.filter((row) => row.name !== instance.name), instance].sort((a, b) =>
          a.name.localeCompare(b.name),
        ),
      );
      toast.success(`Created instance “${instance.name}”`);
      openInstance(instance.name);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create instance');
    } finally {
      setCreating(false);
    }
  };

  const startRename = (instance: SentinelInstance) => {
    setRenamingName(instance.name);
    setRenameValue(instance.name);
  };

  const cancelRename = () => {
    setRenamingName(null);
    setRenameValue('');
  };

  const submitRename = async () => {
    if (!renamingName) return;
    const newName = renameValue.trim();
    if (!newName || newName === renamingName) {
      cancelRename();
      return;
    }
    setPendingRename(true);
    try {
      const updated = await api.post<SentinelInstance>(
        `/instances/${encodeURIComponent(renamingName)}/rename`,
        { name: newName },
      );
      setInstances((current) =>
        current
          .map((row) => (row.name === renamingName ? updated : row))
          .sort((a, b) => a.name.localeCompare(b.name)),
      );
      toast.success(`Renamed to “${updated.name}”`);
      cancelRename();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to rename instance');
    } finally {
      setPendingRename(false);
    }
  };

  const handleRenameKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void submitRename();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelRename();
    }
  };

  const askDelete = (instance: SentinelInstance) => {
    setDeleteTarget(instance);
    setDeleteConfirm('');
  };

  const cancelDelete = () => {
    setDeleteTarget(null);
    setDeleteConfirm('');
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deleteConfirm !== deleteTarget.name) return;
    setPendingDelete(true);
    try {
      await api.delete(`/instances/${encodeURIComponent(deleteTarget.name)}`);
      const removed = deleteTarget.name;
      setInstances((current) => current.filter((row) => row.name !== removed));
      toast.success(`Deleted instance “${removed}”`);
      cancelDelete();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to delete instance');
    } finally {
      setPendingDelete(false);
    }
  };

  const refreshing = loading;

  return (
    <AppShell
      title="Instances"
      subtitle="Choose a Sentinel workspace"
      actions={
        <div className="flex items-center gap-2">
          {desktopApi?.showControlCenter && (
            <button
              type="button"
              onClick={() => void desktopApi.showControlCenter()}
              className="inline-flex h-9 items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-3 text-sm text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]"
            >
              <LayoutDashboard size={15} />
              Control Center
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              void load();
              void loadAudit();
            }}
            disabled={refreshing}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-3 text-sm text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] disabled:opacity-60"
          >
            <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void logout()}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-3 text-sm text-[color:var(--text-secondary)] hover:bg-rose-500/10 hover:text-rose-500 hover:border-rose-500/30"
            title="Sign out"
          >
            <LogOut size={15} />
            Sign out
          </button>
        </div>
      }
      hideSidebar
      contentClassName="max-w-3xl w-full mx-auto"
    >
      <div className="space-y-6">
        <Panel className="p-5">
          <form onSubmit={createInstance} className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1 min-w-0 space-y-1.5">
              <label
                htmlFor="instance-name"
                className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]"
              >
                New instance
              </label>
              <input
                id="instance-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. main, sandbox, research"
                className="h-10 w-full rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-sm outline-none focus:border-[color:var(--accent-solid)]"
              />
            </div>
            <button
              type="submit"
              disabled={creating || !name.trim()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[color:var(--accent-solid)] px-4 text-sm font-medium text-[color:var(--app-bg)] disabled:opacity-60"
            >
              {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
              Create
            </button>
          </form>
        </Panel>

        <Panel className="overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-[color:var(--border-subtle)]">
            <h2 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              Registered instances
            </h2>
            <span className="text-xs text-[color:var(--text-muted)]">
              {loading
                ? 'Loading…'
                : `${instances.length} ${instances.length === 1 ? 'instance' : 'instances'}`}
            </span>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 px-5 py-10 text-sm text-[color:var(--text-muted)]">
              <Loader2 size={16} className="animate-spin" />
              Loading instances…
            </div>
          ) : instances.length === 0 ? (
            <div className="px-5 py-12 text-center text-sm text-[color:var(--text-muted)]">
              No instances registered yet. Create one above to get started.
            </div>
          ) : (
            <ul className="divide-y divide-[color:var(--border-subtle)]">
              {instances.map((instance) => {
                const isRenaming = renamingName === instance.name;
                const primaryLabel = instance.display_name || instance.name;
                return (
                  <li
                    key={instance.database_name}
                    role={isRenaming ? undefined : 'button'}
                    tabIndex={isRenaming ? -1 : 0}
                    onClick={isRenaming ? undefined : () => openInstance(instance.name)}
                    onKeyDown={
                      isRenaming
                        ? undefined
                        : (event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              openInstance(instance.name);
                            }
                          }
                    }
                    className={`group grid grid-cols-[auto_1fr_auto] items-center gap-3 px-5 py-4 outline-none ${
                      isRenaming
                        ? 'bg-[color:var(--surface-1)]'
                        : 'cursor-pointer hover:bg-[color:var(--surface-1)] focus-visible:bg-[color:var(--surface-1)]'
                    }`}
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]">
                      <Database size={18} />
                    </div>

                    <div className="min-w-0">
                      {isRenaming ? (
                        <div className="flex items-center gap-2">
                          <input
                            ref={renameInputRef}
                            value={renameValue}
                            onChange={(event) => setRenameValue(event.target.value)}
                            onKeyDown={handleRenameKey}
                            onClick={(event) => event.stopPropagation()}
                            disabled={pendingRename}
                            className="h-9 flex-1 min-w-0 rounded-md border border-[color:var(--accent-solid)] bg-[color:var(--surface-0)] px-3 text-sm outline-none"
                          />
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              void submitRename();
                            }}
                            disabled={pendingRename || !renameValue.trim()}
                            className="inline-flex h-9 w-9 items-center justify-center rounded-md text-[color:var(--accent-solid)] hover:bg-[color:var(--surface-accent)] disabled:opacity-40"
                            title="Save (Enter)"
                          >
                            {pendingRename ? (
                              <Loader2 size={15} className="animate-spin" />
                            ) : (
                              <Check size={15} />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              cancelRename();
                            }}
                            disabled={pendingRename}
                            className="inline-flex h-9 w-9 items-center justify-center rounded-md text-[color:var(--text-muted)] hover:bg-[color:var(--surface-accent)] disabled:opacity-40"
                            title="Cancel (Esc)"
                          >
                            <X size={15} />
                          </button>
                        </div>
                      ) : (
                        <>
                          <div className="truncate text-sm font-medium text-[color:var(--text-primary)]">
                            {primaryLabel}
                          </div>
                          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-[color:var(--text-muted)]">
                            <span className="truncate font-mono">{instance.name}</span>
                            <span>·</span>
                            <span className="truncate font-mono">{instance.database_name}</span>
                          </div>
                        </>
                      )}
                    </div>

                    {!isRenaming && (
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              startRename(instance);
                            }}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[color:var(--text-muted)] hover:bg-[color:var(--surface-accent)] hover:text-[color:var(--text-primary)]"
                            title="Rename"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              askDelete(instance);
                            }}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[color:var(--text-muted)] hover:bg-red-500/10 hover:text-red-500"
                            title="Delete"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        <Panel className="overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-[color:var(--border-subtle)]">
            <h2 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] inline-flex items-center gap-2">
              <Activity size={12} />
              Recent activity
            </h2>
            <span className="text-xs text-[color:var(--text-muted)]">
              {auditLoading ? 'Loading…' : `${auditEvents.length} event${auditEvents.length === 1 ? '' : 's'}`}
            </span>
          </div>

          {auditLoading ? (
            <div className="flex items-center gap-2 px-5 py-8 text-sm text-[color:var(--text-muted)]">
              <Loader2 size={16} className="animate-spin" />
              Loading activity…
            </div>
          ) : auditEvents.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-[color:var(--text-muted)]">
              No manager-scoped events yet.
            </div>
          ) : (
            <ul className="divide-y divide-[color:var(--border-subtle)]">
              {auditEvents.map((event) => (
                <li
                  key={event.id}
                  className="grid grid-cols-[1fr_auto] items-center gap-3 px-5 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-[color:var(--text-primary)]">{event.action}</span>
                      {event.user_id && (
                        <span className="text-[color:var(--text-muted)]">·</span>
                      )}
                      {event.user_id && (
                        <span className="truncate text-[color:var(--text-secondary)]">{event.user_id}</span>
                      )}
                      {event.status_code !== null && event.status_code !== undefined && (
                        <>
                          <span className="text-[color:var(--text-muted)]">·</span>
                          <span
                            className={`font-mono text-[11px] ${
                              event.status_code >= 400
                                ? 'text-red-500'
                                : 'text-[color:var(--text-muted)]'
                            }`}
                          >
                            {event.status_code}
                          </span>
                        </>
                      )}
                    </div>
                    {event.ip_address && (
                      <div className="mt-0.5 truncate font-mono text-[11px] text-[color:var(--text-muted)]">
                        {event.ip_address}
                      </div>
                    )}
                  </div>
                  <div className="font-mono text-[11px] text-[color:var(--text-muted)] whitespace-nowrap">
                    {new Date(event.timestamp).toLocaleString()}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={pendingDelete ? undefined : cancelDelete}
          />
          <Panel className="relative w-full max-w-md bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-red-500/10 text-red-500">
                  <Trash2 size={18} />
                </div>
                <div className="flex flex-col">
                  <h2 className="font-bold text-sm uppercase tracking-widest">Delete instance</h2>
                  <span className="text-[9px] text-[color:var(--text-muted)] font-mono uppercase tracking-tighter">
                    Permanent action
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={cancelDelete}
                disabled={pendingDelete}
                className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]"
              >
                <X size={20} />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-sm text-[color:var(--text-secondary)] leading-relaxed">
                This drops the manager-registry row and the per-instance database for{' '}
                <span className="font-mono text-[color:var(--text-primary)]">{deleteTarget.name}</span>.
                All sessions, memory, and logs for this instance are lost.
              </p>
              <div className="space-y-2">
                <label
                  htmlFor="delete-confirm"
                  className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]"
                >
                  Type{' '}
                  <span className="font-mono normal-case tracking-normal text-[color:var(--text-primary)]">
                    {deleteTarget.name}
                  </span>{' '}
                  to confirm
                </label>
                <input
                  id="delete-confirm"
                  value={deleteConfirm}
                  onChange={(event) => setDeleteConfirm(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && deleteConfirm === deleteTarget.name && !pendingDelete) {
                      void confirmDelete();
                    } else if (event.key === 'Escape' && !pendingDelete) {
                      cancelDelete();
                    }
                  }}
                  disabled={pendingDelete}
                  autoFocus
                  className="h-10 w-full rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-sm outline-none focus:border-red-500"
                  placeholder={deleteTarget.name}
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
              <button
                type="button"
                onClick={cancelDelete}
                disabled={pendingDelete}
                className="h-9 px-4 rounded-md text-sm text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-0)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={pendingDelete || deleteConfirm !== deleteTarget.name}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-red-500 px-4 text-sm font-medium text-white hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {pendingDelete ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Trash2 size={14} />
                )}
                Delete
              </button>
            </div>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
