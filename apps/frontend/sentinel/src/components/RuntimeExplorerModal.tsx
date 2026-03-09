import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  ChevronRight,
  Clock3,
  FileCode2,
  Folder,
  GitBranch,
  Loader2,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import { buildRuntimeCommandRows } from '../lib/runtimeCommands';
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
import { Markdown } from './ui/Markdown';

type RuntimeInspectorTab = 'files' | 'commands';

type RuntimeExplorerModalProps = {
  open: boolean;
  session: Session | null;
  runtime: SessionRuntimeStatus | null;
  onClose: () => void;
};

type WorkbenchTab = {
  path: string;
  name: string;
  size_bytes: number;
  modified_at: string | null;
  content: string;
  truncated: boolean;
  max_bytes: number;
};

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '\u2014';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function inferCodeLanguageFromName(name: string): string {
  const normalized = name.trim().toLowerCase();
  if (!normalized) return 'text';
  if (normalized.endsWith('.ts') || normalized.endsWith('.tsx')) return 'typescript';
  if (normalized.endsWith('.js') || normalized.endsWith('.mjs') || normalized.endsWith('.cjs')) return 'javascript';
  if (normalized.endsWith('.py')) return 'python';
  if (normalized.endsWith('.rs')) return 'rust';
  if (normalized.endsWith('.go')) return 'go';
  if (normalized.endsWith('.java')) return 'java';
  if (normalized.endsWith('.kt')) return 'kotlin';
  if (normalized.endsWith('.rb')) return 'ruby';
  if (normalized.endsWith('.php')) return 'php';
  if (normalized.endsWith('.sh') || normalized.endsWith('.bash') || normalized.endsWith('.zsh')) return 'bash';
  if (normalized.endsWith('.css')) return 'css';
  if (normalized.endsWith('.scss')) return 'scss';
  if (normalized.endsWith('.html') || normalized.endsWith('.htm')) return 'html';
  if (normalized.endsWith('.json')) return 'json';
  if (normalized.endsWith('.md')) return 'markdown';
  if (normalized.endsWith('.yaml') || normalized.endsWith('.yml')) return 'yaml';
  if (normalized.endsWith('.toml')) return 'toml';
  if (normalized.endsWith('.sql')) return 'sql';
  if (normalized.endsWith('.xml')) return 'xml';
  if (normalized.endsWith('.diff') || normalized.endsWith('.patch')) return 'diff';
  return 'text';
}

