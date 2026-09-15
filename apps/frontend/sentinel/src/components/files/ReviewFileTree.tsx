import { ChevronDown, ChevronRight, Folder, File, Check } from 'lucide-react';
import { usePaneState } from './usePaneState';
type ChangedFile = { filename: string; sha: string; status: string; additions: number; deletions: number };
type Node = { name: string; path: string; children: Map<string, Node>; file?: ChangedFile };
export function ReviewFileTree({ files, tree, selected, viewed, onSelect, storageKey }: { files: ChangedFile[]; tree: boolean; selected: string; viewed: string[]; onSelect: (path: string) => void; storageKey: string }) {
  const [closed, setClosed] = usePaneState<string[]>(storageKey, []);
  const root: Node = { name: '', path: '', children: new Map() };
  for (const file of files) {
    const parts = file.filename.split('/'); let node = root;
    parts.forEach((name, index) => { const path = parts.slice(0, index + 1).join('/'); if (!node.children.has(name)) node.children.set(name, { name, path, children: new Map() }); node = node.children.get(name)!; }); node.file = file;
  }
  const row = (file: ChangedFile, depth: number) => <button key={file.filename} className={`pr-tree-file ${selected === file.filename ? 'selected' : ''}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => onSelect(file.filename)} data-status={file.status} title={`${file.filename} · ${file.status}`} aria-current={selected === file.filename ? 'true' : undefined}>
    {viewed.includes(`${file.filename}:${file.sha}`) ? <Check size={13} /> : <File size={13} />}<span><strong>{file.filename.split('/').pop()}</strong>{!tree && <small>{file.filename.split('/').slice(0, -1).join('/') || 'Repository root'}</small>}</span><span className={`pr-status ${file.status}`}>{file.status === 'added' ? 'A' : file.status === 'removed' ? 'D' : file.status === 'renamed' ? 'R' : 'M'}</span><small className="pr-file-count">+{file.additions} −{file.deletions}</small>
  </button>;
  const render = (node: Node, depth: number): React.ReactNode => [...node.children.values()].sort((a,b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name)).map(child => child.file ? row(child.file, depth) : <div key={child.path}><button className="pr-tree-folder" style={{ paddingLeft: 8 + depth * 14 }} aria-expanded={!closed.includes(child.path)} onClick={() => setClosed(value => value.includes(child.path) ? value.filter(p => p !== child.path) : [...value, child.path])}>{closed.includes(child.path) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}<Folder size={13} /><span>{child.name}</span></button>{!closed.includes(child.path) && render(child, depth + 1)}</div>);
  return <div className="pr-tree">{tree ? render(root, 0) : files.map(file => row(file, 0))}</div>;
}
