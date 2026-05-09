import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  Loader2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import { api } from '../lib/api';
import { buildRuntimeGitChangedTree } from '../lib/runtimeGitTree';
import type {
  Session,
  SessionRuntimeFileEntry,
  SessionRuntimeFilePreviewResponse,
  SessionRuntimeFilesResponse,
  SessionRuntimeGitChangedFilesResponse,
  SessionRuntimeGitDiffResponse,
  SessionRuntimeGitRoot,
  SessionRuntimeGitRootsResponse,
  SessionRuntimeStatus,
} from '../types/api';
import { Workbench, type WorkbenchTab } from './workbench/Workbench';

type RuntimeExplorerModalProps = {
  open: boolean;
  session: Session | null;
  runtime: SessionRuntimeStatus | null;
  onClose: () => void;
};

function buildRuntimeDiffBaseRefOptions(
  roots: SessionRuntimeGitRoot[],
  currentRef: string | null | undefined,
): string[] {
  const options = new Set<string>();
  options.add('HEAD');
  for (const root of roots) {
    if (!root.detached_head && root.branch) {
      options.add(root.branch);
      options.add(`origin/${root.branch}`);
    }
  }
  options.add('origin/main');
  options.add('origin/master');
  const normalizedCurrent = (currentRef ?? '').trim();
  if (normalizedCurrent) {
    options.add(normalizedCurrent);
  }
  return Array.from(options);
}

function runtimeStatusLabel(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'Unavailable';
  if (!runtime.runtime_exists) return 'Missing';
  return runtime.active ? 'Active' : 'Idle';
}

function runtimeBadgeTone(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-muted)]';
  if (!runtime.runtime_exists) return 'border-rose-500/30 bg-rose-500/10 text-rose-400';
  if (runtime.active) return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400';
  return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
}

