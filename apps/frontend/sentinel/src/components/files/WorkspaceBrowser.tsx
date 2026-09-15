import { FilesPaneControls } from './FilesPaneControls';
import { ExplorerSidebar } from './ExplorerSidebar';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Menu, MenuItem } from '@mui/material';
import { Check, ChevronRight, Download, Upload, File, Files, GitBranch, GitCommitHorizontal, History, Layers, Link2, Loader2, Pin, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, X } from 'lucide-react';
import { downloadFile } from '../../lib/api';
import { useWorkspaceStore } from '../../store/workspace-store';
import type { Workspace } from '../../types/api';
import { exceedsPreviewBudget } from '../workbench/previewBudget';
import { DiffViewer } from '../workbench/DiffViewer';
import { Markdown } from '../ui/Markdown';
import { HtmlFilePreview } from './HtmlFilePreview';
import { MediaFilePreview, mediaKind } from './MediaFilePreview';
import { ProjectTree } from './ProjectTree';
import { WorktreePicker } from './WorktreePicker';
import { RepositoryPicker } from './RepositoryPicker';
import { ChangedFilesTree } from './ChangedFilesTree';
import { relativeChangePath } from './changeTree';
import { gitFileStatus, gitStatusLabel } from './gitStatus';
import { useWorkspaceBrowser } from './useWorkspaceBrowser';
import { useFileUploads } from './useFileUploads';
import { filename, type Change, type FileTab } from './types';
import './workspace-browser.css';

