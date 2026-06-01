import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { FolderTree } from 'lucide-react';

import { Workbench } from '../components/workbench/Workbench';
import { useInstanceName } from '../lib/workspace-context';
import { useActiveSessionId } from '../store/active-session-store';
import { useSessionRuntimeStream } from '../hooks/useSessionRuntimeStream';
import { useSessionWorkbench } from '../hooks/useSessionWorkbench';
import type { SessionRuntimeFileEntry } from '../types/api';

/**
 * Standalone FILES workspace tab.
 *
 * Hosts the FULL file workbench for the workspace-wide active session:
 *   - directory browser (WorkbenchExplorerPane via Workbench's `showExplorer`);
 *   - open-file → VIEW contents;
 *   - git DIFF view (with base-ref selection);
 *   - repo-changes sections + expand/collapse;
 *   - file/folder download.
 *
 * All file/git logic lives in {@link useSessionWorkbench}; this component only
 * wires the hook to the {@link Workbench} surface and drives the same
 * fetch/refresh lifecycle SessionsPage's files view used. It subscribes to the
 * shared, ref-counted session stream purely to know when the runtime is
 * `connected` so the live 3s refresh loop matches the original behavior — that
 * subscription reuses the single socket already opened for the session.
 */
export function FilesTab() {
  const instanceName = useInstanceName() ?? null;
  const activeSessionId = useActiveSessionId();

  const {
    runtimeFiles,
    runtimePath,
    runtimeFilesLoading,
    runtimeFilesRefreshKey,
    fetchRuntimeFiles,
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
    activeWorkbenchTab,
    openRuntimeFile,
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
  } = useSessionWorkbench(activeSessionId);

  // Subscribe to the shared session stream so we know when the runtime is
  // connected (gates the live refresh loop). Ref-counted: this reuses the same
  // socket SessionsPage / Desktop / Terminal already opened for this session.
  const { connection } = useSessionRuntimeStream(instanceName, activeSessionId);

  // Workbench sizes itself off an explicit pixel `width`. Inside a flexible
  // workspace pane we measure the host so the workbench fills it (down to its
  // own 400px min-width) and tracks pane resizes.
  const hostRef = useRef<HTMLDivElement>(null);
  const [hostWidth, setHostWidth] = useState(0);
  useLayoutEffect(() => {
    const node = hostRef.current;
    if (!node) return;
    setHostWidth(node.clientWidth);
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width;
      if (typeof next === 'number') setHostWidth(next);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [activeSessionId]);

  // Initial + on-session-change load. Mirrors SessionsPage's files-view fetch
  // (refreshGit so repo-changes sections populate alongside the tree).
  useEffect(() => {
    if (!activeSessionId) return;
    void fetchRuntimeFiles('', { refreshGit: true, silent: false });
  }, [activeSessionId, fetchRuntimeFiles]);

  // Live refresh loop: while connected, silently re-fetch the current dir +
  // repo changes every 3s. Matches the original files-view polling exactly.
  useEffect(() => {
    if (!activeSessionId) return;
    if (connection !== 'connected') return;
    const timer = window.setInterval(() => {
      void fetchRuntimeFiles(runtimePath, { refreshGit: true, silent: true });
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeSessionId, connection, runtimePath, fetchRuntimeFiles]);

  const handleExplorerDirectoryToggle = useCallback(
    (entry: SessionRuntimeFileEntry, expanded: boolean) => {
      if (!activeSessionId || !entry.is_git_root) return;
      if (!expanded) {
        forgetRepoRoot(entry.path);
        return;
      }
      void fetchChangedFilesForRepo(entry.path);
    },
    [activeSessionId, forgetRepoRoot, fetchChangedFilesForRepo],
  );

  if (!activeSessionId) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-12 text-center text-[color:var(--text-muted)]">
        <div className="flex h-16 w-16 items-center justify-center rounded-3xl bg-[color:var(--surface-2)]">
          <FolderTree size={32} strokeWidth={1} />
        </div>
        <div className="space-y-1">
          <h3 className="text-[13px] font-bold uppercase tracking-widest text-[color:var(--text-primary)]">
            Files
          </h3>
          <p className="max-w-[240px] text-[11px] leading-relaxed opacity-70">
            Select a session to browse its runtime files, view contents, and inspect git diffs.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div ref={hostRef} className="flex h-full w-full min-h-0 overflow-hidden">
      <Workbench
        className="h-full flex-1 border-l-0"
        width={hostWidth}
        tabs={workbenchTabs}
        activeTabPath={activeWorkbenchPath}
        onTabClick={(path) => setActiveWorkbenchPath(path)}
        onTabClose={closeWorkbenchTab}
        onCloseAll={closeAllWorkbenchTabs}
        showExplorer
        explorerEntries={runtimeFiles?.entries || []}
        currentExplorerPath={runtimePath}
        explorerLoading={runtimeFilesLoading}
        onExplorerFileClick={(entry) => void openRuntimeFile(entry.path)}
        onExplorerDownload={(entry) => void downloadRuntimeEntry(entry)}
        loadExplorerDirectory={loadRuntimeDirectoryEntries}
        explorerRefreshKey={runtimeFilesRefreshKey}
        onExplorerDirectoryToggle={handleExplorerDirectoryToggle}
        repoChangesSections={repoChangeSections}
        expandedGitDirs={expandedGitDirs}
        onToggleGitDir={toggleGitDir}
        onGitFileClick={(path) => void openRuntimeFileDiff(path)}
        diffMode={activeWorkbenchTab ? workbenchShowDiffByPath[activeWorkbenchTab.path] ?? false : false}
        setDiffMode={(enabled) => {
          if (!activeWorkbenchTab) return;
          setShowDiffForPath(activeWorkbenchTab.path, enabled);
        }}
        diffContent={activeWorkbenchDiff}
        diffLoading={activeWorkbenchDiffLoading}
        diffError={activeWorkbenchDiffError}
        diffBaseRef={activeWorkbenchBaseRef}
        onDiffBaseRefChange={(ref) => {
          if (!activeWorkbenchTab) return;
          setDiffBaseRefForPath(activeWorkbenchTab.path, ref);
        }}
        diffBaseRefOptions={activeWorkbenchBaseRefOptions}
      />
    </div>
  );
}
