import React, { useState } from 'react';
import { 
  FileCode2, 
  Files, 
  GitBranch, 
  Layout, 
  Loader2, 
  Terminal, 
  X 
} from 'lucide-react';
import type { 
  SessionRuntimeFileEntry, 
  SessionRuntimeGitDiffResponse 
} from '../../types/api';
import { DiffViewer, type DiffViewMode } from './DiffViewer';
import { WorkbenchExplorerPane, type WorkbenchRepoChangesSection } from './WorkbenchExplorerPane';
import { Markdown } from '../ui/Markdown';

export interface WorkbenchTab {
  path: string;
  name: string;
  size_bytes: number;
  modified_at: string | null;
  content: string;
  truncated: boolean;
  max_bytes: number;
}

interface WorkbenchProps {
  tabs: WorkbenchTab[];
  activeTabPath: string | null;
  onTabClick: (path: string) => void;
  onTabClose: (path: string) => void;
  onCloseAll: () => void;
  showExplorer?: boolean;
  
  // Explorer
  explorerEntries: SessionRuntimeFileEntry[];
  currentExplorerPath: string;
  explorerLoading: boolean;
  onExplorerFileClick: (entry: SessionRuntimeFileEntry) => void;
  onExplorerDownload?: (entry: SessionRuntimeFileEntry) => void;
  loadExplorerDirectory: (path: string) => Promise<SessionRuntimeFileEntry[]>;
  onExplorerDirectoryToggle?: (entry: SessionRuntimeFileEntry, expanded: boolean) => void;
  explorerRefreshKey?: number;
  
  // Git / Diff
  repoChangesSections: WorkbenchRepoChangesSection[];
  expandedGitDirs: Record<string, boolean>;
  onToggleGitDir: (path: string) => void;
  onGitFileClick: (path: string) => void;
  diffMode: boolean;
  setDiffMode: (enabled: boolean) => void;
  diffContent: SessionRuntimeGitDiffResponse | null;
  diffLoading: boolean;
  diffError: string | null;
  diffBaseRef: string;
  onDiffBaseRefChange: (ref: string) => void;
  diffBaseRefOptions: string[];
  
  // Layout
  width: number;
  className?: string;
}

