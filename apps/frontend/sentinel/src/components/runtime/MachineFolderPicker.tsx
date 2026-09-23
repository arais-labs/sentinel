import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, ArrowRight, ArrowUp, Check, ChevronRight, Clock, FolderPlus, Folder, Home, Loader2, Pencil, Search, X } from 'lucide-react';
import { api } from '../../lib/api';
import { projectDirectoryError } from './workspaceValidation';
import './machine-folder-picker.css';

type DirectoryListing = { path: string; parent: string; directories: string[] };
const join = (parent: string, name: string) => `${parent === '/' ? '' : parent}/${name}`;
const basename = (path: string) => path.split('/').filter(Boolean).at(-1) || 'Root';

export function MachineFolderPicker({ machineId, machineName, initialPath, onSelect, onClose }: {
  machineId: string; machineName: string; initialPath: string;
  onSelect: (path: string) => void; onClose: () => void;
}) {
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [home, setHome] = useState<DirectoryListing | null>(null);
  const [path, setPath] = useState(initialPath);
  const [editingPath, setEditingPath] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [position, setPosition] = useState(-1);
  const [recent, setRecent] = useState<string[]>([]);
  const [newFolder, setNewFolder] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [createError, setCreateError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const request = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const pathInput = useRef<HTMLInputElement>(null);
  const typeAhead = useRef({ text: '', time: 0 });
  const destination = selected ?? listing?.path;
  const selectionError = destination ? projectDirectoryError(destination) : null;
  const blocked = loading || creatingFolder;
  const rows = listing?.directories.filter(name => name.toLowerCase().includes(search.toLowerCase())) ?? [];
  const endpoint = `/machines/${machineId}/directories`;

  async function open(directory: string, historyPosition?: number) {
    const id = ++request.current;
    setLoading(true); setError('');
    try {
      const result = await api.get<DirectoryListing>(`${endpoint}?path=${encodeURIComponent(directory)}`);
      if (id !== request.current) return false;
      setListing(result); setPath(result.path); setSelected(null); setSearch('');
      setEditingPath(false); setNewFolder(false); setCreateError('');
      if (historyPosition != null) setPosition(historyPosition);
      else if (history[position] !== result.path) {
        const next = [...history.slice(0, position + 1), result.path];
        setHistory(next); setPosition(next.length - 1);
      }
      setRecent(current => [result.path, ...current.filter(item => item !== result.path)].slice(0, 6));
      return true;
    } catch (reason) {
      if (id === request.current) setError(reason instanceof Error ? reason.message : 'Could not open folder');
      return false;
    } finally { if (id === request.current) setLoading(false); }
  }

  async function createFolder() {
    if (!listing || creatingFolder || !folderName.trim()) return;
    if (folderName === '.' || folderName === '..' || /[/\\\\\x00-\x1f]/.test(folderName)) {
      setCreateError('Enter a folder name without slashes or control characters.'); return;
    }
    const parent = listing.path;
    const id = ++request.current;
    setCreatingFolder(true); setCreateError('');
    try {
      const result = await api.post<DirectoryListing>(endpoint, { path: parent, name: folderName });
      if (id !== request.current) return;
      if (await open(parent)) { setSelected(result.path); setFolderName(''); }
    } catch (reason) {
      if (id === request.current) setCreateError(reason instanceof Error ? reason.message : 'Could not create folder');
    } finally { setCreatingFolder(false); }
  }

  useEffect(() => {
    let active = true;
    void open(initialPath);
    // Resolve locations on the selected machine, never on the browser's host.
    api.get<DirectoryListing>(`${endpoint}?path=`).then(value => { if (active) setHome(value); }).catch(() => {});
    return () => { active = false; request.current++; };
  }, [machineId]); // Picker is mounted fresh for each browse action.
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => { if (editingPath) { pathInput.current?.focus(); pathInput.current?.select(); } }, [editingPath]);

  function navigateKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (blocked || event.target instanceof HTMLInputElement) return;
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[data-folder-row]')];
    if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (event.key === 'ArrowDown') next = Math.min(buttons.length - 1, index + 1);
    else if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = buttons.length - 1;
    else if (event.key === 'Enter' && index >= 0 && listing) {
      event.preventDefault(); void open(join(listing.path, rows[index])); return;
    } else if (event.key.length === 1 && event.key !== ' ' && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const now = Date.now();
      const text = (now - typeAhead.current.time < 800 ? typeAhead.current.text : '') + event.key.toLowerCase();
      typeAhead.current = { text, time: now };
      next = rows.findIndex(name => name.toLowerCase().startsWith(text));
    }
    if (next >= 0) { event.preventDefault(); buttons[next].focus(); }
  }

  const inHome = home && listing && (listing.path === home.path || listing.path.startsWith(`${home.path}/`));
  const base = inHome ? home.path : '/';
  const segments = listing?.path.slice(inHome ? home.path.length : 0).split('/').filter(Boolean) ?? [];
  const places = home ? ['Desktop', 'Documents', 'Downloads', 'Projects'].filter(name => home.directories.includes(name)) : [];
  const recentPlaces = recent.filter(item => item !== home?.path && !places.some(name => item === join(home!.path, name))).slice(0, 4);

  return createPortal(<dialog ref={dialog} className="machine-folder-picker" aria-label="Choose project folder"
    onCancel={event => { event.preventDefault(); if (!creatingFolder) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !creatingFolder) onClose(); }}
    onKeyDown={event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'l') { event.preventDefault(); event.stopPropagation(); if (!blocked) setEditingPath(true); }
      if (event.altKey && event.key === 'ArrowUp' && listing && !blocked) { event.preventDefault(); void open(listing.parent); }
    }}>
    <div className="machine-folder-shell" onClick={event => event.stopPropagation()}>
      <header className="machine-folder-heading"><div><h2>Choose project folder</h2><p>{machineName}</p></div><button type="button" aria-label="Close folder picker" disabled={creatingFolder} onClick={onClose}><X size={18} /></button></header>
      <div className="machine-folder-toolbar">
        <div className="machine-folder-navigation">
          <button type="button" aria-label="Back" title="Back" disabled={blocked || position <= 0} onClick={() => void open(history[position - 1], position - 1)}><ArrowLeft size={16} /></button>
          <button type="button" aria-label="Forward" title="Forward" disabled={blocked || position >= history.length - 1} onClick={() => void open(history[position + 1], position + 1)}><ArrowRight size={16} /></button>
          <button type="button" aria-label="Parent folder" title="Up one folder" disabled={blocked || !listing || listing.path === '/'} onClick={() => listing && void open(listing.parent)}><ArrowUp size={16} /></button>
        </div>
        {editingPath ? <form className="machine-folder-path" onSubmit={event => { event.preventDefault(); if (!blocked) void open(path); }}><input ref={pathInput} aria-label="Folder path" spellCheck={false} value={path} disabled={blocked} onChange={event => setPath(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setEditingPath(false); setPath(listing?.path ?? initialPath); } }} /><button type="submit" disabled={blocked} aria-label="Open path"><ArrowRight size={15} /></button></form>
          : <nav className="machine-folder-breadcrumbs" aria-label="Folder path" onClick={event => { if (event.target === event.currentTarget && !blocked) setEditingPath(true); }}>
            <button type="button" disabled={blocked} onClick={() => void open(base)}>{inHome ? <Home size={15} /> : <Folder size={15} />}{inHome ? 'Home' : 'Root'}</button>
            {segments.map((part, index) => <span key={index}><ChevronRight size={12} /><button type="button" disabled={blocked} aria-current={index === segments.length - 1 ? 'location' : undefined} onClick={() => void open(join(base, segments.slice(0, index + 1).join('/')))}>{part}</button></span>)}
            <button type="button" className="machine-folder-edit-path" title="Edit path (⌘L / Ctrl+L)" aria-label="Edit path" disabled={blocked} onClick={() => setEditingPath(true)}><Pencil size={13} /></button>
          </nav>}
      </div>
      <div className="machine-folder-body">
        <aside className="machine-folder-sidebar" aria-label="Locations"><h3>Locations</h3>
          <button type="button" disabled={blocked} aria-current={home && listing?.path === home.path ? 'location' : undefined} onClick={() => void open(home?.path ?? '')}><Home size={15} />Home</button>
          {places.map(name => <button type="button" key={name} disabled={blocked} aria-current={listing?.path === join(home!.path, name) ? 'location' : undefined} onClick={() => void open(join(home!.path, name))}><Folder size={15} />{name}</button>)}
          {!!recentPlaces.length && <><h3>Recent locations</h3>{recentPlaces.map(item => <button type="button" key={item} title={item} disabled={blocked} onClick={() => void open(item)}><Clock size={14} /><span>{basename(item)}</span></button>)}</>}
        </aside>
        <main className="machine-folder-main">
          <div className="machine-folder-actions"><label><Search size={15} /><input type="search" aria-label="Filter this folder" placeholder="Filter this folder…" value={search} onChange={event => { setSearch(event.target.value); setSelected(null); }} /></label><button type="button" title="New folder" disabled={blocked || !!error || !listing} onClick={() => { setNewFolder(true); setFolderName(''); setCreateError(''); }}><FolderPlus size={16} /><span>New folder</span></button></div>
          <div className="machine-folder-list" aria-label="Folders" aria-busy={loading} onKeyDown={navigateKeys}>
            {loading ? <div className="machine-folder-message"><Loader2 size={22} className="animate-spin" /></div> : error ? <div className="machine-folder-message" role="alert"><p>{error}</p><button type="button" onClick={() => void open('')}>Open home folder</button></div> : <>
              {newFolder && <form className="machine-folder-create" onSubmit={event => { event.preventDefault(); void createFolder(); }}><div><FolderPlus size={17} /><input autoFocus aria-label="New folder name" required maxLength={255} value={folderName} disabled={creatingFolder} onChange={event => setFolderName(event.target.value)} placeholder="Folder name" /><button type="submit" aria-label="Create folder" disabled={creatingFolder || !folderName.trim()}>{creatingFolder ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}</button><button type="button" aria-label="Cancel new folder" disabled={creatingFolder} onClick={() => setNewFolder(false)}><X size={15} /></button></div>{createError && <p role="alert">{createError}</p>}</form>}
              {rows.map(name => <button type="button" data-folder-row key={name} disabled={creatingFolder} aria-pressed={selected === join(listing!.path, name)} onFocus={() => setSelected(join(listing!.path, name))} onClick={() => setSelected(join(listing!.path, name))} onDoubleClick={() => void open(join(listing!.path, name))}><Folder size={17} /><span>{name}</span>{selected === join(listing!.path, name) && <Check size={14} />}</button>)}
              {!rows.length && !newFolder && <div className="machine-folder-message"><Folder size={28} /><p>{search ? 'No matching folders' : 'This folder has no subfolders'}</p><small>{search ? 'Try a different filter.' : 'You can choose this folder or create one here.'}</small></div>}
            </>}
          </div>
          <div className="machine-folder-hint">Click to select · Double-click or Enter to open</div>
        </main>
      </div>
      <footer className="machine-folder-footer"><div><span>{selected ? 'Selected folder' : 'Current folder'}</span><strong title={destination}>{destination ?? 'Choose a location'}</strong>{selectionError && <p role="alert">{selectionError}</p>}</div><button type="button" disabled={creatingFolder} onClick={onClose} className="btn-secondary">Cancel</button><button type="button" disabled={blocked || !!error || !destination || !!selectionError} onClick={() => destination && onSelect(destination)} className="btn-primary" title={destination}>Choose {destination ? basename(destination) : 'folder'}</button></footer>
    </div>
  </dialog>, document.body);
}
