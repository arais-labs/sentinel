import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Download, FileCode2, Folder, GitBranch, Loader2 } from 'lucide-react';
import type { SessionRuntimeFileEntry } from '../../types/api';

interface FileTreeProps {
  rootPath: string;
  entries: SessionRuntimeFileEntry[];
  onFileClick: (entry: SessionRuntimeFileEntry) => void;
  onEntryDownload?: (entry: SessionRuntimeFileEntry) => void;
  loadFolderEntries: (path: string) => Promise<SessionRuntimeFileEntry[]>;
  onDirectoryToggle?: (entry: SessionRuntimeFileEntry, expanded: boolean) => void;
  loading?: boolean;
}

export const FileTree: React.FC<FileTreeProps> = ({
  rootPath,
  entries,
  onFileClick,
  onEntryDownload,
  loadFolderEntries,
  onDirectoryToggle,
  loading = false,
}) => {
  const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
  const [loadingPaths, setLoadingPaths] = useState<Record<string, boolean>>({});
  const [entriesByPath, setEntriesByPath] = useState<Record<string, SessionRuntimeFileEntry[]>>({});

  const normalizedRootPath = useMemo(() => rootPath.trim(), [rootPath]);

  useEffect(() => {
    setEntriesByPath((current) => ({ ...current, [normalizedRootPath]: entries }));
  }, [entries, normalizedRootPath]);

  useEffect(() => {
    setExpandedPaths({});
  }, [normalizedRootPath]);

  async function toggleDirectory(entry: SessionRuntimeFileEntry) {
    const nextExpanded = !expandedPaths[entry.path];
    setExpandedPaths((current) => ({ ...current, [entry.path]: nextExpanded }));
    onDirectoryToggle?.(entry, nextExpanded);
    if (!nextExpanded || entriesByPath[entry.path] || loadingPaths[entry.path]) return;
    setLoadingPaths((current) => ({ ...current, [entry.path]: true }));
    try {
      const children = await loadFolderEntries(entry.path);
      setEntriesByPath((current) => ({ ...current, [entry.path]: children }));
    } finally {
      setLoadingPaths((current) => ({ ...current, [entry.path]: false }));
    }
  }

  function renderEntries(items: SessionRuntimeFileEntry[], depth = 0): JSX.Element[] {
    return items.map((entry) => {
      const isDirectory = entry.kind === 'directory';
      const isExpanded = Boolean(expandedPaths[entry.path]);
      const childEntries = entriesByPath[entry.path] || [];
      const isChildLoading = Boolean(loadingPaths[entry.path]);

      return (
        <div key={`${entry.path}:${entry.kind}`} className="space-y-0.5">
          <button
            type="button"
            onClick={() => {
              if (isDirectory) {
                void toggleDirectory(entry);
                return;
              }
              onFileClick(entry);
            }}
            className="w-full group flex items-center gap-2.5 px-3 py-2 rounded-lg transition-all hover:bg-[color:var(--surface-2)] active:scale-[0.99] text-left"
            style={{ paddingLeft: `${12 + depth * 14}px` }}
          >
            <div className="shrink-0 w-3.5">
              {isDirectory ? (
                isExpanded ? (
                  <ChevronDown
                    size={12}
                    className="text-[color:var(--text-muted)] opacity-70 transition-transform duration-200"
                  />
                ) : (
                  <ChevronRight
                    size={12}
                    className="text-[color:var(--text-muted)] opacity-70 transition-transform duration-200"
                  />
                )
              ) : null}
            </div>

            <div className="shrink-0">
              {isDirectory ? (
                <Folder size={14} className="text-sky-500" />
              ) : (
                <FileCode2 size={14} className="text-[color:var(--text-muted)] group-hover:text-[color:var(--text-primary)]" />
              )}
            </div>

            <div className="flex-1 min-w-0 flex flex-col">
              <span className="text-[11px] font-medium text-[color:var(--text-primary)] truncate">
                {entry.name}
              </span>
              {isDirectory && entry.is_git_root ? (
                <div className="flex items-center gap-1 mt-0.5">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.2 text-[8px] font-bold uppercase tracking-wider ${
                      entry.git_detached_head
                        ? 'border-amber-500/30 bg-amber-500/5 text-amber-400'
                        : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-2)] text-[color:var(--text-muted)]'
                    }`}
                  >
                    <GitBranch size={9} />
                    {entry.git_detached_head ? 'detached' : entry.git_branch || 'repo'}
                  </span>
                </div>
              ) : null}
            </div>

            {onEntryDownload ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onEntryDownload(entry);
                }}
                className="shrink-0 p-1 rounded-md text-[color:var(--text-muted)] opacity-0 group-hover:opacity-100 hover:bg-[color:var(--surface-3)] hover:text-[color:var(--text-primary)] transition-all"
                title={isDirectory ? 'Download folder as zip' : 'Download file'}
                aria-label={isDirectory ? `Download folder ${entry.name} as zip` : `Download file ${entry.name}`}
              >
                <Download size={12} />
              </button>
            ) : null}
          </button>

          {isDirectory ? (
            <div
              className={`grid transition-[grid-template-rows,opacity] duration-200 ease-out ${
                isExpanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
              }`}
            >
              <div className="min-h-0 overflow-hidden">
                <div className="space-y-0.5 pt-0.5">
                  {isChildLoading ? (
                    <div
                      className="flex items-center gap-2 px-3 py-1.5 text-[10px] text-[color:var(--text-muted)] animate-in fade-in duration-150"
                      style={{ paddingLeft: `${28 + depth * 14}px` }}
                    >
                      <Loader2 size={11} className="animate-spin" />
                      Loading…
                    </div>
                  ) : childEntries.length > 0 ? (
                    renderEntries(childEntries, depth + 1)
                  ) : (
                    <div
                      className="px-3 py-1.5 text-[10px] text-[color:var(--text-muted)] opacity-60 animate-in fade-in duration-150"
                      style={{ paddingLeft: `${28 + depth * 14}px` }}
                    >
                      Empty folder
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      );
    });
  }

  if (loading && entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-[color:var(--text-muted)] animate-pulse">
        <Loader2 size={20} className="animate-spin mb-3" />
        <p className="text-[10px] font-bold uppercase tracking-widest">Loading Workspace...</p>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-[color:var(--text-muted)] opacity-40">
        <div className="p-3 rounded-2xl bg-[color:var(--surface-2)] mb-3">
          <FileCode2 size={20} strokeWidth={1.5} />
        </div>
        <p className="text-[10px] font-bold uppercase tracking-[0.1em]">Workspace is empty</p>
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {renderEntries(entries)}
    </div>
  );
};