export function RuntimeExplorerModal({ open, session, runtime, onClose }: RuntimeExplorerModalProps) {
  const [runtimeStatus, setRuntimeStatus] = useState<SessionRuntimeStatus | null>(runtime);
  const [statusLoading, setStatusLoading] = useState(false);

  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);
  const [runtimePath, setRuntimePath] = useState('');
  const [runtimeRepoChangesByRoot, setRuntimeRepoChangesByRoot] = useState<Record<string, SessionRuntimeGitChangedFilesResponse | null>>({});
  const [runtimeRepoChangesLoadingByRoot, setRuntimeRepoChangesLoadingByRoot] = useState<Record<string, boolean>>({});
  const [runtimeExpandedGitDirs, setRuntimeExpandedGitDirs] = useState<Record<string, boolean>>({});
  const [workbenchTabs, setWorkbenchTabs] = useState<WorkbenchTab[]>([]);

  const [activeWorkbenchPath, setActiveWorkbenchPath] = useState<string | null>(null);
  const [workbenchLoadingPath, setWorkbenchLoadingPath] = useState<string | null>(null);
  const [workbenchShowDiffByPath, setWorkbenchShowDiffByPath] = useState<Record<string, boolean>>({});
  const [workbenchDiffBaseRefByPath, setWorkbenchDiffBaseRefByPath] = useState<Record<string, string>>({});
  const [workbenchDiffByPath, setWorkbenchDiffByPath] = useState<Record<string, SessionRuntimeGitDiffResponse | null>>({});
  const [workbenchDiffErrorByPath, setWorkbenchDiffErrorByPath] = useState<Record<string, string | null>>({});
  const [workbenchDiffLoadingPath, setWorkbenchDiffLoadingPath] = useState<string | null>(null);
  const [workbenchGitRootsByPath, setWorkbenchGitRootsByPath] = useState<Record<string, SessionRuntimeGitRoot[]>>({});

  const sessionIdRef = useRef<string | null>(null);
  const sessionId = session?.id ?? null;
  const runtimeRepoChangeSections = useMemo(
    () =>
      Object.entries(runtimeRepoChangesByRoot).map(([rootPath, payload]) => ({
        id: rootPath,
        title:
          (payload?.git_root || rootPath)
            .split('/')
            .filter(Boolean)
            .pop() || rootPath || 'repo',
        tree: buildRuntimeGitChangedTree(payload),
        loading: Boolean(runtimeRepoChangesLoadingByRoot[rootPath]),
      })),
    [runtimeRepoChangesByRoot, runtimeRepoChangesLoadingByRoot],
  );

  useEffect(() => {
    if (!open) return;
    setRuntimeStatus(runtime);
  }, [open, runtime]);

  useEffect(() => {
    sessionIdRef.current = open ? sessionId : null;
  }, [open, sessionId]);

  function isCurrentSession(targetSessionId: string): boolean {
    return Boolean(open && sessionIdRef.current === targetSessionId);
  }

  async function fetchRuntimeStatus(targetSessionId: string, actionLimit = 80) {
    setStatusLoading(true);
    try {
      const payload = await api.get<SessionRuntimeStatus>(`/sessions/${targetSessionId}/runtime?action_limit=${actionLimit}`);
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeStatus(payload);
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeStatus(null);
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setStatusLoading(false);
      }
    }
  }

  async function fetchRuntimeChangedFilesForRepo(
    targetSessionId: string,
    path: string,
  ): Promise<SessionRuntimeGitChangedFilesResponse | null> {
    setRuntimeRepoChangesLoadingByRoot((current) => ({ ...current, [path]: true }));
    try {
      const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
        `/sessions/${targetSessionId}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (!isCurrentSession(targetSessionId)) return null;
      setRuntimeRepoChangesByRoot((current) => ({ ...current, [path]: payload }));
      return payload;
    } catch {
      if (!isCurrentSession(targetSessionId)) return null;
      setRuntimeRepoChangesByRoot((current) => ({ ...current, [path]: null }));
      return null;
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setRuntimeRepoChangesLoadingByRoot((current) => ({ ...current, [path]: false }));
      }
    }
  }

  async function fetchRuntimeFiles(
    targetSessionId: string,
    path = '',
    options?: { refreshGit?: boolean; silent?: boolean },
  ) {
    const silent = Boolean(options?.silent);
    if (!silent) {
      setRuntimeFilesLoading(true);
    }
    try {
      const query = new URLSearchParams();
      if (path.trim().length > 0) query.set('path', path.trim());
      query.set('limit', '400');
      const suffix = query.toString();
      const payload = await api.get<SessionRuntimeFilesResponse>(
        `/sessions/${targetSessionId}/runtime/files${suffix ? `?${suffix}` : ''}`,
      );
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeFiles(payload);
      setRuntimePath(payload.path || '');
      if (options?.refreshGit ?? true) {
        Object.keys(runtimeRepoChangesByRoot).forEach((rootPath) => {
          void fetchRuntimeChangedFilesForRepo(targetSessionId, rootPath);
        });
      }
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      // Preserve the last successful explorer tree on transient refresh failures.
    } finally {
      if (!silent && isCurrentSession(targetSessionId)) {
        setRuntimeFilesLoading(false);
      }
    }
  }

  async function loadRuntimeDirectoryEntries(path: string): Promise<SessionRuntimeFileEntry[]> {
    if (!sessionId) return [];
    const query = new URLSearchParams();
    if (path.trim().length > 0) query.set('path', path.trim());
    query.set('limit', '400');
    const suffix = query.toString();
    const payload = await api.get<SessionRuntimeFilesResponse>(
      `/sessions/${sessionId}/runtime/files${suffix ? `?${suffix}` : ''}`,
    );
    if (!isCurrentSession(sessionId)) return [];
    return Array.isArray(payload?.entries) ? payload.entries : [];
  }

  async function downloadRuntimeEntry(entry: SessionRuntimeFileEntry) {
    if (!sessionId) return;
    try {
      const { blob, filename } = await api.download(
        `/sessions/${sessionId}/runtime/download?path=${encodeURIComponent(entry.path)}`,
        { timeoutMs: 120_000 },
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename || (entry.kind === 'directory' ? `${entry.name}.zip` : entry.name);
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } catch {
      toast.error(entry.kind === 'directory' ? 'Failed to download folder zip' : 'Failed to download file');
    }
  }

  async function fetchRuntimeGitRoots(targetSessionId: string, path: string) {
    try {
      const payload = await api.get<SessionRuntimeGitRootsResponse>(
        `/sessions/${targetSessionId}/runtime/git/roots?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (!isCurrentSession(targetSessionId)) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: payload.roots || [] }));
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: [] }));
    }
  }

  async function fetchRuntimeGitDiff(
    targetSessionId: string,
    path: string,
    options?: { baseRef?: string },
  ) {
    const baseRefRaw = options?.baseRef ?? workbenchDiffBaseRefByPath[path];
    const baseRef = (typeof baseRefRaw === 'string' && baseRefRaw.trim().length > 0) ? baseRefRaw.trim() : 'HEAD';
    setWorkbenchDiffLoadingPath(path);
    setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: null }));
    try {
      const query = new URLSearchParams();
      query.set('path', path);
      query.set('base_ref', baseRef);
      query.set('staged', 'false');
      query.set('context_lines', '3');
      query.set('max_bytes', '120000');
      const payload = await api.get<SessionRuntimeGitDiffResponse>(
        `/sessions/${targetSessionId}/runtime/git/diff?${query.toString()}`,
      );
      if (!isCurrentSession(targetSessionId)) return;
      setWorkbenchDiffByPath((current) => ({ ...current, [path]: payload }));
      setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
      if (!workbenchGitRootsByPath[path]?.length) {
        void fetchRuntimeGitRoots(targetSessionId, path);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Failed to load git diff';
      if (!isCurrentSession(targetSessionId)) return;
      setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: detail }));
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  async function openRuntimeDirectory(
    path: string,
    options?: { autoOpenFirstDiff?: boolean },
  ) {
    if (!sessionId) return;
    const shouldAutoOpenFirstDiff = Boolean(options?.autoOpenFirstDiff);
    await fetchRuntimeFiles(sessionId, path, {
      refreshGit: !shouldAutoOpenFirstDiff,
    });
    if (!shouldAutoOpenFirstDiff) return;
    const changed = await fetchRuntimeChangedFilesForRepo(sessionId, path);
    const firstPath = changed?.entries?.[0]?.path;
    if (!firstPath) return;
    await openRuntimeFileDiff(firstPath);
  }

  async function openRuntimeFile(
    path: string,
    options?: { suppressErrorToast?: boolean },
  ): Promise<boolean> {
    if (!sessionId) return false;
    setWorkbenchLoadingPath(path);
    try {
      const payload = await api.get<SessionRuntimeFilePreviewResponse>(
        `/sessions/${sessionId}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
      );
      if (!isCurrentSession(sessionId)) return false;
      const nextTab: WorkbenchTab = {
        path: payload.path,
        name: payload.name,
        size_bytes: payload.size_bytes,
        modified_at: payload.modified_at,
        content: payload.content,
        truncated: payload.truncated,
        max_bytes: payload.max_bytes,
      };
      setWorkbenchTabs((current) => {
        const existing = current.find((tab) => tab.path === nextTab.path);
        if (existing) {
          return current.map((tab) => (tab.path === nextTab.path ? nextTab : tab));
        }
        return [...current, nextTab];
      });
      setActiveWorkbenchPath(nextTab.path);
      setWorkbenchDiffBaseRefByPath((current) =>
        current[nextTab.path] ? current : { ...current, [nextTab.path]: 'HEAD' },
      );
      setWorkbenchDiffErrorByPath((current) => ({ ...current, [nextTab.path]: null }));
      setWorkbenchShowDiffByPath((current) =>
        Object.prototype.hasOwnProperty.call(current, nextTab.path)
          ? current
          : { ...current, [nextTab.path]: false },
      );
      void fetchRuntimeGitRoots(sessionId, nextTab.path);
      return true;
    } catch {
      if (!options?.suppressErrorToast) {
        toast.error('Failed to open runtime file');
      }
      return false;
    } finally {
      if (isCurrentSession(sessionId)) {
        setWorkbenchLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  function ensureWorkbenchTab(path: string) {
    const name = path.split('/').pop() || path;
    setWorkbenchTabs((current) => {
      if (current.some((tab) => tab.path === path)) return current;
      return [
        ...current,
        {
          path,
          name,
          size_bytes: 0,
          modified_at: null,
          content: '',
          truncated: false,
          max_bytes: 0,
        },
      ];
    });
    setActiveWorkbenchPath(path);
    setWorkbenchDiffBaseRefByPath((current) =>
      current[path] ? current : { ...current, [path]: 'HEAD' },
    );
    setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: null }));
  }

  async function openRuntimeFileDiff(path: string) {
    if (!sessionId) return;
    const opened = await openRuntimeFile(path, { suppressErrorToast: true });
    if (!opened) {
      ensureWorkbenchTab(path);
    }
    setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
    void fetchRuntimeGitDiff(sessionId, path);
  }

  function closeWorkbenchTab(path: string) {
    setWorkbenchTabs((current) => {
      const targetIndex = current.findIndex((tab) => tab.path === path);
      const next = current.filter((tab) => tab.path !== path);
      setActiveWorkbenchPath((previous) => {
        if (previous !== path) return previous;
        if (!next.length) return null;
        const fallbackIndex = Math.min(Math.max(targetIndex - 1, 0), next.length - 1);
        return next[fallbackIndex]?.path ?? next[next.length - 1].path;
      });
      return next;
    });
    setWorkbenchShowDiffByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffErrorByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchDiffBaseRefByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchGitRootsByPath((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setWorkbenchLoadingPath((current) => (current === path ? null : current));
    setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
  }

  async function refreshAll(targetSessionId: string, path = runtimePath) {
    await Promise.all([
      fetchRuntimeStatus(targetSessionId),
      fetchRuntimeFiles(targetSessionId, path),
    ]);
  }
useEffect(() => {
  if (!open || !sessionId) return;
  setRuntimePath('');
  setWorkbenchTabs([]);
  setActiveWorkbenchPath(null);
  setWorkbenchLoadingPath(null);
  setWorkbenchShowDiffByPath({});
  setWorkbenchDiffBaseRefByPath({});
  setWorkbenchDiffByPath({});
  setWorkbenchDiffErrorByPath({});
  setWorkbenchDiffLoadingPath(null);
  setWorkbenchGitRootsByPath({});
  setRuntimeRepoChangesByRoot({});
  setRuntimeRepoChangesLoadingByRoot({});
  setRuntimeExpandedGitDirs({});
  void refreshAll(sessionId, '');
}, [open, sessionId]);


  const activeWorkbenchTab = useMemo(() => {
    if (!workbenchTabs.length) return null;
    if (!activeWorkbenchPath) return workbenchTabs[0];
    return workbenchTabs.find((tab) => tab.path === activeWorkbenchPath) ?? workbenchTabs[0];
  }, [workbenchTabs, activeWorkbenchPath]);

  const activeWorkbenchDiff = activeWorkbenchTab ? workbenchDiffByPath[activeWorkbenchTab.path] ?? null : null;
  const activeWorkbenchDiffError = activeWorkbenchTab ? workbenchDiffErrorByPath[activeWorkbenchTab.path] ?? null : null;
  const activeWorkbenchGitRoots = activeWorkbenchTab ? workbenchGitRootsByPath[activeWorkbenchTab.path] ?? [] : [];
  const activeWorkbenchBaseRef = activeWorkbenchTab
    ? workbenchDiffBaseRefByPath[activeWorkbenchTab.path] ?? 'HEAD'
    : 'HEAD';
  const activeWorkbenchBaseRefOptions = useMemo(
    () => (activeWorkbenchTab ? buildRuntimeDiffBaseRefOptions(activeWorkbenchGitRoots, activeWorkbenchBaseRef) : ['HEAD']),
    [activeWorkbenchTab, activeWorkbenchGitRoots, activeWorkbenchBaseRef],
  );

  useEffect(() => {
    if (!open || !sessionId) return;
    const timer = window.setInterval(() => {
      void fetchRuntimeStatus(sessionId);
      void fetchRuntimeFiles(sessionId, runtimePath, { silent: true, refreshGit: true });
      if (activeWorkbenchTab && workbenchShowDiffByPath[activeWorkbenchTab.path]) {
        void fetchRuntimeGitDiff(sessionId, activeWorkbenchTab.path);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [open, sessionId, runtimePath, activeWorkbenchTab, workbenchShowDiffByPath]);

  if (!open || !session) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 h-[90vh] w-full max-w-[1400px] overflow-hidden rounded-xl border border-[color:var(--border-strong)] bg-[color:var(--surface-1)] shadow-2xl animate-in zoom-in-95 duration-200">
        <div className="flex h-full min-h-0 flex-col">
          <header className="flex items-center justify-between border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-5 py-3 shrink-0">
            <div className="min-w-0">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Runtime Explorer</p>
              <div className="flex items-center gap-2 min-w-0">
                <p className="truncate text-xs font-mono font-medium text-[color:var(--text-primary)]">
                  {session.title || `session_${session.id.slice(0, 8)}`}
                </p>
                <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest ${runtimeBadgeTone(runtimeStatus)}`}>
                  {runtimeStatusLabel(runtimeStatus)}
                </span>
                {statusLoading ? <Loader2 size={12} className="animate-spin text-[color:var(--text-muted)]" /> : null}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-8 w-8 items-center justify-center rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--surface-1)]"
                aria-label="Close runtime explorer"
              >
                <X size={16} />
              </button>
            </div>
          </header>

          <div className="flex min-h-0 flex-1">
            <Workbench
              tabs={workbenchTabs}
              activeTabPath={activeWorkbenchPath}
              onTabClick={(path) => setActiveWorkbenchPath(path)}
              onTabClose={closeWorkbenchTab}
              onCloseAll={() => {
                setWorkbenchTabs([]);
                setActiveWorkbenchPath(null);
                setWorkbenchShowDiffByPath({});
                setWorkbenchDiffByPath({});
                setWorkbenchDiffErrorByPath({});
                setWorkbenchDiffBaseRefByPath({});
                setWorkbenchGitRootsByPath({});
                setWorkbenchLoadingPath(null);
                setWorkbenchDiffLoadingPath(null);
              }}
              explorerEntries={runtimeFiles?.entries || []}
              currentExplorerPath={runtimePath}
              explorerLoading={runtimeFilesLoading}
              onExplorerFileClick={(entry) => void openRuntimeFile(entry.path)}
              onExplorerDownload={(entry) => void downloadRuntimeEntry(entry)}
              loadExplorerDirectory={loadRuntimeDirectoryEntries}
              onExplorerDirectoryToggle={(entry, expanded) => {
                if (!sessionId || !entry.is_git_root) return;
                if (!expanded) {
                  setRuntimeRepoChangesByRoot((current) => {
                    const next = { ...current };
                    delete next[entry.path];
                    return next;
                  });
                  setRuntimeRepoChangesLoadingByRoot((current) => {
                    const next = { ...current };
                    delete next[entry.path];
                    return next;
                  });
                  setRuntimeExpandedGitDirs((current) => {
                    const next = { ...current };
                    Object.keys(next).forEach((key) => {
                      if (key === entry.path || key.startsWith(`${entry.path}/`)) delete next[key];
                    });
                    return next;
                  });
                  return;
                }
                void fetchRuntimeChangedFilesForRepo(sessionId, entry.path);
              }}
              repoChangesSections={runtimeRepoChangeSections}
              expandedGitDirs={runtimeExpandedGitDirs}
              onToggleGitDir={(path) => {
                setRuntimeExpandedGitDirs((current) => ({ ...current, [path]: !(current[path] ?? false) }));
              }}
              onGitFileClick={(path) => void openRuntimeFileDiff(path)}
              diffMode={activeWorkbenchTab ? workbenchShowDiffByPath[activeWorkbenchTab.path] ?? false : false}
              setDiffMode={(enabled) => {
                if (!activeWorkbenchTab) return;
                setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: enabled }));
                if (enabled && sessionId) {
                  void fetchRuntimeGitDiff(sessionId, activeWorkbenchTab.path);
                }
              }}
              diffContent={activeWorkbenchDiff}
              diffLoading={workbenchDiffLoadingPath === activeWorkbenchTab?.path}
              diffError={activeWorkbenchDiffError}
              diffBaseRef={activeWorkbenchBaseRef}
              onDiffBaseRefChange={(ref) => {
                if (!activeWorkbenchTab) return;
                setWorkbenchDiffBaseRefByPath((current) => ({
                  ...current,
                  [activeWorkbenchTab.path]: ref,
                }));
                if (sessionId && workbenchShowDiffByPath[activeWorkbenchTab.path]) {
                  void fetchRuntimeGitDiff(sessionId, activeWorkbenchTab.path, {
                    baseRef: ref,
                  });
                }
              }}
              diffBaseRefOptions={activeWorkbenchBaseRefOptions}
              width={1400}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
