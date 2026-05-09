import React from 'react';
import { ChevronDown, ChevronRight, Folder, Loader2 } from 'lucide-react';

import type { SessionRuntimeFileEntry } from '../../types/api';
import type { RuntimeGitChangedTreeNode } from '../../lib/runtimeGitTree';
import { FileTree } from './FileTree';

export interface WorkbenchRepoChangesSection {
  id: string;
  title: string;
  tree: RuntimeGitChangedTreeNode[];
  loading: boolean;
}

interface WorkbenchExplorerPaneProps {
  showTitle?: boolean;
  currentPath: string;
  explorerLoading: boolean;
  explorerEntries: SessionRuntimeFileEntry[];
  onExplorerFileClick: (entry: SessionRuntimeFileEntry) => void;
  onExplorerDownload?: (entry: SessionRuntimeFileEntry) => void;
  loadExplorerDirectory: (path: string) => Promise<SessionRuntimeFileEntry[]>;
  onExplorerDirectoryToggle?: (entry: SessionRuntimeFileEntry, expanded: boolean) => void;
  repoChangesSections: WorkbenchRepoChangesSection[];
  expandedGitDirs: Record<string, boolean>;
  onToggleGitDir: (path: string) => void;
  onGitFileClick: (path: string) => void;
}

export const WorkbenchExplorerPane: React.FC<WorkbenchExplorerPaneProps> = ({
  showTitle = true,
  currentPath,
  explorerLoading,
  explorerEntries,
  onExplorerFileClick,
  onExplorerDownload,
  loadExplorerDirectory,
  onExplorerDirectoryToggle,
  repoChangesSections,
  expandedGitDirs,
  onToggleGitDir,
  onGitFileClick,
}) => {
  function displayGitStatus(status: string | undefined): string {
    if (!status) return 'M';
    return status === '??' ? 'N' : status;
  }

  function renderGitTree(nodes: RuntimeGitChangedTreeNode[], depth = 0): JSX.Element[] {
    return nodes.map((node) => {
      if (node.kind === 'directory') {
        const expanded = expandedGitDirs[node.fullPath] ?? false;
        return (
          <div key={node.key} className="space-y-0.5">
            <button
              type="button"
              onClick={() => onToggleGitDir(node.fullPath)}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors hover:bg-[color:var(--surface-2)] text-left"
              style={{ paddingLeft: `${8 + depth * 12}px` }}
            >
              {expanded ? (
                <ChevronDown size={12} className="shrink-0 text-violet-500/70" />
              ) : (
                <ChevronRight size={12} className="shrink-0 text-violet-500/70" />
              )}
              <Folder size={12} className="shrink-0 text-violet-600/80" />
              <span className="truncate text-[10px] font-mono text-violet-700 dark:text-violet-200/90 flex-1">
                {node.name}
              </span>
              <span className="shrink-0 text-[8px] font-bold text-violet-500/60">{node.fileCount}</span>
            </button>
            {expanded ? renderGitTree(node.children, depth + 1) : null}
          </div>
        );
      }

      return (
        <button
          key={`git-change:${node.fullPath}`}
          type="button"
          onClick={() => onGitFileClick(node.fullPath)}
          className="group w-full flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors hover:bg-violet-500/5 text-left"
          style={{ paddingLeft: `${24 + depth * 12}px` }}
        >
          <span className="w-8 shrink-0 text-[8px] font-black text-violet-500/70">
            {displayGitStatus(node.entry?.status)}
          </span>
          <span className="truncate text-[10px] font-mono text-violet-700 dark:text-violet-200/90 flex-1">
            {node.name}
          </span>
          <ChevronRight size={11} className="shrink-0 opacity-0 group-hover:opacity-100 text-violet-500/40" />
        </button>
      );
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[color:var(--surface-1)]">
      {showTitle ? (
        <div className="p-3 border-b border-[color:var(--border-subtle)] flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Explorer</span>
          <div className="flex items-center gap-1">
            {explorerLoading ? <Loader2 size={12} className="animate-spin text-[color:var(--text-muted)]" /> : null}
          </div>
        </div>
      ) : null}
      <div className="flex-1 overflow-y-auto p-2 space-y-4">
        <div className="space-y-2">
          <div className="px-2">
            <span className="text-[9px] font-black uppercase tracking-[0.2em] text-violet-500">Repo Changes</span>
          </div>
          {repoChangesSections.length > 0 ? (
            <div className="space-y-3">
              {repoChangesSections.map((section) => (
                <div key={section.id} className="space-y-1">
                  <div className="flex items-center justify-between px-2">
                    <span className="truncate text-[10px] font-mono text-violet-700 dark:text-violet-200/90">
                      {section.title}
                    </span>
                    {section.loading ? <Loader2 size={10} className="animate-spin text-violet-500" /> : null}
                  </div>
                  {section.tree.length > 0 ? (
                    <div className="space-y-0.5">
                      {renderGitTree(section.tree)}
                    </div>
                  ) : (
                    <div className="px-2 py-1 text-[10px] text-violet-500/70">
                      No repo changes.
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="px-2 py-1 text-[10px] text-violet-500/70">
              Expand a git repo folder to inspect its changes.
            </div>
          )}
        </div>

        <div className="space-y-2">
          {showTitle ? (
            <div className="flex items-center justify-between px-2">
              <span className="text-[9px] font-black uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Workspace</span>
              {explorerLoading ? <Loader2 size={10} className="animate-spin text-[color:var(--text-muted)]" /> : null}
            </div>
          ) : explorerLoading ? (
            <div className="flex items-center justify-end px-2">
              <Loader2 size={10} className="animate-spin text-[color:var(--text-muted)]" />
            </div>
          ) : null}
          <FileTree
            rootPath={currentPath}
            entries={explorerEntries}
            onFileClick={onExplorerFileClick}
            onEntryDownload={onExplorerDownload}
            loadFolderEntries={loadExplorerDirectory}
            onDirectoryToggle={onExplorerDirectoryToggle}
            loading={explorerLoading}
          />
        </div>
      </div>
    </div>
  );
};
