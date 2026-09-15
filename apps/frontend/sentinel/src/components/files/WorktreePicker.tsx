import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Popover } from '@mui/material';
import { ArrowDownWideNarrow, Check, ChevronDown, GitBranch, GitCommitHorizontal, HelpCircle, LockKeyhole, Search, X } from 'lucide-react';
import type { Repo, Worktree } from './types';
import { parseWorktreeQuery, searchWorktrees, type WorktreeAvailability, type WorktreeKind, type WorktreeSort } from './worktreeSearch';

type Preferences = { sort: WorktreeSort; kind: WorktreeKind; availability: WorktreeAvailability; visited: Record<string, number> };
const defaults: Preferences = { sort: 'smart', kind: 'all', availability: 'all', visited: {} };
function loadPreferences(key: string): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? '{}');
    return {
      sort: ['smart', 'recent', 'branch', 'folder', 'visited'].includes(saved.sort) ? saved.sort : 'smart',
      kind: ['all', 'branch', 'detached'].includes(saved.kind) ? saved.kind : 'all',
      availability: ['all', 'available', 'unavailable'].includes(saved.availability) ? saved.availability : 'all',
      visited: saved.visited && typeof saved.visited === 'object' ? saved.visited : {},
    };
  } catch { return defaults; }
}
function commitDate(seconds: number) {
  const date = new Date(seconds * 1000);
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(date.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } as const : {}) });
}