function toMarkdownCodeFence(content: string, language: string): string {
  let fence = '```';
  while (content.includes(fence)) {
    fence += '`';
  }
  return `${fence}${language}\n${content}\n${fence}`;
}

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
  const [runtimeInspectorTab, setRuntimeInspectorTab] = useState<RuntimeInspectorTab>('files');
  const [runtimeStatus, setRuntimeStatus] = useState<SessionRuntimeStatus | null>(runtime);
  const [statusLoading, setStatusLoading] = useState(false);

  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);
  const [runtimePath, setRuntimePath] = useState('');
  const [runtimeChangedFiles, setRuntimeChangedFiles] = useState<SessionRuntimeGitChangedFilesResponse | null>(null);
  const [runtimeChangedFilesLoading, setRuntimeChangedFilesLoading] = useState(false);

  const [workbenchTabs, setWorkbenchTabs] = useState<WorkbenchTab[]>([]);
  const [activeWorkbenchPath, setActiveWorkbenchPath] = useState<string | null>(null);
  const [workbenchLoadingPath, setWorkbenchLoadingPath] = useState<string | null>(null);
  const [workbenchShowDiffByPath, setWorkbenchShowDiffByPath] = useState<Record<string, boolean>>({});
  const [workbenchDiffBaseRefByPath, setWorkbenchDiffBaseRefByPath] = useState<Record<string, string>>({});
  const [workbenchDiffByPath, setWorkbenchDiffByPath] = useState<Record<string, SessionRuntimeGitDiffResponse | null>>({});
  const [workbenchDiffErrorByPath, setWorkbenchDiffErrorByPath] = useState<Record<string, string | null>>({});
  const [workbenchDiffLoadingPath, setWorkbenchDiffLoadingPath] = useState<string | null>(null);
  const [workbenchGitRootsByPath, setWorkbenchGitRootsByPath] = useState<Record<string, SessionRuntimeGitRoot[]>>({});

  const [isCancellingCommand, setIsCancellingCommand] = useState(false);
  const [commandOutputCollapsedById, setCommandOutputCollapsedById] = useState<Record<string, boolean>>({});

  const sessionIdRef = useRef<string | null>(null);
  const sessionId = session?.id ?? null;

  useEffect(() => {
    if (!open) return;
    setRuntimeStatus(runtime);
    setRuntimeInspectorTab('files');
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

  async function fetchRuntimeChangedFilesForExplorer(
    targetSessionId: string,
    path: string,
    options?: { silent?: boolean },
  ): Promise<SessionRuntimeGitChangedFilesResponse | null> {
    const silent = Boolean(options?.silent);
    if (!silent) {
      setRuntimeChangedFilesLoading(true);
    }
    try {
      const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
        `/sessions/${targetSessionId}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (!isCurrentSession(targetSessionId)) return null;
      setRuntimeChangedFiles(payload);
      return payload;
    } catch {
      if (!isCurrentSession(targetSessionId)) return null;
      setRuntimeChangedFiles(null);
      return null;
    } finally {
      if (!silent && isCurrentSession(targetSessionId)) {
        setRuntimeChangedFilesLoading(false);
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
        void fetchRuntimeChangedFilesForExplorer(targetSessionId, payload.path || '', { silent });
      }
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeFiles(null);
      setRuntimePath(path);
      setRuntimeChangedFiles(null);
    } finally {
      if (!silent && isCurrentSession(targetSessionId)) {
        setRuntimeFilesLoading(false);
      }
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
    const changed = await fetchRuntimeChangedFilesForExplorer(sessionId, path);
    const firstPath = changed?.entries?.[0]?.path;
    if (!firstPath) return;
    await openRuntimeFileDiff(firstPath);
  }

  async function openRuntimeFile(path: string) {
    if (!sessionId) return;
    setWorkbenchLoadingPath(path);
    try {
      const payload = await api.get<SessionRuntimeFilePreviewResponse>(
        `/sessions/${sessionId}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
      );
      if (!isCurrentSession(sessionId)) return;
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
    } catch {
      toast.error('Failed to open runtime file');
    } finally {
      if (isCurrentSession(sessionId)) {
        setWorkbenchLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  async function openRuntimeFileDiff(path: string) {
    if (!sessionId) return;
    await openRuntimeFile(path);
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
    setRuntimeChangedFiles(null);
    setCommandOutputCollapsedById({});
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

  const commandActions = useMemo(() => {
    return buildRuntimeCommandRows(runtimeStatus, { newestFirst: true, limit: 50 });
  }, [runtimeStatus]);

  async function cancelRunningCommand() {
    if (!sessionId || isCancellingCommand) return;
    setIsCancellingCommand(true);
    try {
      await api.post(`/sessions/${sessionId}/stop`, {});
      toast.success('Stopping command');
      await fetchRuntimeStatus(sessionId);
    } catch {
      toast.error('Failed to stop command');
    } finally {
      setIsCancellingCommand(false);
    }
  }

  function toggleCommandOutput(rowId: string) {
    setCommandOutputCollapsedById((current) => ({
      ...current,
      [rowId]: !(current[rowId] ?? true),
    }));
  }

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
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 w-8 items-center justify-center rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--surface-1)]"
              aria-label="Close runtime explorer"
            >
              <X size={16} />
            </button>
          </header>

          <div className="flex min-h-0 flex-1">
            <aside className="w-[390px] shrink-0 border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
              <div className="flex h-full min-h-0 flex-col">
                <div className="border-b border-[color:var(--border-subtle)] p-3 space-y-2 shrink-0">
                  <div className="relative grid grid-cols-2 gap-0 rounded-full border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)] overflow-hidden">
                    {/* Sliding Indicator */}
                    <div 
                      className={`absolute top-0.5 bottom-0.5 w-[calc(50%-1px)] rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out ${
                        runtimeInspectorTab === 'files' 
                          ? 'left-0.5' 
                          : 'left-[calc(50%)]'
                      }`}
                    />

                    <button
                      type="button"
                      onClick={() => setRuntimeInspectorTab('files')}
                      className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                        runtimeInspectorTab === 'files'
                          ? 'text-[color:var(--text-primary)]'
                          : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                      }`}
                    >
                      Files
                    </button>
                    <button
                      type="button"
                      onClick={() => setRuntimeInspectorTab('commands')}
                      className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
                        runtimeInspectorTab === 'commands'
                          ? 'text-[color:var(--text-primary)]'
                          : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                      }`}
                    >
                      Commands
                    </button>
                  </div>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto p-4">
                  {runtimeInspectorTab === 'files' ? (
                    <div className="space-y-2">
                      <div className="mb-1 flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            if (!sessionId || !runtimeFiles || runtimeFiles.parent_path === null) return;
                            void openRuntimeDirectory(runtimeFiles.parent_path);
                          }}
                          disabled={!runtimeFiles || runtimeFiles.parent_path === null || runtimeFilesLoading}
                          className="inline-flex items-center gap-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-[color:var(--text-muted)] disabled:opacity-40"
                        >
                          <ArrowUp size={11} />
                          Up
                        </button>
                        <div className="min-w-0 flex-1 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-mono text-[color:var(--text-secondary)] truncate">
                          /workspace{runtimePath ? `/${runtimePath}` : ''}
                        </div>
                      </div>

                      <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Explorer</div>

                      {runtimeChangedFiles?.git_root ? (
                        <div className="rounded-lg border border-violet-500/30 bg-violet-500/10 p-2">
                          <div className="flex items-center justify-between gap-2">
                            <div className="min-w-0 flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-widest text-violet-700 dark:text-violet-200">
                              <GitBranch size={11} />
                              <span className="truncate">Repo {runtimeChangedFiles.git_root || '.'}</span>
                              <span className="text-violet-600/90 dark:text-violet-300/90">
                                {runtimeChangedFiles.detached_head ? 'detached' : runtimeChangedFiles.branch || 'unknown'}
                              </span>
                            </div>
                            <span className="text-[8px] font-bold uppercase tracking-widest text-violet-600 dark:text-violet-300/80">auto</span>
                          </div>
                          {runtimeChangedFiles.entries.length > 0 ? (
                            <div className="relative mt-2">
                              {runtimeChangedFilesLoading ? (
                                <div className="pointer-events-none absolute inset-x-0 -top-1 z-10 mx-auto w-fit rounded-full border border-violet-500/35 bg-violet-50 px-2 py-0.5 text-[8px] font-bold uppercase tracking-widest text-violet-700 dark:bg-violet-900/35 dark:text-violet-100">
                                  Updating…
                                </div>
                              ) : null}
                              <div className={`space-y-1 transition-opacity duration-150 ${runtimeChangedFilesLoading ? 'opacity-85' : 'opacity-100'}`}>
                                {runtimeChangedFiles.entries.slice(0, 8).map((entry) => (
                                  <button
                                    key={`runtime-inline-change:${entry.path}:${entry.status}`}
                                    type="button"
                                    onClick={() => void openRuntimeFileDiff(entry.path)}
                                    className="w-full rounded-md border border-violet-400/30 bg-violet-50/80 px-2 py-1.5 text-left transition-colors hover:border-violet-500/50 dark:bg-violet-950/30"
                                  >
                                    <div className="flex items-center gap-2 min-w-0">
                                      <span className="w-7 shrink-0 text-[9px] font-bold uppercase text-violet-700 dark:text-violet-200">{entry.status}</span>
                                      <span className="truncate text-[10px] font-mono text-violet-700/90 dark:text-violet-100/90">{entry.path}</span>
                                      <ChevronRight size={11} className="ml-auto shrink-0 text-violet-500 dark:text-violet-200/70" />
                                    </div>
                                  </button>
                                ))}
                                {runtimeChangedFiles.entries.length > 8 ? (
                                  <div className="text-[9px] uppercase tracking-wider text-violet-600 dark:text-violet-200/80">
                                    +{runtimeChangedFiles.entries.length - 8} more changed files
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          ) : runtimeChangedFilesLoading ? (
                            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-violet-600 dark:text-violet-200/80">
                              <Loader2 size={11} className="animate-spin" />
                              Scanning changes…
                            </div>
                          ) : (
                            <div className="mt-2 text-[10px] text-violet-600 dark:text-violet-100/80">
                              No changed files in this repository.
                            </div>
                          )}
                        </div>
                      ) : null}

                      {runtimeFiles?.entries?.length ? (
                        <div className="relative">
                          {runtimeFilesLoading ? (
                            <div className="pointer-events-none absolute inset-x-0 -top-2 z-10 mx-auto w-fit rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2 py-0.5 text-[8px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                              Updating folder…
                            </div>
                          ) : null}
                          <div className={`space-y-1.5 transition-all duration-150 ${runtimeFilesLoading ? 'opacity-80 blur-[0.2px]' : 'opacity-100'}`}>
                            {runtimeFiles.entries.map((entry: SessionRuntimeFileEntry) => (
                              <button
                                key={`${entry.path}:${entry.kind}`}
                                type="button"
                                onClick={() => {
                                  if (entry.kind === 'directory') {
                                    void openRuntimeDirectory(entry.path, {
                                      autoOpenFirstDiff: Boolean(entry.is_git_root),
                                    });
                                  } else {
                                    void openRuntimeFile(entry.path);
                                  }
                                }}
                                className="w-full rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2.5 py-2 text-left transition-colors hover:border-[color:var(--accent-solid)]/40"
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  {entry.kind === 'directory' ? (
                                    <Folder size={13} className="text-sky-500 shrink-0" />
                                  ) : (
                                    <FileCode2 size={13} className="text-[color:var(--text-muted)] shrink-0" />
                                  )}
                                  <span className="text-[11px] font-semibold truncate">{entry.name}</span>
                                  <span className="text-[9px] text-[color:var(--text-muted)] shrink-0">
                                    {entry.kind === 'directory' ? 'DIR' : formatBytes(entry.size_bytes)}
                                  </span>
                                  {entry.modified_at ? (
                                    <span className="text-[9px] text-[color:var(--text-muted)] shrink-0">{formatCompactDate(entry.modified_at)}</span>
                                  ) : null}
                                  {entry.kind === 'directory' && entry.is_git_root ? (
                                    <span className="inline-flex items-center gap-1 rounded-full border border-violet-500/35 bg-violet-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider text-violet-700 dark:text-violet-300">
                                      <GitBranch size={9} />
                                      {entry.git_detached_head ? 'detached' : entry.git_branch || 'repo'}
                                    </span>
                                  ) : null}
                                  <ChevronRight size={12} className="ml-auto text-[color:var(--text-muted)] shrink-0" />
                                </div>
                              </button>
                            ))}
                            {runtimeFiles.truncated ? (
                              <p className="text-[9px] uppercase tracking-wider text-amber-500">List truncated to 400 entries</p>
                            ) : null}
                          </div>
                        </div>
                      ) : runtimeFilesLoading ? (
                        <div className="flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                          <Loader2 size={12} className="animate-spin" />
                          Loading workspace…
                        </div>
                      ) : (
                        <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">Workspace is empty.</div>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Recent Commands</div>
                      {commandActions.length > 0 ? (
                        <div className="space-y-2">
                          {commandActions.map((entry) => {
                            const command = entry.command || '';
                            const isRunning = entry.state === 'running';
                            const output = entry.output;
                            const hasOutput = Boolean(
                              output &&
                                (output.stdout.trim().length > 0 ||
                                  output.stderr.trim().length > 0 ||
                                  output.timedOut ||
                                  output.returncode !== null ||
                                  output.ok !== null),
                            );
                            const isOutputCollapsed = commandOutputCollapsedById[entry.id] ?? true;
                            const statusTone =
                              entry.state === 'running'
                                ? 'border-[color:var(--border-subtle)] bg-emerald-500/[0.05]'
                                : entry.state === 'cancelled'
                                  ? 'border-[color:var(--border-subtle)] bg-rose-500/[0.04]'
                                  : entry.state === 'failed'
                                    ? 'border-[color:var(--border-subtle)] bg-rose-500/[0.04]'
                                    : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]/65';
                            const statusPillTone =
                              entry.state === 'running'
                                ? 'border-emerald-500/35 bg-emerald-500/[0.10] text-emerald-300'
                                : entry.state === 'cancelled'
                                  ? 'border-rose-500/40 bg-rose-500/[0.12] text-rose-300'
                                  : entry.state === 'failed'
                                    ? 'border-rose-500/35 bg-rose-500/[0.10] text-rose-300'
                                    : 'border-sky-500/35 bg-sky-500/[0.12] text-sky-300';
                            const accentTone =
                              entry.state === 'running'
                                ? 'bg-emerald-400/80'
                                : entry.state === 'cancelled'
                                  ? 'bg-rose-400/80'
                                  : entry.state === 'failed'
                                    ? 'bg-rose-400/80'
                                    : 'bg-[color:var(--border-subtle)]/90';
                            const sourceLabel = entry.source === 'detached_job' ? 'detached job' : 'command';
                            const displayTimestamp = entry.endedAt || entry.startedAt;
                            return (
                              <div key={entry.id} className={`relative overflow-hidden rounded-xl border px-3 py-2.5 ${statusTone}`}>
                                <div className={`absolute left-0 top-2 bottom-2 w-[2px] rounded-full ${accentTone}`} />
                                <div className="ml-2.5">
                                  <div className="flex items-center gap-2 text-[9px] uppercase tracking-widest text-[color:var(--text-muted)]">
                                    <Clock3 size={10} className={isRunning ? 'text-emerald-400' : 'opacity-70'} />
                                    <span className="font-semibold">{sourceLabel}</span>
                                    <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[8px] font-bold tracking-wider ${statusPillTone}`}>
                                      {entry.state}
                                    </span>
                                    {hasOutput ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleCommandOutput(entry.id)}
                                        className="inline-flex items-center rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/40 px-1.5 py-0.5 text-[8px] font-bold tracking-wider text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--surface-2)]/65"
                                      >
                                        {isOutputCollapsed ? 'show output' : 'hide output'}
                                      </button>
                                    ) : null}
                                    <span className="ml-auto font-semibold">{displayTimestamp ? formatCompactDate(displayTimestamp) : '\u2014'}</span>
                                  </div>
                                  <div className="mt-1.5">
                                    <Markdown
                                      content={toMarkdownCodeFence(command || '[empty command]', 'bash')}
                                      className="!text-[9px] markdown-workbench markdown-command-inline"
                                    />
                                  </div>
                                  {hasOutput && !isOutputCollapsed && output ? (
                                    <div className="mt-2 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/65 p-2">
                                      <div className="flex flex-wrap items-center gap-1 text-[8px] uppercase tracking-wider text-[color:var(--text-muted)]">
                                        {output.ok !== null ? (
                                          <span className="rounded-full border border-[color:var(--border-subtle)] px-1.5 py-0.5">ok: {String(output.ok)}</span>
                                        ) : null}
                                        {output.returncode !== null ? (
                                          <span className="rounded-full border border-[color:var(--border-subtle)] px-1.5 py-0.5">exit: {output.returncode}</span>
                                        ) : null}
                                        {output.timedOut ? (
                                          <span className="rounded-full border border-rose-500/40 bg-rose-500/12 px-1.5 py-0.5 text-rose-300">timed out</span>
                                        ) : null}
                                      </div>
                                      {output.stdout.trim() ? (
                                        <div className="mt-1.5">
                                          <div className="text-[8px] font-bold uppercase tracking-wider text-emerald-300/90">stdout</div>
                                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-[color:var(--text-secondary)]">{output.stdout}</pre>
                                        </div>
                                      ) : null}
                                      {output.stderr.trim() ? (
                                        <div className="mt-1.5">
                                          <div className="text-[8px] font-bold uppercase tracking-wider text-rose-300/90">stderr</div>
                                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-rose-200/95">{output.stderr}</pre>
                                        </div>
                                      ) : null}
                                    </div>
                                  ) : null}
                                  {isRunning ? (
                                    <div className="mt-1.5 flex justify-end">
                                      <button
                                        type="button"
                                        onClick={() => void cancelRunningCommand()}
                                        disabled={isCancellingCommand}
                                        className="inline-flex items-center rounded-md border border-rose-500/40 bg-rose-500/12 px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-rose-300 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                                      >
                                        {isCancellingCommand ? 'Cancelling…' : 'Cancel'}
                                      </button>
                                    </div>
                                  ) : null}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">
                          No runtime commands yet.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </aside>

            <section className="flex min-w-0 flex-1 flex-col bg-[color:var(--surface-1)]">
              <div className="border-b border-[color:var(--border-subtle)] p-3 space-y-2 shrink-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Open Files</div>
                  <button
                    type="button"
                    onClick={() => {
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
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-rose-400/50 bg-rose-500/20 text-rose-300 transition-colors hover:bg-rose-500/35 hover:text-rose-100"
                    title="Close all tabs"
                    aria-label="Close all tabs"
                  >
                    <X size={12} />
                  </button>
                </div>
                <div className="flex items-center gap-1 overflow-x-auto no-scrollbar">
                  {workbenchTabs.map((tab) => (
                    <button
                      key={tab.path}
                      type="button"
                      onClick={() => setActiveWorkbenchPath(tab.path)}
                      className={`group inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] font-semibold max-w-[240px] shrink-0 ${
                        activeWorkbenchTab?.path === tab.path
                          ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                          : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-secondary)]'
                      }`}
                      title={tab.path}
                    >
                      <span className="truncate">{tab.name}</span>
                      <span
                        role="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          closeWorkbenchTab(tab.path);
                        }}
                        className="inline-flex h-4 w-4 items-center justify-center rounded hover:bg-rose-500/20 hover:text-rose-300"
                        title="Close tab"
                      >
                        <X size={11} />
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {activeWorkbenchTab ? (
                <div className="flex-1 min-h-0 flex flex-col">
                  <div className="border-b border-[color:var(--border-subtle)] px-3 py-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[11px] font-semibold truncate">{activeWorkbenchTab.name}</div>
                        <div className="text-[9px] text-[color:var(--text-muted)] font-mono truncate" title={activeWorkbenchTab.path}>
                          {activeWorkbenchTab.path}
                        </div>
                      </div>
                      <div className="text-[9px] text-[color:var(--text-muted)]">{formatBytes(activeWorkbenchTab.size_bytes)}</div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: false }))}
                        className={`rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${
                          !workbenchShowDiffByPath[activeWorkbenchTab.path]
                            ? 'border-sky-500/40 bg-sky-500/15 text-sky-300'
                            : 'border-[color:var(--border-subtle)] text-[color:var(--text-muted)]'
                        }`}
                      >
                        Content
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setWorkbenchShowDiffByPath((current) => ({ ...current, [activeWorkbenchTab.path]: true }));
                          if (sessionId) {
                            void fetchRuntimeGitDiff(sessionId, activeWorkbenchTab.path);
                          }
                        }}
                        className={`rounded-md border px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${
                          workbenchShowDiffByPath[activeWorkbenchTab.path]
                            ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                            : 'border-[color:var(--border-subtle)] text-[color:var(--text-muted)]'
                        }`}
                      >
                        Diff
                      </button>
                      <div className="ml-auto flex items-center gap-1.5">
                        <select
                          value={activeWorkbenchBaseRef}
                          onChange={(event) => {
                            const selectedRef = event.target.value || 'HEAD';
                            setWorkbenchDiffBaseRefByPath((current) => ({
                              ...current,
                              [activeWorkbenchTab.path]: selectedRef,
                            }));
                            if (sessionId && workbenchShowDiffByPath[activeWorkbenchTab.path]) {
                              void fetchRuntimeGitDiff(sessionId, activeWorkbenchTab.path, {
                                baseRef: selectedRef,
                              });
                            }
                          }}
                          className="h-7 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 text-[10px] font-mono text-[color:var(--text-secondary)]"
                          title="Diff base reference"
                        >
                          {activeWorkbenchBaseRefOptions.map((ref) => (
                            <option key={`${activeWorkbenchTab.path}:base-ref:${ref}`} value={ref}>
                              {ref}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                    {activeWorkbenchGitRoots.length > 0 ? (
                      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar">
                        {activeWorkbenchGitRoots.slice(0, 6).map((root) => (
                          <span
                            key={`${activeWorkbenchTab.path}:${root.root_path || '.'}:${root.branch ?? 'detached'}`}
                            className="inline-flex items-center gap-1 rounded-full border border-violet-500/35 bg-violet-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-violet-700 dark:text-violet-300"
                          >
                            <span>{root.root_path || '.'}</span>
                            <span>{root.detached_head ? 'detached' : root.branch || 'unknown'}</span>
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  <div className="flex-1 min-h-0 overflow-auto p-3">
                    {workbenchShowDiffByPath[activeWorkbenchTab.path] ? (
                      workbenchDiffLoadingPath === activeWorkbenchTab.path ? (
                        <div className="flex items-center gap-2 text-[11px] text-[color:var(--text-muted)]">
                          <Loader2 size={13} className="animate-spin" />
                          Loading diff…
                        </div>
                      ) : activeWorkbenchDiff ? (
                        <div className="space-y-2">
                          <div className="flex items-center justify-between text-[10px] text-[color:var(--text-muted)]">
                            <span>root: {activeWorkbenchDiff.git_root || '.'}</span>
                            <span>{activeWorkbenchDiff.truncated ? 'truncated' : 'full'}</span>
                          </div>
                          <div className="rounded-lg border border-[color:var(--border-subtle)] p-2">
                            <Markdown
                              content={toMarkdownCodeFence(activeWorkbenchDiff.diff || '[no diff output]', 'diff')}
                              className="!text-[11px] markdown-workbench"
                            />
                          </div>
                        </div>
                      ) : activeWorkbenchDiffError ? (
                        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-2 text-[10px] text-rose-300">
                          {activeWorkbenchDiffError}
                        </div>
                      ) : (
                        <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">Open Diff to load the comparison automatically.</div>
                      )
                    ) : (
                      <div className="rounded-lg border border-[color:var(--border-subtle)] p-2">
                        <Markdown
                          content={toMarkdownCodeFence(
                            activeWorkbenchTab.content || '[empty file]',
                            inferCodeLanguageFromName(activeWorkbenchTab.name),
                          )}
                          className="!text-[11px] markdown-workbench"
                        />
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-[10px] text-[color:var(--text-muted)]">
                  Open a file from the workspace list to start.
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
