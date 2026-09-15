import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, File, Folder, FolderOpen, Loader2 } from 'lucide-react';
import { gitFileStatus, gitStatusLabel } from './gitStatus';
import type { Change, FileEntry } from './types';

export function ProjectTree({ entries, root, expanded, onExpanded, load, open, selected, changes, revision, hidden, uploadTarget }: {
  entries: FileEntry[]; root: string; expanded: string[]; onExpanded: (paths: string[]) => void;
  load: (path: string) => Promise<FileEntry[]>; open: (path: string) => void; selected: string | null;
  changes: Change[]; revision: number; hidden: boolean;
  uploadTarget?: string | null;
}) {
  const [cache, setCache] = useState<Record<string, FileEntry[]>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    let active = true;
    setCache({}); setErrors({}); setLoading({});
    for (const path of expanded) {
      setLoading(value => ({ ...value, [path]: true }));
      load(path).then(value => { if (active) setCache(state => ({ ...state, [path]: value })); })
        .catch(reason => { if (active) setErrors(state => ({ ...state, [path]: String(reason.message || reason) })); })
        .finally(() => { if (active) setLoading(state => ({ ...state, [path]: false })); });
    }
    return () => { active = false; };
    // Expanded folders refresh only on explicit refresh/root changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root, revision, load]);
  async function toggle(entry: FileEntry) {
    if (expanded.includes(entry.path)) { onExpanded(expanded.filter(path => path !== entry.path)); return; }
    onExpanded([...expanded, entry.path]);
    if (cache[entry.path] || loading[entry.path]) return;
    setLoading(value => ({ ...value, [entry.path]: true }));
    try { const children = await load(entry.path); setCache(value => ({ ...value, [entry.path]: children })); }
    catch (reason) { setErrors(value => ({ ...value, [entry.path]: reason instanceof Error ? reason.message : 'Could not open folder' })); }
    finally { setLoading(value => ({ ...value, [entry.path]: false })); }
  }
  const visible = (items: FileEntry[]) => items.filter(item => item.name !== '.git' && (hidden || !item.name.startsWith('.')));
  function render(items: FileEntry[], depth = 0) {
    return visible(items).map(entry => {
      const folder = entry.kind === 'directory';
      const isOpen = expanded.includes(entry.path);
      const change = changes.find(item => item.path === entry.path);
      const dirty = folder && changes.some(item => item.path.startsWith(`${entry.path}/`));
      const status = gitFileStatus(change);
      return <div key={entry.path} role="none">
        <button role="treeitem" aria-expanded={folder ? isOpen : undefined} aria-selected={selected === entry.path}
          data-upload-directory={folder ? entry.path : undefined} data-upload-target={folder && uploadTarget === entry.path || undefined}
          className={`project-tree-row${selected === entry.path ? ' selected' : ''}`} title={`${entry.path}${change ? ` · ${gitStatusLabel(change)}` : ''}`} aria-label={`${entry.name}${change ? `, ${gitStatusLabel(change)}` : ''}`}
          style={{ paddingLeft: 16 + depth * 12 }}
          onClick={() => folder ? void toggle(entry) : open(entry.path)}
          onKeyDown={event => {
            if (event.key === 'ArrowRight' && folder && !isOpen || event.key === 'ArrowLeft' && folder && isOpen) { event.preventDefault(); void toggle(entry); }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              const rows = [...(event.currentTarget.closest('[role="tree"]')?.querySelectorAll<HTMLButtonElement>('[role="treeitem"]') ?? [])];
              rows[rows.indexOf(event.currentTarget) + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
            }
          }}>
          <span className="project-tree-chevron">{folder && (loading[entry.path] ? <Loader2 size={12} className="animate-spin" /> : isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}</span>
          {folder ? isOpen ? <FolderOpen size={14} /> : <Folder size={14} /> : <File size={13} />}
          <span className="project-tree-name" data-git-status={status ?? (dirty ? 'descendants' : undefined)}>{entry.name}</span>
        </button>
        {folder && isOpen && <div role="group">
          {errors[entry.path] ? <p className="project-inline-error">{errors[entry.path]}</p> : cache[entry.path] ? visible(cache[entry.path]).length ? render(cache[entry.path], depth + 1) : <p className="project-tree-empty" style={{ paddingLeft: 42 + depth * 12 }}>Empty folder</p> : null}
        </div>}
      </div>;
    });
  }
  return <div role="tree" aria-label="Project files">{render(entries)}</div>;
}