const formatBytes = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${(bytes / 1024 ** 3).toFixed(2)} GB`;

export function WorkspaceBrowser({ instance, workspace, workspaces, onWorkspace, pinned, followingAttachment, attachmentLoading, onPin }: {
  instance: string; workspace: Workspace; workspaces: Workspace[]; onWorkspace: (id: string) => void;
  pinned: boolean; followingAttachment: boolean; attachmentLoading: boolean; onPin: () => void;
}) {
  const browser = useWorkspaceBrowser(workspace.id, instance);
  const { prefs, setPrefs, context, changes, activeTab } = browser;
  const uploads = useFileUploads({ instance, workspaceId: workspace.id, root: prefs.root, disabled: browser.needsStart || browser.loading });
  const uploadPicker = useRef<HTMLInputElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [narrow, setNarrow] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [size, setSize] = useState(() => Number(localStorage.getItem('project-sidebar-width')) || 240);
  const [hidden, setHidden] = useState(false);
  const [split, setSplit] = useState(false);
  const [htmlPreview, setHtmlPreview] = useState(false);
  const [actionError, setActionError] = useState('');
  const [copied, setCopied] = useState('');
  const [tabMenu, setTabMenu] = useState<{ path: string; anchor: HTMLElement } | null>(null);
  useEffect(() => {
    if (!tabMenu) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && !target.closest('.project-tab-menu, .project-file-tab')) setTabMenu(null);
    };
    document.addEventListener('pointerdown', dismiss, true);
    return () => document.removeEventListener('pointerdown', dismiss, true);
  }, [tabMenu]);
  function closeTabs(scope: 'others' | 'right' | 'all') {
    if (!tabMenu) return;
    setPrefs(value => {
      const index = value.tabs.findIndex(tab => tab.path === tabMenu.path);
      const tabs = scope === 'all' ? [] : value.tabs.filter((tab, i) => scope === 'others' ? tab.path === tabMenu.path : i <= index);
      return { ...value, tabs, active: tabs.some(tab => tab.path === value.active) ? value.active : tabs.at(-1)?.path ?? null };
    });
    setTabMenu(null);
  }
  const repo = context?.repository;
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < 680));
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const [searchFocusRequest, requestSearchFocus] = useState(0);
  const findFile = useCallback(() => {
    setSidebar(true);
    setPrefs(value => ({ ...value, view: 'files' }));
    requestSearchFocus(value => value + 1);
  }, [setPrefs]);
  useEffect(() => {
    if (searchFocusRequest && sidebar && prefs.view === 'files') search.current?.focus();
  }, [searchFocusRequest, sidebar, prefs.view]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.shiftKey || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'p') return;
      if (!host.current?.getClientRects().length || browser.needsStart) return;
      event.preventDefault();
      findFile();
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [findFile, browser.needsStart]);
  function open(path: string, mode: FileTab['mode'] = 'file') { browser.open(path, mode); if (narrow) setSidebar(false); }
  function download() {
    if (!activeTab) return;
    downloadFile(`/instances/${encodeURIComponent(instance)}/workspaces/${workspace.id}/browse/content?download=true&path=${encodeURIComponent(activeTab.path)}`, filename(activeTab.path));
  }
  function mode(mode: FileTab['mode']) {
    if (!activeTab) return;
    setPrefs(value => ({ ...value, tabs: value.tabs.map(tab => tab.path === activeTab.path ? { ...tab, mode, base: mode === 'working' ? '' : 'HEAD' } : tab) }));
  }
  const groups: { label: string; entries: Change[]; mode: FileTab['mode'] }[] = [
    { label: 'Conflicts', entries: changes.filter(item => item.conflicted), mode: 'combined' },
    { label: 'Staged', entries: changes.filter(item => item.staged && !item.conflicted), mode: 'staged' },
    { label: 'Unstaged', entries: changes.filter(item => item.unstaged && !item.conflicted), mode: 'working' },
    { label: 'Unversioned', entries: changes.filter(item => item.untracked), mode: 'combined' },
  ];
  const filePath = activeTab?.path ?? '';
  const content = browser.preview?.content ?? '';
  const isHtml = /\.html?$/i.test(filePath);
  const showHtml = isHtml && htmlPreview && activeTab?.mode === 'file';
  const showMedia = activeTab?.mode === 'file' && mediaKind(browser.preview?.media_type) !== null;
  const ext = filename(filePath).split('.').pop() ?? '';
  const language = ({ ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript', py: 'python', rs: 'rust', sh: 'bash', md: 'markdown', yml: 'yaml' } as Record<string, string>)[ext] ?? ext;
  let fence = '```'; while (content.includes(fence)) fence += '`';
  return <div ref={host} className="project-browser" data-narrow={narrow} data-file-drag={uploads.target !== null || undefined} {...uploads.handlers}>
    <FilesPaneControls>
      <select aria-label="Workspace" value={workspace.id} onChange={event => onWorkspace(event.target.value)} className="project-workspace-select chat-header-pill">{workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      <button className="project-follow-button chat-header-pill" aria-label={pinned ? 'Follow session workspace' : 'Pin this workspace'} aria-pressed={pinned}
        title={pinned ? 'Pinned: session changes will not switch this workspace. Click to follow the session.' : followingAttachment ? 'Following the session’s workspace. Click to pin here.' : 'Following sessions. This session has no attached workspace; showing your last project.'} onClick={onPin}>
        {pinned ? <Pin size={12} /> : <Link2 size={12} />}<span>{pinned ? 'Pinned' : attachmentLoading ? 'Following session…' : followingAttachment ? 'Following session' : 'No attachment'}</span>
      </button>
      {!browser.needsStart && <RepositoryPicker loading={browser.reposLoading} errors={browser.repoErrors} onRetry={browser.refresh} repositories={browser.repos} current={repo} root={prefs.root} workspaceName={workspace.name}
        includeGenerated={prefs.includeGenerated} onGenerated={includeGenerated => setPrefs(value => ({ ...value, includeGenerated }))} onSelect={browser.selectRoot} />}
      {!browser.needsStart && repo && <WorktreePicker key={`${instance}:${workspace.id}`} repository={repo} worktrees={browser.worktrees} onOpen={() => void browser.loadWorktrees()} loading={browser.worktreesLoading} loadError={browser.worktreesError} storageKey={`${instance}:${workspace.id}`} onSelect={browser.selectRoot} />}
      <button className="project-icon-button chat-header-pill" title="Refresh files and Git" onClick={browser.refresh}><RefreshCw size={14} className={browser.loading ? 'animate-spin' : ''} /></button>
      <button className="project-icon-button chat-header-pill" title="Upload files" aria-label="Upload files" disabled={browser.needsStart || browser.loading || uploads.busy} onClick={() => uploadPicker.current?.click()}><Upload size={14} /></button>
    </FilesPaneControls>
    <input ref={uploadPicker} type="file" multiple hidden onChange={event => { if (event.target.files) uploads.choose(event.target.files); event.target.value = ''; }} />
    {uploads.message && <div className="project-upload-status" role={uploads.error ? 'alert' : 'status'} data-error={uploads.error || undefined}>
      {uploads.busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}<span>{uploads.message}{uploads.busy && <><progress aria-label="File upload progress" max={uploads.totalBytes || uploads.total || 1} value={uploads.totalBytes ? uploads.bytes : uploads.completed} /><small>{formatBytes(uploads.bytes)} / {formatBytes(uploads.totalBytes)} · {uploads.completed} / {uploads.total} items saved</small></>}</span>
      <button className="project-text-button" onClick={uploads.busy ? uploads.cancel : uploads.clear}>{uploads.busy ? 'Cancel' : <X size={14} aria-label="Dismiss upload status" />}</button>
    </div>}
    {uploads.target !== null && <div className="project-upload-overlay" aria-hidden="true"><Upload size={24} /><strong>{browser.needsStart ? 'Start this workspace to upload' : browser.loading ? 'Waiting for files…' : uploads.busy ? 'An upload is already in progress' : `Drop into ${workspace.name}`}</strong><span>{uploads.target || 'Project folder'}</span><small>Existing files are kept. Files with matching names are renamed.</small></div>}
    {actionError && !browser.needsStart && <div className="project-error" role="alert">{actionError}<button onClick={() => setActionError('')} aria-label="Dismiss error"><X size={13} /></button></div>}
    <div className="project-body" data-explorer-open={sidebar}>
      {!sidebar && !browser.needsStart && <div className="project-explorer-edge"><button type="button" className="project-icon-button" title="Show explorer" aria-label="Show explorer" aria-expanded={false} onClick={() => setSidebar(true)}><PanelLeftOpen size={16} /></button></div>}
      {browser.needsStart ? <div className="project-empty m-auto max-w-sm" role="status">
        <strong>Workspace unavailable</strong>
        <p>Files are unavailable until {workspace.name} is ready. Check the workspace to resolve the issue.</p>
        <button className="project-text-button" onClick={() => useWorkspaceStore.getState().openTab('workspaces')}>Open Workspaces<ChevronRight size={13} className="inline ml-1" /></button>
      </div> : <>
        {narrow && sidebar && <button className="project-drawer-backdrop" aria-label="Close explorer" onClick={() => setSidebar(false)} />}
      <ExplorerSidebar className="project-explorer-shell" open={sidebar} width={narrow ? Math.min(size, 300) : size + 3} collapsedWidth={narrow ? 0 : 36} overlay={narrow}>
        <aside className="project-sidebar" style={{ width: narrow ? Math.min(size, 300) : size }}>
          <div className="project-explorer-heading"><span>EXPLORER</span><button type="button" className="project-icon-button" title="Hide explorer" aria-label="Hide explorer" aria-expanded={true} onClick={() => setSidebar(false)}><PanelLeftClose size={16} /></button></div>
          <nav className="project-view-tabs" aria-label="Browser view"><span aria-hidden="true" className="project-view-indicator" style={{ transform: `translateX(${(['files', 'changes', 'history'].indexOf(prefs.view)) * 100}%)` }} />{(['files', 'changes', 'history'] as const).map(view => <button key={view} aria-pressed={prefs.view === view} onClick={() => setPrefs(value => ({ ...value, view }))}>{view === 'files' ? 'Files' : view === 'changes' ? 'Changes' : 'History'}{view === 'changes' && changes.length > 0 && <span>{changes.length}{browser.changesTruncated ? '+' : ''}</span>}</button>)}</nav>
          {prefs.view === 'files' && <div className="project-search-row"><div className="project-search"><Search size={13} /><input ref={search} aria-label="Find a file" placeholder={prefs.changedOnly ? "Find a changed file…" : "Find a file…"} value={browser.query} onChange={event => browser.setQuery(event.target.value)} />{(!prefs.changedOnly || browser.query) && <button title={browser.query ? 'Clear search' : hidden ? 'Hide dotfiles' : 'Show dotfiles'} aria-label={browser.query ? 'Clear search' : 'Toggle hidden files'} onClick={() => browser.query ? browser.setQuery('') : setHidden(value => !value)}>{browser.query ? <X size={12} /> : <span className={hidden ? 'project-active' : ''}>···</span>}</button>}</div><button type="button" className="project-changed-filter project-icon-button" aria-label="Show changed files only" title={prefs.changedOnly ? 'Show all files' : 'Show changed files only'} aria-pressed={prefs.changedOnly} onClick={() => setPrefs(value => ({ ...value, changedOnly: !value.changedOnly }))}><GitBranch size={14} /></button></div>}
          <div className="project-sidebar-content">
            {browser.gitError && browser.hasChanges && <p className="project-hint" role="status" title={browser.gitError}>Git refresh delayed. Showing the last successful result. <button className="project-text-button" onClick={browser.refresh}>Retry</button></p>}
            {prefs.view === 'files' ? browser.error ? <div className="project-empty"><p role="alert">{browser.error}</p><button className="project-text-button" onClick={browser.refresh}>Retry</button></div> : browser.loading ? <p className="project-hint"><Loader2 size={14} className="animate-spin" />Loading files…</p> : prefs.changedOnly ? <>
              {browser.gitError && !browser.hasChanges ? <p className="project-inline-error" role="alert">{browser.gitError}</p> : !context || browser.changesLoading ? <p className="project-hint">Checking Git…</p> : !repo ? <p className="project-hint">Select a repository to see changed files.</p> : <>
                <div className="project-root-label" title={repo.root_path || workspace.directory}>{filename(repo.root_path) || workspace.name}</div>
                <ChangedFilesTree key={repo.root_path} changes={changes} root={repo.root_path} query={browser.query} selected={prefs.active} open={open} />
                {browser.changesTruncated && <p className="project-hint">Showing the first 2,000 changes.</p>}
              </>}
            </> : browser.query ? <>
              {browser.searching ? <p className="project-hint">Searching…</p> : browser.searchError ? <p className="project-inline-error">{browser.searchError}</p> : !browser.matches.length ? <p className="project-hint">No matching files</p> : browser.matches.map(entry => <button className="project-search-result" key={entry.path} onClick={() => open(entry.path)}><File size={14} /><span><strong>{entry.name}</strong><small>{entry.path}</small></span></button>)}
              {browser.searchTruncated && !browser.searching && <p className="project-hint">Results limited. Refine your search.</p>}
            </> : <>
              <ProjectTree key={prefs.root} entries={browser.entries} root={prefs.root} expanded={prefs.expanded} onExpanded={expanded => setPrefs(value => ({ ...value, expanded }))} load={browser.loadFolder} open={open} selected={prefs.active} changes={changes} revision={browser.revision} hidden={hidden} uploadTarget={uploads.target} />
              {!browser.entries.length && <p className="project-hint">This folder is empty.</p>}
              {browser.truncated && <p className="project-hint">Showing the first 2,000 entries. Use search for more.</p>}
            </> : prefs.view === 'changes' ? <>
              {browser.gitError && !browser.hasChanges ? <p className="project-inline-error" role="alert">{browser.gitError}</p> : !context || browser.changesLoading ? <p className="project-hint">Checking Git…</p> : !repo ? <div className="project-empty"><GitBranch size={24} /><p>Select a repository to see changes.</p>{browser.repoErrors.map((error, i) => <p key={i} className="project-inline-error">{error.path && `${error.path}: `}{error.message}</p>)}</div> : !changes.length ? <div className="project-empty"><Check size={24} /><strong>Working tree clean</strong><p>No uncommitted changes on {repo.branch ?? 'this checkout'}.</p></div> : groups.filter(group => group.entries.length).map(group => <section key={group.label} className="project-change-group"><h3>{group.label}<span>{group.entries.length}</span></h3>{group.entries.map(change => <button className="project-change-row" key={change.path} onClick={() => open(change.path, group.mode)} title={change.original_path ? `${change.original_path} → ${change.path}` : change.path}><File size={13} /><span><strong data-git-status={gitFileStatus(change, group.mode === 'staged' || group.mode === 'working' ? group.mode : undefined)}>{filename(change.path)}</strong><small>{relativeChangePath(change.path, repo.root_path).split('/').slice(0, -1).join('/') || '.'}</small></span><small className="project-change-kind">{gitStatusLabel(change, group.mode === 'staged' || group.mode === 'working' ? group.mode : undefined)}</small></button>)}</section>)}
              {browser.changesTruncated && <p className="project-hint">Showing the first 2,000 changes.</p>}
            </> : <>
              {context && !repo && !browser.gitError ? <div className="project-empty"><History size={24} /><strong>No Git history</strong><p>This folder isn’t a Git repository.</p></div> : browser.historyLoading ? <p className="project-hint">Loading history…</p> : browser.historyError ? <p className="project-inline-error" role="alert">{browser.historyError}</p> : !browser.history.length ? <p className="project-hint">No commits to show.</p> : browser.history.map(commit => <article className="project-commit" key={commit.id}><GitCommitHorizontal size={15} /><div><strong>{commit.subject}</strong><small>{commit.author} · {new Date(commit.date).toLocaleDateString()}</small><button title="Copy commit hash" onClick={() => { void navigator.clipboard.writeText(commit.id).then(() => setCopied(commit.id)).catch(() => setActionError('Could not copy commit hash')); }}>{copied === commit.id ? 'Copied' : commit.short_id}</button></div></article>)}
              {browser.history.length === 50 && <p className="project-hint">Latest 50 commits on this checkout.</p>}
            </>}
          </div>
          <footer className="project-sidebar-footer"><span>{prefs.view === 'files' ? '⌘P  Find a file' : prefs.view === 'changes' ? 'Working tree & index' : 'Current branch history'}</span>{browser.worktrees.length > 0 && <span title="Registered worktrees"><Layers size={11} />{browser.worktrees.length}</span>}</footer>
        </aside>
        {!narrow && <div className="project-resizer" role="separator" aria-label="Resize file explorer" aria-orientation="vertical" tabIndex={0} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); const next = Math.max(180, Math.min(380, size + (event.key === 'ArrowRight' ? 16 : -16))); setSize(next); localStorage.setItem('project-sidebar-width', String(next)); } }} onPointerDown={event => { const target = event.currentTarget; target.setPointerCapture(event.pointerId); }} onPointerMove={event => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const left = host.current?.getBoundingClientRect().left ?? 0; const next = Math.max(180, Math.min(380, event.clientX - left)); setSize(next); }} onPointerUp={event => { event.currentTarget.releasePointerCapture(event.pointerId); localStorage.setItem('project-sidebar-width', String(size)); }} />}
      </ExplorerSidebar>
      <Menu open={Boolean(tabMenu)} anchorEl={tabMenu?.anchor} onClose={() => setTabMenu(null)} disableScrollLock disableAutoFocus disableEnforceFocus disableRestoreFocus hideBackdrop sx={{ pointerEvents: 'none' }} data-pane-menu slotProps={{ paper: { className: 'project-tab-menu', sx: { pointerEvents: 'auto' } } }}>
        <MenuItem onClick={() => { if (tabMenu) browser.close(tabMenu.path); setTabMenu(null); }}>Close tab</MenuItem>
        <MenuItem disabled={prefs.tabs.length < 2} onClick={() => closeTabs('others')}>Close other tabs</MenuItem>
        <MenuItem disabled={!tabMenu || prefs.tabs.at(-1)?.path === tabMenu.path} onClick={() => closeTabs('right')}>Close tabs to the right</MenuItem>
        <MenuItem onClick={() => closeTabs('all')}>Close all tabs</MenuItem>
        <MenuItem onClick={() => { if (tabMenu) void navigator.clipboard.writeText(tabMenu.path).catch(() => setActionError('Could not copy file path')); setTabMenu(null); }}>Copy file path</MenuItem>
      </Menu>
      <main className="project-preview" data-empty={!activeTab}>
        {!!prefs.tabs.length && <div className="project-file-tabs" role="tablist" aria-label="Open files">{prefs.tabs.map(tab => <div className="project-file-tab" data-active={tab.path === prefs.active} key={tab.path} onMouseDown={event => { if (event.button === 1) event.preventDefault(); }} onAuxClick={event => { if (event.button === 1) { event.preventDefault(); browser.close(tab.path); setTabMenu(null); } }}><button role="tab" aria-selected={tab.path === prefs.active} title={tab.path} onClick={event => { setPrefs(value => ({ ...value, active: tab.path })); const anchor = event.currentTarget; setTabMenu(current => current && current.path !== tab.path ? { path: tab.path, anchor } : null); }} onContextMenu={event => { event.preventDefault(); setTabMenu({ path: tab.path, anchor: event.currentTarget }); }}><File size={12} />{filename(tab.path)}</button><button aria-label={`Close ${filename(tab.path)}`} onClick={() => browser.close(tab.path)}><X size={12} /></button></div>)}</div>}
        {activeTab ? <>
          <div className="project-file-toolbar"><span className="project-breadcrumb" title={filePath}>{filePath.split('/').filter(Boolean).map((part, i) => <span key={i}>{i > 0 && <ChevronRight size={11} />}{part}</span>)}</span><button className="project-icon-button" title="Download file" onClick={() => void download()}><Download size={14} /></button></div>
          <div className="project-comparison"><select aria-label="File view" value={showHtml ? 'html' : activeTab.mode} onChange={event => { setHtmlPreview(event.target.value === 'html'); mode(event.target.value === 'html' ? 'file' : event.target.value as FileTab['mode']); }}>{isHtml && <option value="html">HTML preview</option>}<option value="file">{isHtml ? 'HTML source' : showMedia ? 'Media preview' : 'File content'}</option><option value="working">Unstaged changes</option><option value="staged">Staged changes</option><option value="combined">Compare to revision</option></select>{activeTab.mode === 'combined' && <select aria-label="Comparison revision" value={activeTab.base} onChange={event => setPrefs(value => ({ ...value, tabs: value.tabs.map(tab => tab.path === activeTab.path ? { ...tab, base: event.target.value } : tab) }))}>{[...new Set(['HEAD', ...(browser.fileRepo?.refs ?? []), activeTab.base])].map(ref => <option key={ref} value={ref}>{ref}</option>)}</select>}{activeTab.mode !== 'file' && <button className="project-text-button" onClick={() => setSplit(value => !value)}>{split ? 'Unified diff' : 'Split diff'}</button>}</div>
          <div className="project-file-content" data-html-preview={showHtml || undefined} data-pdf-preview={showMedia || undefined}>
            {showMedia ? <MediaFilePreview key={`${instance}:${workspace.id}:${filePath}:${browser.revision}`} instance={instance} workspaceId={workspace.id} path={filePath} type={browser.preview!.media_type!} onDownload={download} /> : showHtml ? <HtmlFilePreview key={`${instance}:${workspace.id}:${filePath}:${browser.revision}`} instance={instance} workspaceId={workspace.id} path={filePath} /> : browser.fileLoading ? <p className="project-hint"><Loader2 size={15} className="animate-spin" />Loading…</p> : browser.fileError ? <div className="project-empty"><p role="alert">{browser.fileError}</p><button className="project-text-button" onClick={browser.refresh}>Retry</button></div> : activeTab.mode !== 'file' ? browser.diff?.diff ? <DiffViewer diff={browser.diff.diff} viewMode={split && !narrow ? 'split' : 'unified'} /> : <div className="project-empty"><Check size={22} /><p>No differences for this comparison.</p></div> : browser.preview?.binary ? <div className="project-file-state"><File size={28} /><p>This binary file cannot be previewed as text.</p><button className="project-preview-download" onClick={() => void download()}>Download file</button></div> : exceedsPreviewBudget(content) ? <div><p className="project-hint">Large file · showing plain text to keep the viewer responsive.</p><pre className="project-plain-source">{content}</pre></div> : <Markdown className="markdown-workbench project-source" content={`${fence}${language}\n${content}\n${fence}`} />}
            {!showHtml && !showMedia && !browser.preview?.binary && (browser.preview?.truncated || browser.diff?.truncated) && <p className="project-hint">Preview limited to 200 KB. Download the file for its full contents.</p>}
          </div>
        </> : <div className="project-welcome"><header className="project-welcome-heading"><Files size={26} strokeWidth={1.3} /><div><h2>{workspace.name}</h2><p>Open a file or review what changed.</p></div></header><div><button onClick={findFile}><Search size={14} />Find a file<kbd>⌘P</kbd></button><button onClick={() => { setSidebar(true); setPrefs(value => ({ ...value, view: 'changes' })); }}><GitBranch size={14} />Review changes<span>{changes.length}</span></button><button onClick={() => { setSidebar(true); setPrefs(value => ({ ...value, view: 'history' })); }}><History size={14} />Browse history</button></div>{repo && <small><GitBranch size={12} />{repo.branch ?? 'Detached HEAD'}{repo.upstream ? ` → ${repo.upstream}` : ''}</small>}</div>}
      </main>
      </>}
    </div>
  </div>;
}
