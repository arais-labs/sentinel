import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notificationPublisher } from '../lib/notifications';

import { api, downloadFile } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { useSessionWorkspace } from './useSessionWorkspace';
import { buildRuntimeGitChangedTree } from '../lib/runtimeGitTree';
import type { WorkbenchTab } from '../components/workbench/Workbench';
import type {
  SessionRuntimeFileEntry,
  SessionRuntimeFilePreviewResponse,
  SessionRuntimeFilesResponse,
  SessionRuntimeGitChangedFilesResponse,
  SessionRuntimeGitDiffResponse,
  SessionRuntimeGitRoot,
  SessionRuntimeGitRootsResponse,
} from '../types/api';

const notify = notificationPublisher('Files');

/** A repo-changes section as rendered by WorkbenchExplorerPane. */
export interface WorkbenchRepoChangeSection {
  id: string;
  title: string;
  tree: ReturnType<typeof buildRuntimeGitChangedTree>;
  loading: boolean;
}

function buildDiffBaseRefOptions(
  roots: SessionRuntimeGitRoot[],
  currentRef: string | null | undefined,
): string[] {
  const options = new Set<string>();
  options.add('HEAD');
  for (const root of roots) {
    for (const ref of root.refs ?? []) options.add(ref);
    if (root.branch) options.add(root.branch);
  }
  const normalizedCurrent = (currentRef ?? '').trim();
  if (normalizedCurrent) {
    options.add(normalizedCurrent);
  }
  return Array.from(options);
}

export interface UseSessionWorkbenchResult {
  // explorer (directory browse)
  runtimeFiles: SessionRuntimeFilesResponse | null;
  runtimePath: string;
  runtimeFilesLoading: boolean;
  runtimeFilesRefreshKey: number;
  fetchRuntimeFiles: (
    path?: string,
    options?: { refreshGit?: boolean; silent?: boolean },
  ) => Promise<void>;
  bumpRuntimeFilesRefreshKey: () => void;
  loadRuntimeDirectoryEntries: (path: string) => Promise<SessionRuntimeFileEntry[]>;
  downloadRuntimeEntry: (entry: SessionRuntimeFileEntry) => Promise<void>;

  // repo changes (git)
  repoChangeSections: WorkbenchRepoChangeSection[];
  expandedGitDirs: Record<string, boolean>;
  toggleGitDir: (path: string) => void;
  fetchChangedFilesForRepo: (path: string) => Promise<SessionRuntimeGitChangedFilesResponse | null>;
  forgetRepoRoot: (path: string) => void;

  // workbench tabs (open file → view + diff)
  workbenchTabs: WorkbenchTab[];
  activeWorkbenchPath: string | null;
  setActiveWorkbenchPath: React.Dispatch<React.SetStateAction<string | null>>;
  workbenchLoadingPath: string | null;
  activeWorkbenchTab: WorkbenchTab | null;
  openRuntimeFile: (path: string, options?: { suppressErrorToast?: boolean }) => Promise<boolean>;
  openRuntimeDirectory: (path: string, options?: { autoOpenFirstDiff?: boolean }) => Promise<void>;
  openRuntimeFileDiff: (path: string) => Promise<void>;
  closeWorkbenchTab: (path: string) => void;
  closeAllWorkbenchTabs: () => void;

  // diff view
  workbenchShowDiffByPath: Record<string, boolean>;
  setShowDiffForPath: (path: string, enabled: boolean) => void;
  activeWorkbenchDiff: SessionRuntimeGitDiffResponse | null;
  activeWorkbenchDiffError: string | null;
  activeWorkbenchDiffLoading: boolean;
  activeWorkbenchBaseRef: string;
  setDiffBaseRefForPath: (path: string, baseRef: string) => void;
  activeWorkbenchBaseRefOptions: string[];
  fetchRuntimeGitDiff: (path: string, options?: { baseRef?: string }) => Promise<void>;

  /** Clear ALL workbench/explorer/git state (e.g. when the session goes null). */
  resetWorkbench: () => void;
}

/**
 * Owns the per-session files / workbench surface:
 *   - directory browse + lazy directory load (`loadRuntimeDirectoryEntries`);
 *   - open file to view (`openRuntimeFile`) and git diff (`openRuntimeFileDiff`);
 *   - repo-changes sections + expanded git dirs;
 *   - file/folder download.
 *
 * Every request is guarded against a stale active session via `sessionIdRef`,
 * matching SessionsPage's behavior exactly. State resets when `sessionId`
 * changes so a new session never shows the previous one's tree/tabs.
 */