function Highlight({ text, query }: { text: string; query: string }) {
  const terms = parseWorktreeQuery(query).terms.filter(term => !term.exclude && term.field !== 'is').map(term => term.value);
  const ranges: [number, number][] = [];
  for (const term of terms) {
    let start = text.toLowerCase().indexOf(term);
    while (start >= 0 && term.length) {
      ranges.push([start, start + term.length]);
      start = text.toLowerCase().indexOf(term, start + term.length);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const range of ranges) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  let cursor = 0;
  const parts = merged.map(([start, end]) => {
    const before = text.slice(cursor, start); cursor = end;
    return <span key={start}>{before}<mark>{text.slice(start, end)}</mark></span>;
  });
  return <>{parts}{text.slice(cursor)}</>;
}

export function WorktreePicker({ repository, worktrees, storageKey, onSelect, onOpen, loading, loadError }: {
  repository: Repo; worktrees: Worktree[]; storageKey: string; onSelect: (path: string) => void; onOpen: () => void; loading: boolean; loadError: string;
}) {
  const key = `worktree-picker:${storageKey}`;
  const [prefs, setPrefs] = useState<Preferences>(() => loadPreferences(key));
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState('');
  const [help, setHelp] = useState(false);
  const [activePath, setActivePath] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = Boolean(anchor);
  const { matches, error } = useMemo(() => searchWorktrees(worktrees, { ...prefs, query }), [worktrees, prefs, query]);
  const selectedIndex = matches.findIndex(match => match.tree.path === activePath);
  const active = selectedIndex >= 0 ? selectedIndex : Math.max(0, matches.findIndex(match => match.tree.available));
  const label = repository.branch ?? `Detached · ${worktrees.find(tree => tree.current)?.head?.slice(0, 7) ?? 'HEAD'}`;
  const hasFilters = prefs.kind !== 'all' || prefs.availability !== 'all';
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(prefs)); } catch { /* Preferences are optional. */ } }, [key, prefs]);
  useEffect(() => { setActivePath(null); }, [query, prefs.sort, prefs.kind, prefs.availability]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);
  useEffect(() => {
    if (open) { const frame = requestAnimationFrame(() => input.current?.focus()); return () => cancelAnimationFrame(frame); }
  }, [open]);

  function choose(tree: Worktree) {
    if (!tree.available) return;
    setPrefs(value => ({ ...value, visited: { ...value.visited, [tree.path]: Date.now() } }));
    setAnchor(null); onSelect(tree.path);
  }
  function move(direction: number) {
    for (let step = 1; step <= matches.length; step++) {
      const next = (active + direction * step + matches.length) % matches.length;
      if (matches[next].tree.available) { setActivePath(matches[next].tree.path); return; }
    }
  }
  function reset() { setQuery(''); setPrefs(value => ({ ...value, kind: 'all', availability: 'all' })); input.current?.focus(); }

  return <>
    <button className="chat-header-pill project-worktree-trigger" aria-label={`Browse branches and worktrees: ${label}`} aria-haspopup="dialog" aria-expanded={open} onClick={event => { setAnchor(event.currentTarget); onOpen(); }}>
      <GitBranch size={13} /><span>{label}</span><ChevronDown size={12} />
    </button>
    <Popover data-pane-menu disableScrollLock sx={{ zIndex: 10000 }} open={open} anchorEl={anchor} onClose={() => setAnchor(null)}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }} transformOrigin={{ vertical: 'top', horizontal: 'left' }}
      marginThreshold={12} slotProps={{ paper: { className: 'worktree-picker', role: 'dialog', 'aria-label': 'Browse branches and worktrees' } }}>
      <header className="worktree-picker-heading"><strong>Branches & worktrees</strong><span>{worktrees.length}</span><button aria-label="Search tips" aria-expanded={help} onClick={() => setHelp(value => !value)}><HelpCircle size={15} /></button></header>
      <div className="worktree-picker-search"><Search size={17} /><input ref={input} autoFocus maxLength={240} value={query} onChange={event => setQuery(event.target.value)}
        role="combobox" aria-label="Search branches, folders, or commits" aria-autocomplete="list" aria-expanded="true" aria-controls={`${id}-results`}
        aria-activedescendant={matches[active] ? `${id}-${active}` : undefined} placeholder="Search branch, folder, or commit…"
        onKeyDown={event => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); move(event.key === 'ArrowDown' ? 1 : -1); }
          if (event.key === 'Enter' && matches[active]) { event.preventDefault(); choose(matches[active].tree); }
          if (event.key === 'Escape') { event.stopPropagation(); setAnchor(null); }
        }} />{query && <button aria-label="Clear search" onClick={() => { setQuery(''); input.current?.focus(); }}><X size={15} /></button>}</div>
      <div className="worktree-picker-controls">
        <select aria-label="Filter worktree type" value={prefs.kind} onChange={event => setPrefs(value => ({ ...value, kind: event.target.value as WorktreeKind }))}>
          <option value="all">All checkouts</option><option value="branch">Named branches</option><option value="detached">Detached worktrees</option>
        </select>
        <select aria-label="Filter availability" value={prefs.availability} onChange={event => setPrefs(value => ({ ...value, availability: event.target.value as WorktreeAvailability }))}>
          <option value="all">Any availability</option><option value="available">Available</option><option value="unavailable">Unavailable</option>
        </select>
        <label className="worktree-picker-sort"><ArrowDownWideNarrow size={13} /><select aria-label="Sort worktrees" value={prefs.sort} onChange={event => setPrefs(value => ({ ...value, sort: event.target.value as WorktreeSort }))}>
          <option value="smart">Recommended</option><option value="recent">Recent commits</option>
          <option value="branch">Branch A–Z</option><option value="folder">Folder A–Z</option><option value="visited">Recently viewed</option>
        </select></label>
      </div>
      {hasFilters && <div className="worktree-picker-active-filters">
        {prefs.kind !== 'all' && <button onClick={() => setPrefs(value => ({ ...value, kind: 'all' }))}>{prefs.kind === 'branch' ? 'Named branches' : 'Detached worktrees'}<X size={11} /></button>}
        {prefs.availability !== 'all' && <button onClick={() => setPrefs(value => ({ ...value, availability: 'all' }))}>{prefs.availability === 'available' ? 'Available' : 'Unavailable'}<X size={11} /></button>}
      </div>}
      {help && <div className="worktree-picker-help"><p>Words can appear in any order. Partial names, initials, and small typos work too.</p><p>Use quotes for an exact phrase, or <code>-test</code> to exclude a term.</p><div>{['branch:fix', 'path:broker', 'is:detached', 'is:locked'].map(example => <button key={example} onClick={() => { setQuery(value => `${value} ${example}`.trim()); input.current?.focus(); }}>{example}</button>)}</div></div>}
      <div ref={list} className="worktree-picker-results" id={`${id}-results`} role="listbox" aria-label="Matching worktrees">
        {loading && <p className="project-hint" role="status">Loading worktrees…</p>}
        {loadError && <p className="project-inline-error" role="alert">{loadError} <button onClick={onOpen}>Retry</button></p>}
        {error ? <p className="worktree-picker-empty" role="alert">{error}</p> : !matches.length && (loading || loadError) ? null : !matches.length ? <div className="worktree-picker-empty"><Search size={22} /><strong>No matching worktrees</strong><span>Try fewer words or clear the filters.</span><button onClick={reset}>Clear search & filters</button></div> : matches.map(({ tree, approximate }, index) => {
          const folder = tree.path || repository.root_path || 'Project root';
          const title = tree.branch ?? folder.split('/').at(-1) ?? 'Detached worktree';
          return <button key={tree.path} id={`${id}-${index}`} data-index={index} data-active={active === index} className="worktree-picker-result" role="option" aria-selected={active === index} aria-disabled={!tree.available}
            onMouseMove={() => setActivePath(tree.path)} onClick={() => choose(tree)} title={tree.available ? folder : `Unavailable inside this workspace: ${folder}`}>
            <span className="worktree-picker-result-icon">{tree.branch ? <GitBranch size={16} /> : <GitCommitHorizontal size={16} />}</span>
            <span className="worktree-picker-result-text"><strong><Highlight text={title} query={query} /></strong><small><Highlight text={folder} query={query} /></small>
              <span className="worktree-picker-result-meta">{!tree.branch && <span>Detached</span>}{tree.head && <code><Highlight text={tree.head.slice(0, 7)} query={query} /></code>}{tree.last_commit_at && <span title={`Last commit: ${new Date(tree.last_commit_at * 1000).toLocaleString()}`}>{commitDate(tree.last_commit_at)}</span>}{tree.locked && <span><LockKeyhole size={10} />Locked</span>}{!tree.available && <span>Unavailable</span>}{approximate && <span className="worktree-picker-close-match">Close match</span>}</span>
            </span>
            {tree.current && <span className="worktree-picker-current"><Check size={12} />Viewing</span>}
          </button>;
        })}
      </div>
      <footer className="worktree-picker-footer"><span aria-live="polite">{matches.length} of {worktrees.length} worktrees</span><span><kbd>↑↓</kbd> navigate <kbd>↵</kbd> open <kbd>esc</kbd> close</span></footer>
    </Popover>
  </>;
}