export const Workbench: React.FC<WorkbenchProps> = ({
  tabs,
  activeTabPath,
  onTabClick,
  onTabClose,
  onCloseAll,
  showExplorer = true,
  explorerEntries,
  currentExplorerPath,
  explorerLoading,
  onExplorerFileClick,
  onExplorerDownload,
  loadExplorerDirectory,
  onExplorerDirectoryToggle,
  explorerRefreshKey = 0,
  repoChangesSections,
  expandedGitDirs,
  onToggleGitDir,
  onGitFileClick,
  diffMode,
  setDiffMode,
  diffContent,
  diffLoading,
  diffError,
  diffBaseRef,
  onDiffBaseRefChange,
  diffBaseRefOptions,
  width,
  className = '',
}) => {
  const [explorerVisible, setExplorerVisible] = useState(showExplorer);
  const [diffViewMode, setDiffViewMode] = useState<DiffViewMode>('unified');
  
  const activeTab = tabs.find(t => t.path === activeTabPath) || tabs[0] || null;

  React.useEffect(() => {
    setExplorerVisible(showExplorer);
  }, [showExplorer]);

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

  return (
    <aside 
      style={{ width: `${width}px` }}
      className={`relative z-30 flex h-full flex-col border-l border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden min-w-[400px] animate-[workbenchDockIn_180ms_ease-out] ${className}`.trim()}
    >
      <div className="flex flex-1 min-h-0">
        {showExplorer ? (
          <>
            <div className="w-12 shrink-0 flex flex-col items-center py-4 border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]/50">
              <button 
                onClick={() => setExplorerVisible(!explorerVisible)}
                className={`p-2 rounded-lg transition-colors mb-2 ${explorerVisible ? 'text-[color:var(--accent-solid)] bg-[color:var(--accent-solid)]/10' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'}`}
                title="Explorer"
              >
                <Files size={20} />
              </button>
              <div className="flex-1" />
              <button 
                className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors"
                title="Search (Coming soon)"
              >
                <Layout size={20} />
              </button>
            </div>

            {explorerVisible && (
              <div className="w-72 shrink-0 border-r border-[color:var(--border-subtle)]">
                <WorkbenchExplorerPane
                  currentPath={currentExplorerPath}
                  explorerLoading={explorerLoading}
                  explorerEntries={explorerEntries}
                  onExplorerFileClick={onExplorerFileClick}
                  onExplorerDownload={onExplorerDownload}
                  loadExplorerDirectory={loadExplorerDirectory}
                  onExplorerDirectoryToggle={onExplorerDirectoryToggle}
                  explorerRefreshKey={explorerRefreshKey}
                  repoChangesSections={repoChangesSections}
                  expandedGitDirs={expandedGitDirs}
                  onToggleGitDir={onToggleGitDir}
                  onGitFileClick={onGitFileClick}
                />
              </div>
            )}
          </>
        ) : null}

        {/* Editor Area */}
        <div className="flex-1 flex flex-col min-w-0 bg-[color:var(--surface-0)]">
          {/* Tabs Bar */}
          <div className="relative h-12 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)]/80 backdrop-blur-md flex items-center overflow-x-auto no-scrollbar pr-12">
            {tabs.map(tab => (
              <div 
                key={tab.path}
                className={`group h-full flex items-center gap-2 px-3 border-r border-[color:var(--border-subtle)] min-w-[120px] max-w-[200px] cursor-pointer transition-colors relative ${
                  activeTabPath === tab.path 
                    ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-[color:var(--accent-solid)]' 
                    : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                }`}
                onClick={() => onTabClick(tab.path)}
              >
                <FileCode2 size={14} className={activeTabPath === tab.path ? 'text-[color:var(--accent-solid)]' : 'opacity-60'} />
                <span className="text-[11px] font-medium truncate flex-1">{tab.name}</span>
                <button 
                  onClick={(e) => { e.stopPropagation(); onTabClose(tab.path); }}
                  className="p-0.5 rounded-md hover:bg-rose-500/10 hover:text-rose-500 opacity-0 group-hover:opacity-100 transition-all"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            {tabs.length === 0 && (
              <div className="px-4 text-[10px] text-[color:var(--text-muted)] italic">
                No open files
              </div>
            )}
            <button
              onClick={onCloseAll}
              className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 text-[color:var(--text-muted)] hover:text-rose-500 transition-colors"
              title="Close file panel"
            >
              <X size={14} />
            </button>
          </div>

          {activeTab ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* Toolbar */}
              <div className="p-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 flex items-center justify-between gap-4">
                <div className="flex items-center gap-1 rounded-lg border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)]">
                  <button
                    onClick={() => setDiffMode(false)}
                    className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all ${
                      !diffMode ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] shadow-sm' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                    }`}
                  >
                    Content
                  </button>
                  <button
                    onClick={() => setDiffMode(true)}
                    className={`px-3 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all ${
                      diffMode ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] shadow-sm' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                    }`}
                  >
                    Diff
                  </button>
                </div>

                {diffMode && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-tight">Base Ref:</span>
                    <select
                      value={diffBaseRef}
                      onChange={(e) => onDiffBaseRefChange(e.target.value)}
                      className="h-7 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 text-[10px] font-mono text-[color:var(--text-secondary)] focus:outline-none focus:ring-1 focus:ring-[color:var(--accent-solid)]"
                    >
                      {diffBaseRefOptions.map(ref => (
                        <option key={ref} value={ref}>{ref}</option>
                      ))}
                    </select>
                    <div className="flex items-center gap-1 rounded-lg border border-[color:var(--border-subtle)] p-0.5 bg-[color:var(--surface-2)]">
                      <button
                        type="button"
                        onClick={() => setDiffViewMode('unified')}
                        className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all ${
                          diffViewMode === 'unified'
                            ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] shadow-sm'
                            : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                        }`}
                      >
                        Unified
                      </button>
                      <button
                        type="button"
                        onClick={() => setDiffViewMode('split')}
                        className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-all ${
                          diffViewMode === 'split'
                            ? 'bg-[color:var(--surface-0)] text-[color:var(--text-primary)] shadow-sm'
                            : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
                        }`}
                      >
                        Split
                      </button>
                    </div>
                  </div>
                )}

                <div className="flex-1 min-w-0" />
                
                <div className="text-[10px] text-[color:var(--text-muted)] font-mono">
                  {activeTab.path}
                </div>
              </div>

              <div className="flex-1 min-h-0 relative">
                {diffMode ? (
                  diffLoading ? (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[color:var(--surface-0)]/50 backdrop-blur-[1px] z-10">
                      <Loader2 size={24} className="animate-spin text-[color:var(--accent-solid)]" />
                      <span className="text-[11px] font-medium text-[color:var(--text-secondary)]">Loading Git Diff...</span>
                    </div>
                  ) : diffError ? (
                    <div className="m-4 rounded-xl border border-rose-500/30 bg-rose-500/5 p-6 text-center">
                      <p className="text-rose-500 text-[11px] font-medium">{diffError}</p>
                      <button
                        onClick={() => setDiffMode(true)}
                        className="mt-4 px-4 py-2 rounded-lg bg-rose-500/10 text-rose-500 text-[10px] font-bold uppercase tracking-widest hover:bg-rose-500/20 transition-all"
                      >
                        Retry Load
                      </button>
                    </div>
                  ) : diffContent ? (
                    <div className="h-full flex flex-col animate-in fade-in slide-in-from-top-2 duration-300">
                      <div className="px-4 py-2 flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40">
                        <div className="flex items-center gap-2">
                          <GitBranch size={12} />
                          <span>Root: {diffContent.git_root || '.'}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className={diffContent.truncated ? 'text-amber-500' : ''}>
                            {diffContent.truncated ? 'TRUNCATED' : 'FULL DIFF'}
                          </span>
                          <span>{diffContent.max_bytes / 1024}KB Limit</span>
                        </div>
                      </div>
                      <div className="flex-1 min-h-0 overflow-auto">
                        <DiffViewer diff={diffContent.diff} viewMode={diffViewMode} />
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-[color:var(--text-muted)] opacity-50">
                      <Terminal size={32} strokeWidth={1} className="mb-4" />
                      <p className="text-[11px] font-medium uppercase tracking-[0.2em]">Open Diff to compare versions</p>
                    </div>
                  )
                ) : (
                  <div className="animate-in fade-in duration-300 h-full overflow-auto p-4">
                    <Markdown
                      content={toMarkdownCodeFence(
                        activeTab.content || '[empty file]',
                        inferCodeLanguageFromName(activeTab.name),
                      )}
                      className="!text-[12px] markdown-workbench h-full"
                    />
                    {activeTab.truncated && (
                      <div className="mt-4 p-3 rounded-lg border border-dashed border-amber-500/30 bg-amber-500/5 text-center">
                        <p className="text-[10px] font-bold text-amber-500 uppercase tracking-widest">
                          File truncated at {activeTab.max_bytes / 1024}KB
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-[color:var(--text-muted)] p-12 text-center">
              <div className="w-16 h-16 rounded-3xl bg-[color:var(--surface-2)] flex items-center justify-center mb-6">
                <Files size={32} strokeWidth={1} />
              </div>
              <h3 className="text-[13px] font-bold text-[color:var(--text-primary)] mb-2 uppercase tracking-widest">Workbench</h3>
              <p className="text-[11px] max-w-[200px] leading-relaxed opacity-60">
                Open files from the explorer to view their contents or compare versions using git diff.
              </p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};