export function useSessionWorkbench(sessionId: string | null): UseSessionWorkbenchResult {
  const instanceName = useInstanceName();
  const { workspace, loading: workspaceLoading } = useSessionWorkspace(sessionId, instanceName ?? null);
  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimePath, setRuntimePath] = useState('');
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);
  const [runtimeFilesRefreshKey, setRuntimeFilesRefreshKey] = useState(0);

  const [repoChangesByRoot, setRepoChangesByRoot] = useState<
    Record<string, SessionRuntimeGitChangedFilesResponse | null>
  >({});
  const [repoChangesLoadingByRoot, setRepoChangesLoadingByRoot] = useState<Record<string, boolean>>({});
  const [expandedGitDirs, setExpandedGitDirs] = useState<Record<string, boolean>>({});

  const [workbenchTabs, setWorkbenchTabs] = useState<WorkbenchTab[]>([]);
  const [activeWorkbenchPath, setActiveWorkbenchPath] = useState<string | null>(null);
  const [workbenchLoadingPath, setWorkbenchLoadingPath] = useState<string | null>(null);
  const [workbenchShowDiffByPath, setWorkbenchShowDiffByPath] = useState<Record<string, boolean>>({});
  const [workbenchDiffBaseRefByPath, setWorkbenchDiffBaseRefByPath] = useState<Record<string, string>>({});
  const [workbenchDiffByPath, setWorkbenchDiffByPath] = useState<
    Record<string, SessionRuntimeGitDiffResponse | null>
  >({});
  const [workbenchDiffErrorByPath, setWorkbenchDiffErrorByPath] = useState<Record<string, string | null>>({});
  const [workbenchDiffLoadingPath, setWorkbenchDiffLoadingPath] = useState<string | null>(null);
  const [workbenchGitRootsByPath, setWorkbenchGitRootsByPath] = useState<
    Record<string, SessionRuntimeGitRoot[]>
  >({});

  // Stale-request guard. Each async fetch compares against the current session.
  const sessionIdRef = useRef<string | null>(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // Keep a live view of the repo-changes map for fetchRuntimeFiles' refresh loop
  // without making that callback depend on it (and re-subscribe every change).
  const repoChangesByRootRef = useRef(repoChangesByRoot);
  useEffect(() => {
    repoChangesByRootRef.current = repoChangesByRoot;
  }, [repoChangesByRoot]);

  const workspaceRevision = useRef(0);
  const resetWorkbench = useCallback(() => {
    workspaceRevision.current += 1;
    setRuntimeFiles(null);
    setRuntimePath('');
    setRepoChangesByRoot({});
    setRepoChangesLoadingByRoot({});
    setExpandedGitDirs({});
    setWorkbenchTabs([]);
    setActiveWorkbenchPath(null);
    setWorkbenchShowDiffByPath({});
    setWorkbenchDiffByPath({});
    setWorkbenchDiffErrorByPath({});
    setWorkbenchDiffBaseRefByPath({});
    setWorkbenchGitRootsByPath({});
    setWorkbenchLoadingPath(null);
    setWorkbenchDiffLoadingPath(null);
  }, []);

  const bumpRuntimeFilesRefreshKey = useCallback(() => {
    setRuntimeFilesRefreshKey((current) => current + 1);
  }, []);

  const fetchChangedFilesForRepo = useCallback(
    async (path: string): Promise<SessionRuntimeGitChangedFilesResponse | null> => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return null;
      setRepoChangesLoadingByRoot((current) => ({ ...current, [path]: true }));
      try {
        const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
          `/sessions/${sid}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
        );
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return null;
        setRepoChangesByRoot((current) => ({ ...current, [path]: payload }));
        return payload;
      } catch {
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return null;
        setRepoChangesByRoot((current) => ({ ...current, [path]: null }));
        return null;
      } finally {
        if ((sid === sessionIdRef.current && version === workspaceRevision.current)) {
          setRepoChangesLoadingByRoot((current) => ({ ...current, [path]: false }));
        }
      }
    },
    [],
  );

  const fetchRuntimeFiles = useCallback(
    async (path = '', options?: { refreshGit?: boolean; silent?: boolean }) => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid || workspaceLoading || !workspace) return;
      const silent = Boolean(options?.silent);
      if (!silent) {
        setRuntimeFilesLoading(true);
      }
      try {
        const query = new URLSearchParams();
        if (path.length > 0) query.set('path', path);
        query.set('limit', '400');
        const suffix = query.toString();
        const payload = await api.get<SessionRuntimeFilesResponse>(
          `/sessions/${sid}/runtime/files${suffix ? `?${suffix}` : ''}`,
        );
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return;
        setRuntimeFiles(payload);
        setRuntimePath(payload.path || '');
        // The caller decides whether to refresh git; SessionsPage gated this on
        // "files view visible" historically, but the hook is view-agnostic, so
        // refresh whenever explicitly requested.
        if (options?.refreshGit) {
          Object.keys(repoChangesByRootRef.current).forEach((rootPath) => {
            void fetchChangedFilesForRepo(rootPath);
          });
        }
      } catch (err) {
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return;
        // Directory no longer exists — walk up to the nearest valid parent.
        if ((err as { status?: number }).status === 404 && path && path !== '/') {
          const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) || '/' : '';
          void fetchRuntimeFiles(parent, options);
          return;
        }
        // Preserve the last successful explorer tree on transient failures.
      } finally {
        if ((sid === sessionIdRef.current && version === workspaceRevision.current) && !silent) {
          setRuntimeFilesLoading(false);
        }
      }
    },
    [fetchChangedFilesForRepo, workspace?.id, workspaceLoading],
  );

  const loadRuntimeDirectoryEntries = useCallback(
    async (path: string): Promise<SessionRuntimeFileEntry[]> => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return [];
      const query = new URLSearchParams();
      if (path.length > 0) query.set('path', path);
      query.set('limit', '400');
      const suffix = query.toString();
      const payload = await api.get<SessionRuntimeFilesResponse>(
        `/sessions/${sid}/runtime/files${suffix ? `?${suffix}` : ''}`,
      );
      if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return [];
      return Array.isArray(payload?.entries) ? payload.entries : [];
    },
    [],
  );

  const downloadRuntimeEntry = useCallback(async (entry: SessionRuntimeFileEntry) => {
    const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
    if (!sid) return;
    try {
      downloadFile(`/sessions/${sid}/runtime/download?path=${encodeURIComponent(entry.path)}`, entry.kind === 'directory' ? `${entry.name}.zip` : entry.name);
    } catch {
      notify.error(entry.kind === 'directory' ? 'Failed to download folder zip' : 'Failed to download file');
    }
  }, []);

  const fetchRuntimeGitRoots = useCallback(async (path: string) => {
    const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
    if (!sid) return;
    try {
      const payload = await api.get<SessionRuntimeGitRootsResponse>(
        `/sessions/${sid}/runtime/git/roots?path=${encodeURIComponent(path)}&limit=200`,
      );
      if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: payload.roots || [] }));
    } catch {
      if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return;
      setWorkbenchGitRootsByPath((current) => ({ ...current, [path]: [] }));
    }
  }, []);

  const fetchRuntimeGitDiff = useCallback(
    async (path: string, options?: { baseRef?: string }) => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return;
      const baseRefRaw = options?.baseRef ?? workbenchDiffBaseRefByPath[path];
      const baseRef =
        typeof baseRefRaw === 'string' && baseRefRaw.trim().length > 0 ? baseRefRaw.trim() : 'HEAD';
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
          `/sessions/${sid}/runtime/git/diff?${query.toString()}`,
        );
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return;
        setWorkbenchDiffByPath((current) => ({ ...current, [path]: payload }));
        setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
        if (!workbenchGitRootsByPath[path]?.length) {
          void fetchRuntimeGitRoots(path);
        }
      } catch (error) {
        const detail = error instanceof Error ? error.message : 'Failed to load git diff';
        setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: detail }));
      } finally {
        if ((sid === sessionIdRef.current && version === workspaceRevision.current)) {
          setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
        }
      }
    },
    [workbenchDiffBaseRefByPath, workbenchGitRootsByPath, fetchRuntimeGitRoots],
  );

  const openRuntimeFile = useCallback(
    async (path: string, options?: { suppressErrorToast?: boolean }): Promise<boolean> => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return false;
      setWorkbenchLoadingPath(path);
      try {
        const payload = await api.get<SessionRuntimeFilePreviewResponse>(
          `/sessions/${sid}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
        );
        if ((sid !== sessionIdRef.current || version !== workspaceRevision.current)) return false;
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
        void fetchRuntimeGitRoots(nextTab.path);
        return true;
      } catch {
        if (!options?.suppressErrorToast) {
          notify.error('Failed to open runtime file');
        }
        return false;
      } finally {
        if ((sid === sessionIdRef.current && version === workspaceRevision.current)) {
          setWorkbenchLoadingPath((current) => (current === path ? null : current));
        }
      }
    },
    [fetchRuntimeGitRoots],
  );

  const ensureWorkbenchTab = useCallback((path: string) => {
    const name = path.split('/').pop() || path;
    setWorkbenchTabs((current) => {
      if (current.some((tab) => tab.path === path)) return current;
      return [
        ...current,
        { path, name, size_bytes: 0, modified_at: null, content: '', truncated: false, max_bytes: 0 },
      ];
    });
    setActiveWorkbenchPath(path);
    setWorkbenchDiffBaseRefByPath((current) => (current[path] ? current : { ...current, [path]: 'HEAD' }));
    setWorkbenchDiffErrorByPath((current) => ({ ...current, [path]: null }));
  }, []);

  const openRuntimeFileDiff = useCallback(
    async (path: string) => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return;
      const opened = await openRuntimeFile(path, { suppressErrorToast: true });
      if (!opened) {
        ensureWorkbenchTab(path);
      }
      setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: true }));
      void fetchRuntimeGitDiff(path);
    },
    [openRuntimeFile, ensureWorkbenchTab, fetchRuntimeGitDiff],
  );

  const openRuntimeDirectory = useCallback(
    async (path: string, options?: { autoOpenFirstDiff?: boolean }) => {
      const sid = sessionIdRef.current;
      const version = workspaceRevision.current;
      if (!sid) return;
      const shouldAutoOpenFirstDiff = Boolean(options?.autoOpenFirstDiff);
      await fetchRuntimeFiles(path, { refreshGit: !shouldAutoOpenFirstDiff });
      if (!shouldAutoOpenFirstDiff) return;
      const changed = await fetchChangedFilesForRepo(path);
      const firstPath = changed?.entries?.[0]?.path;
      if (!firstPath) return;
      await openRuntimeFileDiff(firstPath);
    },
    [fetchRuntimeFiles, fetchChangedFilesForRepo, openRuntimeFileDiff],
  );

  const closeWorkbenchTab = useCallback((path: string) => {
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
    const dropKey = <T,>(record: Record<string, T>): Record<string, T> => {
      const next = { ...record };
      delete next[path];
      return next;
    };
    setWorkbenchShowDiffByPath(dropKey);
    setWorkbenchDiffByPath(dropKey);
    setWorkbenchDiffErrorByPath(dropKey);
    setWorkbenchDiffBaseRefByPath(dropKey);
    setWorkbenchGitRootsByPath(dropKey);
    setWorkbenchLoadingPath((current) => (current === path ? null : current));
    setWorkbenchDiffLoadingPath((current) => (current === path ? null : current));
  }, []);

  const closeAllWorkbenchTabs = useCallback(() => {
    setWorkbenchTabs([]);
    setActiveWorkbenchPath(null);
    setWorkbenchShowDiffByPath({});
    setWorkbenchDiffByPath({});
    setWorkbenchDiffErrorByPath({});
    setWorkbenchDiffBaseRefByPath({});
    setWorkbenchGitRootsByPath({});
    setWorkbenchLoadingPath(null);
    setWorkbenchDiffLoadingPath(null);
  }, []);

  const setShowDiffForPath = useCallback(
    (path: string, enabled: boolean) => {
      setWorkbenchShowDiffByPath((current) => ({ ...current, [path]: enabled }));
      if (enabled) {
        void fetchRuntimeGitDiff(path);
      }
    },
    [fetchRuntimeGitDiff],
  );

  const setDiffBaseRefForPath = useCallback(
    (path: string, baseRef: string) => {
      setWorkbenchDiffBaseRefByPath((current) => ({ ...current, [path]: baseRef }));
      setWorkbenchShowDiffByPath((current) => {
        if (current[path]) {
          void fetchRuntimeGitDiff(path, { baseRef });
        }
        return current;
      });
    },
    [fetchRuntimeGitDiff],
  );

  const toggleGitDir = useCallback((path: string) => {
    setExpandedGitDirs((current) => ({ ...current, [path]: !(current[path] ?? false) }));
  }, []);

  const forgetRepoRoot = useCallback((path: string) => {
    setRepoChangesByRoot((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setRepoChangesLoadingByRoot((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
    setExpandedGitDirs((current) => {
      const next = { ...current };
      Object.keys(next).forEach((key) => {
        if (key === path || key.startsWith(`${path}/`)) delete next[key];
      });
      return next;
    });
  }, []);

  // Reset all state when the session changes (incl. → null). Matches the
  // wholesale clear SessionsPage did in its session-change effect.
  useEffect(() => {
    resetWorkbench();
  }, [sessionId, resetWorkbench]);

  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent).detail?.sessionId === sessionIdRef.current) resetWorkbench();
    };
    window.addEventListener('sentinel:workspace-changed', changed);
    return () => window.removeEventListener('sentinel:workspace-changed', changed);
  }, [resetWorkbench]);

  const activeWorkbenchTab = useMemo(() => {
    if (!workbenchTabs.length) return null;
    if (!activeWorkbenchPath) return workbenchTabs[0];
    return workbenchTabs.find((tab) => tab.path === activeWorkbenchPath) ?? workbenchTabs[0];
  }, [workbenchTabs, activeWorkbenchPath]);

  const repoChangeSections = useMemo<WorkbenchRepoChangeSection[]>(
    () =>
      Object.entries(repoChangesByRoot).map(([rootPath, payload]) => ({
        id: rootPath,
        title:
          (payload?.git_root || rootPath)
            .split('/')
            .filter(Boolean)
            .pop() || rootPath || 'repo',
        tree: buildRuntimeGitChangedTree(payload),
        loading: Boolean(repoChangesLoadingByRoot[rootPath]),
      })),
    [repoChangesByRoot, repoChangesLoadingByRoot],
  );

  const activeWorkbenchDiff = activeWorkbenchTab ? workbenchDiffByPath[activeWorkbenchTab.path] ?? null : null;
  const activeWorkbenchDiffError = activeWorkbenchTab
    ? workbenchDiffErrorByPath[activeWorkbenchTab.path] ?? null
    : null;
  const activeWorkbenchDiffLoading = workbenchDiffLoadingPath === activeWorkbenchTab?.path;
  const activeWorkbenchBaseRef = activeWorkbenchTab
    ? workbenchDiffBaseRefByPath[activeWorkbenchTab.path] ?? 'HEAD'
    : 'HEAD';
  const activeWorkbenchBaseRefOptions = useMemo(
    () =>
      buildDiffBaseRefOptions(
        activeWorkbenchTab ? workbenchGitRootsByPath[activeWorkbenchTab.path] ?? [] : [],
        activeWorkbenchBaseRef,
      ),
    [activeWorkbenchTab, workbenchGitRootsByPath, activeWorkbenchBaseRef],
  );

  return {
    runtimeFiles,
    runtimePath,
    runtimeFilesLoading,
    runtimeFilesRefreshKey,
    fetchRuntimeFiles,
    bumpRuntimeFilesRefreshKey,
    loadRuntimeDirectoryEntries,
    downloadRuntimeEntry,

    repoChangeSections,
    expandedGitDirs,
    toggleGitDir,
    fetchChangedFilesForRepo,
    forgetRepoRoot,

    workbenchTabs,
    activeWorkbenchPath,
    setActiveWorkbenchPath,
    workbenchLoadingPath,
    activeWorkbenchTab,
    openRuntimeFile,
    openRuntimeDirectory,
    openRuntimeFileDiff,
    closeWorkbenchTab,
    closeAllWorkbenchTabs,

    workbenchShowDiffByPath,
    setShowDiffForPath,
    activeWorkbenchDiff,
    activeWorkbenchDiffError,
    activeWorkbenchDiffLoading,
    activeWorkbenchBaseRef,
    setDiffBaseRefForPath,
    activeWorkbenchBaseRefOptions,
    fetchRuntimeGitDiff,

    resetWorkbench,
  };
}
