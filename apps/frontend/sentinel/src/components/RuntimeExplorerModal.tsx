import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  Clock3,
  FileCode2,
  Folder,
  GitBranch,
  Loader2,
  Terminal,
  X,
} from 'lucide-react';

import { api } from '../lib/api';
import { formatCompactDate } from '../lib/format';
import type {
  Session,
  SessionRuntimeAction,
  SessionRuntimeFileEntry,
  SessionRuntimeFilePreviewResponse,
  SessionRuntimeFilesResponse,
  SessionRuntimeGitChangedFilesResponse,
  SessionRuntimeGitDiffResponse,
  SessionRuntimeStatus,
} from '../types/api';
import { Markdown } from './ui/Markdown';

type RuntimeExplorerTab = 'overview' | 'files' | 'git' | 'commands';

type RuntimeExplorerModalProps = {
  open: boolean;
  session: Session | null;
  runtime: SessionRuntimeStatus | null;
  onClose: () => void;
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

function runtimeStatusLabel(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'Unavailable';
  if (!runtime.runtime_exists) return 'Missing';
  return runtime.active ? 'Active' : 'Idle';
}

function runtimeActionCommand(entry: SessionRuntimeAction): string | null {
  if (!entry || !entry.details || typeof entry.details !== 'object') return null;
  const command = entry.details.command;
  return typeof command === 'string' && command.trim().length > 0 ? command.trim() : null;
}

function runtimeBadgeTone(runtime: SessionRuntimeStatus | null): string {
  if (!runtime) return 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] text-[color:var(--text-muted)]';
  if (!runtime.runtime_exists) return 'border-rose-500/30 bg-rose-500/10 text-rose-400';
  if (runtime.active) return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400';
  return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
}

