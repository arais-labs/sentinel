import { useState } from 'react';
import { Popover } from '@mui/material';
import { Check, ChevronDown, Folder, GitBranch, Search } from 'lucide-react';
import { filename, type Repo } from './types';

export function RepositoryPicker({ repositories, current, root, workspaceName, includeGenerated, onGenerated, onSelect, loading, errors, onRetry }: {
  repositories: Repo[]; current: Repo | null | undefined; root: string; workspaceName: string; includeGenerated: boolean;
  loading: boolean; errors: { path: string; message: string }[]; onRetry: () => void;
  onGenerated: (value: boolean) => void; onSelect: (path: string) => void;
}) {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [query, setQuery] = useState('');
  const selected = repositories.find(repo => current?.common_dir ? repo.common_dir === current.common_dir : repo.root_path === root);
  const label = selected ? filename(selected.root_path) || workspaceName : current ? filename(current.root_path) || workspaceName : 'Project files';
  const filtered = repositories.filter(repo => `${repo.root_path || workspaceName}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()));
  const choose = (path: string) => { onSelect(path); setAnchor(null); };
  return <>
    <button className="chat-header-pill project-worktree-trigger project-repository-trigger" aria-label={`Repository: ${label}`} aria-haspopup="dialog" aria-expanded={Boolean(anchor)} onClick={event => setAnchor(event.currentTarget)}>
      {current ? <GitBranch size={13} /> : <Folder size={13} />}<span>{label}</span><ChevronDown size={12} />
    </button>
    <Popover data-pane-menu disableScrollLock sx={{ zIndex: 10000 }} open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }} transformOrigin={{ vertical: 'top', horizontal: 'left' }}
      marginThreshold={12} slotProps={{ paper: { className: 'worktree-picker project-repository-menu', role: 'dialog', 'aria-label': 'Choose repository' } }}>
      <header className="worktree-picker-heading"><strong>Repositories</strong><span>{repositories.length}</span></header>
      <div className="worktree-picker-search"><Search size={16} /><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a repository…" aria-label="Find a repository" /></div>
      <div className="worktree-picker-results">
        {!query.trim() && !repositories.some(repo => repo.root_path === '') && <button className="worktree-picker-result" onClick={() => choose('')}><Folder size={16} /><span className="worktree-picker-result-text"><strong>Project files</strong><small>Browse all of {workspaceName}</small></span>{!root && <Check size={13} />}</button>}
        {filtered.map(repo => <button className="worktree-picker-result" key={repo.root_path} onClick={() => choose(repo.root_path)}><GitBranch size={16} /><span className="worktree-picker-result-text"><strong>{filename(repo.root_path) || workspaceName}</strong><small>{repo.root_path || 'Project root'}</small></span>{selected === repo && <Check size={13} />}</button>)}
        {!loading && !errors.length && !filtered.length && <p className="worktree-picker-empty">{query ? 'No matching repositories.' : 'No repositories found.'}</p>}
      </div>
      {loading && <p className="project-repository-notice" role="status">Finding repositories…</p>}
      {!loading && errors.length > 0 && <div className="project-repository-notice" role="alert">{errors.map((error, index) => <p key={index}>{error.path ? `${error.path}: ` : ''}{error.message}</p>)}<button onClick={onRetry}>Retry discovery</button></div>}
      <label className="project-repository-generated"><input type="checkbox" checked={includeGenerated} onChange={event => onGenerated(event.target.checked)} />Include generated dependencies</label>
    </Popover>
  </>;
}
