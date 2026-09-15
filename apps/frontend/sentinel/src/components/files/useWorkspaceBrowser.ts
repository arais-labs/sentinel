import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../lib/api';
import { browserRequests, pollVisible } from './browserRequests';
import type { Change, Commit, Context, Diff, FileEntry, FilePreview, FileTab, Repo, Worktree } from './types';

type Preferences = { root: string; view: 'files' | 'changes' | 'history'; tabs: FileTab[]; active: string | null; expanded: string[]; changedOnly: boolean; includeGenerated: boolean };
const saved = new Map<string, Preferences>();
export function resetWorkspaceBrowser(instance: string, workspaceId: string) {
  saved.delete(`${instance}:${workspaceId}`);
  browserRequests.invalidate(`/instances/${encodeURIComponent(instance)}/workspaces/${workspaceId}/browse/`);
  window.dispatchEvent(new CustomEvent('sentinel:workspace-folder-changed', { detail: { instance, workspaceId } }));
}
const defaults = (): Preferences => ({ root: '', view: 'files', tabs: [], active: null, expanded: [], changedOnly: false, includeGenerated: false });
const message = (error: unknown) => error instanceof Error ? error.message : 'Could not load workspace';
export function useWorkspaceBrowser(workspaceId: string, instance: string) {
  const key = `${instance}:${workspaceId}`;
  const [prefs, setPrefs] = useState<Preferences>(() => saved.get(key) ?? defaults());
  useEffect(() => { saved.set(key, prefs); }, [key, prefs]);
  const [revision, setRevision] = useState(0);
  const prefix = `/instances/${encodeURIComponent(instance)}/workspaces/${workspaceId}/browse/`;
  const refresh = useCallback(() => { browserRequests.invalidate(prefix); setRevision(value => value + 1); }, [prefix]);
  useEffect(() => {
    const uploaded = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.workspaceId === workspaceId && detail?.instance === instance) refresh();
    };
    window.addEventListener('sentinel:workspace-files-uploaded', uploaded);
    return () => window.removeEventListener('sentinel:workspace-files-uploaded', uploaded);
  }, [workspaceId, instance, refresh]);
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent).detail?.workspaceId !== workspaceId || (event as CustomEvent).detail?.instance !== instance) return;
      saved.delete(key); setPrefs(defaults()); refresh();
    };
    window.addEventListener('sentinel:workspace-folder-changed', changed);
    return () => window.removeEventListener('sentinel:workspace-folder-changed', changed);
  }, [workspaceId, instance, key, refresh]);
  const request = useCallback(<T,>(operation: string, params: Record<string, string> = {}) => {
    const path = `${prefix}${operation}?${new URLSearchParams(Object.entries(params).sort())}`;
    return browserRequests.get(path, () => api.get<T>(path), operation === 'context' || operation === 'repositories' ? 30000 : 3000);
  }, [prefix]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
  const [repoErrors, setRepoErrors] = useState<{ path: string; message: string }[]>([]);
  const [context, setContext] = useState<Context | null>(null);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [changesLoading, setChangesLoading] = useState(true);
  const [truncated, setTruncated] = useState(false);
  const [changesTruncated, setChangesTruncated] = useState(false);
  const [history, setHistory] = useState<Commit[]>([]);
  const [error, setError] = useState('');
  const [needsStart, setNeedsStart] = useState(false);
  const [gitError, setGitError] = useState('');
  const [hasChanges, setHasChanges] = useState(false);
  const inspectedRoot = useRef<string | null>(null);
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [worktreesLoading, setWorktreesLoading] = useState(false);
  const [worktreesError, setWorktreesError] = useState('');
  const worktreeGeneration = useRef(0);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<FileEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [searchTruncated, setSearchTruncated] = useState(false);

  useEffect(() => {
    let active = true;
    setReposLoading(true);
    request<{ roots: Repo[]; errors: typeof repoErrors }>('repositories', { include_generated: String(prefs.includeGenerated) }).then(value => {
      if (active) { setRepos(value.roots); setRepoErrors(value.errors ?? []); }
    }).catch(reason => { if (active) setRepoErrors([{ path: '', message: message(reason) }]); })
      .finally(() => { if (active) setReposLoading(false); });
    return () => { active = false; };
  }, [request, revision, prefs.includeGenerated]);
  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout>;
    setLoading(true); setError(''); setEntries([]);
    request<{ entries: FileEntry[]; truncated: boolean }>('files', { path: prefs.root, limit: '2000' }).then(value => {
      if (active) { setNeedsStart(false); setEntries(value.entries); setTruncated(value.truncated); }
    }).catch(reason => { if (active) { setError(message(reason)); setNeedsStart(reason instanceof ApiError && reason.status === 409); if (reason instanceof ApiError && reason.status === 409) retry = setTimeout(refresh, 5000); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; clearTimeout(retry); };
  }, [request, prefs.root, revision, refresh]);
  useEffect(() => {
    let active = true;
    let repositoryContext: Context | null = null;
    const rootKey = `${key}:${prefs.root}`;
    if (inspectedRoot.current !== rootKey) {
      inspectedRoot.current = rootKey;
      setContext(null); setChanges([]); setChangesTruncated(false); setHasChanges(false); setChangesLoading(true);
    }
    setGitError(''); setWorktrees([]); setWorktreesError(''); setWorktreesLoading(false);
    const stop = pollVisible(async () => {
      try {
        // Repository identity is stable within this selection. Worktree discovery
        // is requested separately when the picker opens, never during polling.
        if (!repositoryContext?.repository) repositoryContext = await request<Context>('context', { path: prefs.root, include_worktrees: 'false' });
        if (!active) return true;
        setContext(repositoryContext);
        if (repositoryContext.repository) {
          const result = await request<{ entries: Change[]; truncated: boolean; branch: string | null; detached_head: boolean }>('changes', { path: repositoryContext.repository.root_path, limit: '2000' });
          if (!active) return true;
          repositoryContext = { ...repositoryContext, repository: { ...repositoryContext.repository, branch: result.branch, detached_head: result.detached_head } };
          setContext(repositoryContext);
          setChanges(result.entries); setChangesTruncated(result.truncated);
        } else { setChanges([]); setChangesTruncated(false); }
        setHasChanges(true); setGitError('');
        return true;
      } catch (reason) { repositoryContext = null; if (active) setGitError(message(reason)); return false; }
      finally { if (active) setChangesLoading(false); }
    }, document);
    return () => { active = false; stop(); worktreeGeneration.current++; };
  }, [request, key, prefs.root, revision]);
  const loadWorktrees = useCallback(async () => {
    const generation = ++worktreeGeneration.current;
    setWorktreesLoading(true); setWorktreesError('');
    try {
      const value = await request<Context>('context', { path: prefs.root });
      if (generation === worktreeGeneration.current) setWorktrees(value.worktrees);
    } catch (reason) { if (generation === worktreeGeneration.current) setWorktreesError(message(reason)); }
    finally { if (generation === worktreeGeneration.current) setWorktreesLoading(false); }
  }, [request, prefs.root]);
  useEffect(() => {
    if (prefs.view !== 'history') return;
    let active = true;
    setHistory([]); setHistoryError(''); setHistoryLoading(true);
    request<{ commits: Commit[] }>('history', { path: prefs.root, limit: '50' }).then(value => { if (active) setHistory(value.commits); })
      .catch(reason => { if (active) setHistoryError(message(reason)); }).finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [request, prefs.view, prefs.root, revision]);
  useEffect(() => {
    if (prefs.changedOnly || !query.trim()) { setMatches([]); setSearching(false); setSearchError(''); return; }
    let active = true;
    setSearching(true); setSearchError('');
    const timer = setTimeout(() => {
      request<{ entries: FileEntry[]; truncated: boolean }>('search', { path: prefs.root, query, limit: '100' }).then(value => {
        if (active) { setMatches(value.entries); setSearchTruncated(value.truncated); }
      }).catch(reason => { if (active) setSearchError(message(reason)); }).finally(() => { if (active) setSearching(false); });
    }, 220);
    return () => { active = false; clearTimeout(timer); };
  }, [request, prefs.root, query, revision, prefs.changedOnly]);

  const loadFolder = useCallback(async (path: string) => {
    const value = await request<{ entries: FileEntry[]; truncated: boolean }>('files', { path, limit: '2000' });
    if (value.truncated) throw new Error('This folder has more than 2,000 entries. Use file search to narrow it down.');
    return value.entries;
  }, [request]);
  function selectRoot(root: string) { setQuery(''); setPrefs(value => ({ ...value, root, expanded: [] })); }
  function open(path: string, mode: FileTab['mode'] = 'file') {
    setPrefs(value => ({ ...value, active: path, tabs: value.tabs.some(tab => tab.path === path) ? value.tabs.map(tab => tab.path === path ? { path, mode, base: mode === 'working' ? '' : 'HEAD' } : tab) : [...value.tabs, { path, mode, base: mode === 'working' ? '' : 'HEAD' }] }));
  }
  const activeTab = prefs.tabs.find(tab => tab.path === prefs.active) ?? null;
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [fileRepo, setFileRepo] = useState<Repo | null>(null);
  const [fileError, setFileError] = useState('');
  const [fileLoading, setFileLoading] = useState(false);
  const fileGeneration = useRef(0);
  useEffect(() => {
    const generation = ++fileGeneration.current;
    setPreview(null); setDiff(null); setFileError(''); setFileRepo(null);
    if (!activeTab) { setFileLoading(false); return; }
    setFileLoading(true);
    const params = { path: activeTab.path, base_ref: activeTab.base, staged: String(activeTab.mode === 'staged') };
    const pending = activeTab.mode === 'file'
      ? request<FilePreview>('file', params).then(value => { if (generation === fileGeneration.current) setPreview(value); })
      : request<Diff>('diff', params).then(value => { if (generation === fileGeneration.current) { setDiff(value); setFileRepo(value.repository ?? null); } });
    pending.catch(reason => { if (generation === fileGeneration.current) setFileError(message(reason)); })
      .finally(() => { if (generation === fileGeneration.current) setFileLoading(false); });
    return () => { fileGeneration.current++; };
  }, [request, activeTab?.path, activeTab?.mode, activeTab?.base, revision]);
  const close = (path: string) => setPrefs(value => {
    const tabs = value.tabs.filter(tab => tab.path !== path);
    return { ...value, tabs, active: value.active === path ? tabs.at(-1)?.path ?? null : value.active };
  });
  return { prefs, setPrefs, revision, refresh, repos, reposLoading, repoErrors, context, entries, changes, changesLoading, truncated, changesTruncated,
    history, historyError, historyLoading, loading, needsStart, error, gitError, hasChanges, worktrees, worktreesLoading, worktreesError, loadWorktrees, query, setQuery, matches, searching, searchError, searchTruncated,
    request, loadFolder, selectRoot, open, close, activeTab, preview, diff, fileRepo, fileError, fileLoading };
}