export function RuntimeExplorerModal({ open, session, runtime, onClose }: RuntimeExplorerModalProps) {
  const [tab, setTab] = useState<RuntimeExplorerTab>('overview');
  const [runtimeStatus, setRuntimeStatus] = useState<SessionRuntimeStatus | null>(runtime);
  const [statusLoading, setStatusLoading] = useState(false);

  const [runtimeFiles, setRuntimeFiles] = useState<SessionRuntimeFilesResponse | null>(null);
  const [runtimeFilesLoading, setRuntimeFilesLoading] = useState(false);
  const [runtimePath, setRuntimePath] = useState('');

  const [runtimeFilePreview, setRuntimeFilePreview] = useState<SessionRuntimeFilePreviewResponse | null>(null);
  const [runtimeFilePreviewLoadingPath, setRuntimeFilePreviewLoadingPath] = useState<string | null>(null);

  const [runtimeChangedFiles, setRuntimeChangedFiles] = useState<SessionRuntimeGitChangedFilesResponse | null>(null);
  const [runtimeChangedFilesLoading, setRuntimeChangedFilesLoading] = useState(false);
  const [selectedDiffPath, setSelectedDiffPath] = useState<string | null>(null);
  const [diffBaseRef, setDiffBaseRef] = useState('HEAD');
  const [diffStaged, setDiffStaged] = useState(false);
  const [runtimeDiff, setRuntimeDiff] = useState<SessionRuntimeGitDiffResponse | null>(null);
  const [runtimeDiffLoading, setRuntimeDiffLoading] = useState(false);
  const [runtimeDiffError, setRuntimeDiffError] = useState<string | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  const sessionId = session?.id ?? null;

  useEffect(() => {
    if (!open) return;
    setTab('overview');
  }, [open]);

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

  async function fetchRuntimeStatus(targetSessionId: string, actionLimit = 120) {
    setStatusLoading(true);
    try {
      const payload = await api.get<SessionRuntimeStatus>(
        `/sessions/${targetSessionId}/runtime?action_limit=${actionLimit}`,
      );
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

  async function fetchRuntimeFiles(targetSessionId: string, path = '') {
    setRuntimeFilesLoading(true);
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
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeFiles(null);
      setRuntimePath(path);
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setRuntimeFilesLoading(false);
      }
    }
  }

  async function fetchRuntimeChangedFiles(targetSessionId: string, path: string) {
    setRuntimeChangedFilesLoading(true);
    try {
      const payload = await api.get<SessionRuntimeGitChangedFilesResponse>(
        `/sessions/${targetSessionId}/runtime/git/changed?path=${encodeURIComponent(path)}&limit=200`,
      );
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeChangedFiles(payload);
    } catch {
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeChangedFiles(null);
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setRuntimeChangedFilesLoading(false);
      }
    }
  }

  async function fetchRuntimeDiff(targetSessionId: string, path: string) {
    setRuntimeDiffLoading(true);
    setRuntimeDiffError(null);
    try {
      const query = new URLSearchParams();
      query.set('path', path);
      query.set('base_ref', diffBaseRef.trim() || 'HEAD');
      query.set('staged', diffStaged ? 'true' : 'false');
      query.set('context_lines', '3');
      query.set('max_bytes', '120000');
      const payload = await api.get<SessionRuntimeGitDiffResponse>(
        `/sessions/${targetSessionId}/runtime/git/diff?${query.toString()}`,
      );
      if (!isCurrentSession(targetSessionId)) return;
      setRuntimeDiff(payload);
    } catch (error) {
      if (!isCurrentSession(targetSessionId)) return;
      const detail = error instanceof Error ? error.message : 'Failed to load git diff';
      setRuntimeDiffError(detail);
      setRuntimeDiff(null);
    } finally {
      if (isCurrentSession(targetSessionId)) {
        setRuntimeDiffLoading(false);
      }
    }
  }

  async function openRuntimeDirectory(path: string) {
    if (!sessionId) return;
    await fetchRuntimeFiles(sessionId, path);
  }

  async function openRuntimeFile(path: string) {
    if (!sessionId) return;
    setRuntimeFilePreviewLoadingPath(path);
    try {
      const payload = await api.get<SessionRuntimeFilePreviewResponse>(
        `/sessions/${sessionId}/runtime/file?path=${encodeURIComponent(path)}&max_bytes=32000`,
      );
      if (!isCurrentSession(sessionId)) return;
      setRuntimeFilePreview(payload);
    } catch {
      if (!isCurrentSession(sessionId)) return;
      setRuntimeFilePreview(null);
    } finally {
      if (isCurrentSession(sessionId)) {
        setRuntimeFilePreviewLoadingPath((current) => (current === path ? null : current));
      }
    }
  }

  async function refreshAll(targetSessionId: string, path = runtimePath) {
    await Promise.all([
      fetchRuntimeStatus(targetSessionId),
      fetchRuntimeFiles(targetSessionId, path),
      fetchRuntimeChangedFiles(targetSessionId, path),
    ]);
  }

  useEffect(() => {
    if (!open || !sessionId) return;
    setRuntimePath('');
    setRuntimeFilePreview(null);
    setSelectedDiffPath(null);
    setRuntimeDiff(null);
    setRuntimeDiffError(null);
    setDiffBaseRef('HEAD');
    setDiffStaged(false);
    void refreshAll(sessionId, '');
  }, [open, sessionId]);

  useEffect(() => {
    if (!open || !sessionId) return;
    const timer = window.setInterval(() => {
      void refreshAll(sessionId, runtimePath);
      if (selectedDiffPath) {
        void fetchRuntimeDiff(sessionId, selectedDiffPath);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [open, sessionId, runtimePath, selectedDiffPath, diffBaseRef, diffStaged]);

  const commandActions = useMemo(() => {
    if (!runtimeStatus) return [];
    return runtimeStatus.actions
      .filter((entry) => Boolean(runtimeActionCommand(entry)))
      .slice(0, 50);
  }, [runtimeStatus]);

  const statusCards = [
    { label: 'Runtime', value: runtimeStatus?.runtime_exists ? 'ready' : 'missing' },
    { label: 'Workspace', value: runtimeStatus?.workspace_exists ? 'mounted' : 'missing' },
    { label: 'Venv', value: runtimeStatus?.venv_exists ? 'present' : 'missing' },
    { label: 'PID', value: runtimeStatus?.active_pid ? String(runtimeStatus.active_pid) : '\u2014' },
  ];

  if (!open || !session) return null;

  const selectedDiffEntry =
    selectedDiffPath && runtimeChangedFiles?.entries
      ? runtimeChangedFiles.entries.find((entry) => entry.path === selectedDiffPath) ?? null
      : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-6xl h-[88vh] rounded-xl border border-[color:var(--border-strong)] bg-[color:var(--surface-1)] shadow-2xl overflow-hidden flex flex-col animate-in zoom-in-95 duration-200">
        <header className="px-6 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] flex items-center justify-between shrink-0">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] leading-none mb-1">
              Runtime Explorer
            </p>
            <div className="flex items-center gap-2 min-w-0">
              <p className="text-xs font-mono font-medium text-[color:var(--text-primary)] truncate">
                {session.title || `session_${session.id.slice(0, 8)}`}
              </p>
              <span
                className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest ${runtimeBadgeTone(runtimeStatus)}`}
              >
                {runtimeStatusLabel(runtimeStatus)}
              </span>
              {statusLoading ? <Loader2 size={12} className="animate-spin text-[color:var(--text-muted)]" /> : null}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="h-8 w-8 rounded border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] inline-flex items-center justify-center transition-colors text-[color:var(--text-secondary)]"
              aria-label="Close runtime explorer"
            >
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="px-6 py-2 border-b border-[color:var(--border-subtle)] flex items-center gap-1 shrink-0">
          {([
            { id: 'overview', label: 'Overview' },
            { id: 'files', label: 'Files' },
            { id: 'git', label: 'Git' },
            { id: 'commands', label: 'Commands' },
          ] as Array<{ id: RuntimeExplorerTab; label: string }>).map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest rounded transition-colors ${
                tab === item.id
                  ? 'bg-[color:var(--surface-1)] text-[color:var(--text-primary)] shadow-sm border border-[color:var(--border-subtle)]'
                  : 'border border-transparent text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]/50'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="flex-1 min-h-0 overflow-hidden">
          {tab === 'overview' ? (
            <div className="h-full overflow-y-auto p-6 custom-scrollbar space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {statusCards.map((card) => (
                  <div
                    key={card.label}
                    className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-3"
                  >
                    <div className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{card.label}</div>
                    <div className="mt-1 text-xs font-semibold text-[color:var(--text-primary)] break-all">{card.value}</div>
                  </div>
                ))}
              </div>

              <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 space-y-3">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Timestamps</div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-[11px]">
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Created</div>
                    <div className="mt-1">{runtimeStatus?.created_at ? formatCompactDate(runtimeStatus.created_at) : '\u2014'}</div>
                  </div>
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Last Used</div>
                    <div className="mt-1">{runtimeStatus?.last_used_at ? formatCompactDate(runtimeStatus.last_used_at) : '\u2014'}</div>
                  </div>
                  <div>
                    <div className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Last Active</div>
                    <div className="mt-1">{runtimeStatus?.last_active_at ? formatCompactDate(runtimeStatus.last_active_at) : '\u2014'}</div>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-2">Last Command</div>
                <div className="font-mono text-[11px] text-[color:var(--text-secondary)] break-all">
                  {runtimeStatus?.last_command || '\u2014'}
                </div>
              </div>
            </div>
          ) : null}

          {tab === 'files' ? (
            <div className="h-full min-h-0 flex">
              <div className="w-[44%] border-r border-[color:var(--border-subtle)] flex flex-col min-h-0">
                <div className="px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/30 space-y-2 shrink-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Workspace</div>
                  </div>
                  <div className="flex items-center gap-2">
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
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto p-3 custom-scrollbar">
                  {runtimeFilesLoading ? (
                    <div className="flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                      <Loader2 size={12} className="animate-spin" />
                      Loading workspace...
                    </div>
                  ) : runtimeFiles?.entries?.length ? (
                    <div className="space-y-1.5">
                      {runtimeFiles.entries.map((entry: SessionRuntimeFileEntry) => (
                        <button
                          key={`${entry.path}:${entry.kind}`}
                          type="button"
                          onClick={() => {
                            if (entry.kind === 'directory') {
                              void openRuntimeDirectory(entry.path);
                            } else {
                              void openRuntimeFile(entry.path);
                            }
                          }}
                          className="w-full rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2.5 py-2 text-left hover:border-[color:var(--accent-solid)]/40 transition-colors"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            {entry.kind === 'directory' ? (
                              <Folder size={13} className="text-sky-500 shrink-0" />
                            ) : (
                              <FileCode2 size={13} className="text-[color:var(--text-muted)] shrink-0" />
                            )}
                            <span className="text-[11px] font-semibold truncate">{entry.name}</span>
                          </div>
                          <div className="mt-1 text-[9px] text-[color:var(--text-muted)] flex items-center gap-2">
                            <span>{entry.kind === 'directory' ? 'DIR' : formatBytes(entry.size_bytes)}</span>
                            {entry.modified_at ? <span>{formatCompactDate(entry.modified_at)}</span> : null}
                          </div>
                        </button>
                      ))}
                      {runtimeFiles.truncated ? (
                        <p className="text-[9px] uppercase tracking-wider text-amber-500">List truncated to 400 entries</p>
                      ) : null}
                    </div>
                  ) : (
                    <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">Workspace is empty.</div>
                  )}
                </div>
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-2">File Preview</div>
                {runtimeFilePreviewLoadingPath ? (
                  <div className="flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                    <Loader2 size={12} className="animate-spin" />
                    Opening {runtimeFilePreviewLoadingPath}
                  </div>
                ) : runtimeFilePreview ? (
                  <div className="space-y-2">
                    <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[11px] font-semibold truncate" title={runtimeFilePreview.path}>{runtimeFilePreview.name}</div>
                        <div className="text-[9px] text-[color:var(--text-muted)]">{formatBytes(runtimeFilePreview.size_bytes)}</div>
                      </div>
                      <div className="mt-1 text-[9px] font-mono text-[color:var(--text-muted)] truncate" title={runtimeFilePreview.path}>{runtimeFilePreview.path}</div>
                    </div>
                    <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-2">
                      <Markdown
                        content={toMarkdownCodeFence(
                          runtimeFilePreview.content || '[empty file]',
                          inferCodeLanguageFromName(runtimeFilePreview.name),
                        )}
                        className="!text-[11px]"
                      />
                    </div>
                    {runtimeFilePreview.truncated ? (
                      <div className="text-[9px] uppercase tracking-wider text-amber-500">
                        Preview truncated at {formatBytes(runtimeFilePreview.max_bytes)}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="h-full min-h-[180px] rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] flex items-center justify-center text-[10px] text-[color:var(--text-muted)]">
                    Select a file to preview content.
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {tab === 'git' ? (
            <div className="h-full min-h-0 flex">
              <div className="w-[42%] border-r border-[color:var(--border-subtle)] flex flex-col min-h-0">
                <div className="px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/30 space-y-2 shrink-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Changed Files</div>
                  </div>
                  <div className="min-w-0 rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-[10px] font-mono text-[color:var(--text-secondary)] truncate">
                    /workspace{runtimePath ? `/${runtimePath}` : ''}
                  </div>
                  {runtimeChangedFiles?.git_root ? (
                    <div className="inline-flex items-center gap-1 rounded-full border border-violet-500/30 bg-violet-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-violet-300 max-w-full">
                      <GitBranch size={10} />
                      <span className="truncate" title={`${runtimeChangedFiles.git_root} ${runtimeChangedFiles.branch ?? ''}`}>
                        {runtimeChangedFiles.detached_head ? 'detached' : runtimeChangedFiles.branch || 'unknown'}
                      </span>
                    </div>
                  ) : null}
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto p-3 custom-scrollbar">
                  {runtimeChangedFilesLoading ? (
                    <div className="flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                      <Loader2 size={12} className="animate-spin" />
                      Scanning git changes...
                    </div>
                  ) : runtimeChangedFiles?.entries?.length ? (
                    <div className="space-y-1.5">
                      {runtimeChangedFiles.entries.map((entry) => (
                        <button
                          key={`runtime-diff:${entry.path}:${entry.status}`}
                          type="button"
                          onClick={() => {
                            if (!sessionId) return;
                            setSelectedDiffPath(entry.path);
                            void fetchRuntimeDiff(sessionId, entry.path);
                          }}
                          className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                            selectedDiffPath === entry.path
                              ? 'border-amber-500/50 bg-amber-500/10'
                              : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:border-[color:var(--accent-solid)]/40'
                          }`}
                          title={entry.path}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-[9px] font-bold text-[color:var(--text-muted)] w-8 shrink-0">{entry.status}</span>
                            <span className="text-[10px] font-mono truncate">{entry.path}</span>
                          </div>
                        </button>
                      ))}
                      {runtimeChangedFiles.truncated ? (
                        <p className="text-[9px] uppercase tracking-wider text-amber-500">List truncated</p>
                      ) : null}
                    </div>
                  ) : (
                    <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">
                      No changed files in this directory's git root.
                    </div>
                  )}
                </div>
              </div>

              <div className="flex-1 min-h-0 flex flex-col">
                <div className="px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-2)]/20 flex items-center gap-2">
                  <input
                    value={diffBaseRef}
                    onChange={(event) => setDiffBaseRef(event.target.value)}
                    className="h-7 w-24 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 text-[10px] font-mono"
                    placeholder="HEAD"
                    title="Base ref"
                  />
                  <label className="inline-flex items-center gap-1 text-[10px] text-[color:var(--text-muted)]">
                    <input
                      type="checkbox"
                      checked={diffStaged}
                      onChange={(event) => setDiffStaged(event.target.checked)}
                    />
                    staged
                  </label>
                  <div className="ml-auto text-[9px] uppercase tracking-wider text-[color:var(--text-muted)]">
                    {runtimeDiffLoading ? 'Loading diff…' : 'Auto refresh every 5s'}
                  </div>
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto p-4 custom-scrollbar">
                  {!selectedDiffPath ? (
                    <div className="h-full min-h-[180px] rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] flex items-center justify-center text-[10px] text-[color:var(--text-muted)]">
                      Select a changed file to inspect diff.
                    </div>
                  ) : runtimeDiffLoading ? (
                    <div className="flex items-center gap-2 text-[10px] text-[color:var(--text-muted)]">
                      <Loader2 size={12} className="animate-spin" />
                      Loading diff for {selectedDiffPath}...
                    </div>
                  ) : runtimeDiff ? (
                    <div className="space-y-2">
                      <div className="text-[10px] text-[color:var(--text-muted)] flex items-center gap-2">
                        <GitBranch size={11} />
                        <span>
                          {runtimeDiff.git_root || '.'} {runtimeDiff.detached_head ? '(detached)' : runtimeDiff.branch ? `(${runtimeDiff.branch})` : ''}
                        </span>
                      </div>
                      <div className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-2">
                        <Markdown
                          content={toMarkdownCodeFence(runtimeDiff.diff || '[no diff output]', 'diff')}
                          className="!text-[11px]"
                        />
                      </div>
                      {runtimeDiff.truncated ? (
                        <div className="text-[9px] uppercase tracking-wider text-amber-500">Diff truncated</div>
                      ) : null}
                    </div>
                  ) : runtimeDiffError ? (
                    <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-2 text-[10px] text-rose-300">
                      {runtimeDiffError}
                    </div>
                  ) : (
                    <div className="text-[10px] text-[color:var(--text-muted)] opacity-70">
                      Select a changed file to load git diff.
                    </div>
                  )}
                  {selectedDiffEntry ? (
                    <div className="mt-3 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 py-2 text-[10px] text-[color:var(--text-muted)]">
                      {selectedDiffEntry.path}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          ) : null}

          {tab === 'commands' ? (
            <div className="h-full overflow-y-auto p-6 custom-scrollbar space-y-2">
              <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Recent Commands</div>
              {commandActions.length > 0 ? (
                <div className="space-y-1.5">
                  {commandActions.map((entry, index) => {
                    const command = runtimeActionCommand(entry) || '';
                    return (
                      <div
                        key={`${entry.timestamp ?? 'na'}-${entry.action}-${index}`}
                        className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 py-1.5"
                      >
                        <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                          <Clock3 size={10} />
                          <span>{entry.action.replaceAll('_', ' ')}</span>
                          <span className="ml-auto">{entry.timestamp ? formatCompactDate(entry.timestamp) : '\u2014'}</span>
                        </div>
                        <div className="mt-1 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-1.5 py-1">
                          <Markdown
                            content={toMarkdownCodeFence(command || '[empty command]', 'bash')}
                            className="!text-[9px] markdown-workbench markdown-command-inline"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="h-full min-h-[180px] rounded-lg border border-dashed border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] flex items-center justify-center text-[10px] text-[color:var(--text-muted)] gap-2">
                  <Terminal size={12} />
                  No runtime commands yet.
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
