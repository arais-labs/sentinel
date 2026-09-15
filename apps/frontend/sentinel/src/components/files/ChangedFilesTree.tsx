import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, File, Folder, FolderOpen } from 'lucide-react';
import { buildChangeTree, changeMode, type ChangeNode } from './changeTree';
import { gitFileStatus, gitStatusLabel } from './gitStatus';
import type { Change, FileTab } from './types';

export function ChangedFilesTree({ changes, root, query, selected, open }: {
  changes: Change[]; root: string; query: string; selected: string | null; open: (path: string, mode: FileTab['mode']) => void;
}) {
  const nodes = useMemo(() => buildChangeTree(changes, root, query), [changes, root, query]);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const toggle = (path: string) => setCollapsed(value => {
    const next = new Set(value); if (next.has(path)) next.delete(path); else next.add(path); return next;
  });
  function render(nodes: ChangeNode[], depth = 0): React.ReactNode {
    return nodes.map(node => {
      const change = node.change;
      const expanded = !collapsed.has(node.path);
      const status = gitFileStatus(change);
      const state = change ? change.conflicted ? 'Conflict' : change.untracked ? 'Unversioned' : change.staged && change.unstaged ? 'Staged and unstaged' : change.staged ? 'Staged' : 'Unstaged' : `${node.count} changed files`;
      return <div key={node.path} role="none">
        <button role="treeitem" aria-expanded={!change ? expanded : undefined} aria-selected={selected === node.path} aria-label={`${node.name}, ${gitStatusLabel(change)}, ${state}`}
          className={`project-tree-row${selected === node.path ? ' selected' : ''}`} style={{ paddingLeft: 8 + depth * 12 }}
          title={`${change?.original_path ? `${change.original_path} → ` : ''}${node.path}\n${gitStatusLabel(change)} · ${state}`}
          onClick={() => change ? open(change.path, changeMode(change)) : toggle(node.path)}
          onKeyDown={event => {
            if (!change && (event.key === 'ArrowRight' && !expanded || event.key === 'ArrowLeft' && expanded)) { event.preventDefault(); toggle(node.path); }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              const rows = [...(event.currentTarget.closest('[role="tree"]')?.querySelectorAll<HTMLButtonElement>('[role="treeitem"]') ?? [])];
              rows[rows.indexOf(event.currentTarget) + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
            }
          }}>
          <span className="project-tree-chevron">{!change && (expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}</span>
          {change ? <File size={13} /> : expanded ? <FolderOpen size={14} /> : <Folder size={14} />}
          <span className="project-tree-name" data-git-status={status ?? 'descendants'}>{node.name}</span>
          {change ? <span className="project-change-state">{change.conflicted || change.untracked ? '' : change.staged && change.unstaged ? 'Partial' : change.staged ? 'Staged' : ''}</span> : <span className="project-folder-count">{node.count}</span>}
        </button>
        {!change && expanded && <div role="group">{render(node.children, depth + 1)}</div>}
      </div>;
    });
  }
  return nodes.length ? <div role="tree" aria-label="Changed files by folder">{render(nodes)}</div> : <p className="project-hint">{query.trim() ? 'No changed files match your search.' : 'No uncommitted changes.'}</p>;
}
