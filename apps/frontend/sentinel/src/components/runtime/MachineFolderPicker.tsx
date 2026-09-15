import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUp, FolderPlus, Folder, Loader2, Search, X } from 'lucide-react';
import { api } from '../../lib/api';

type DirectoryListing = { path: string; parent: string; directories: string[] };

export function MachineFolderPicker({ machineId, machineName, initialPath, onSelect, onClose }: {
  machineId: string; machineName: string; initialPath: string;
  onSelect: (path: string) => void; onClose: () => void;
}) {
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [path, setPath] = useState(initialPath);
  const [search, setSearch] = useState('');
  const [newFolder, setNewFolder] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [createError, setCreateError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const request = useRef(0);
  const dialog = useRef<HTMLDialogElement>(null);
  async function open(directory: string) {
    const id = ++request.current;
    setLoading(true); setError('');
    try {
      const result = await api.get<DirectoryListing>(`/machines/${machineId}/directories?path=${encodeURIComponent(directory)}`);
      if (id !== request.current) return;
      setListing(result); setPath(result.path); setSearch(''); setNewFolder(false); setCreateError('');
    } catch (error) {
      if (id === request.current) setError(error instanceof Error ? error.message : 'Could not open folder');
    } finally { if (id === request.current) setLoading(false); }
  }
  async function createFolder() {
    if (!listing || creatingFolder || !folderName.trim()) return;
    if (folderName === '.' || folderName === '..' || /[/\\\x00-\x1f]/.test(folderName)) {
      setCreateError('Enter a folder name without slashes or control characters.'); return;
    }
    const id = ++request.current;
    setCreatingFolder(true); setCreateError('');
    try {
      const result = await api.post<DirectoryListing>(`/machines/${machineId}/directories`, { path: listing.path, name: folderName });
      if (id !== request.current) return;
      setListing(result); setPath(result.path); setSearch(''); setNewFolder(false); setFolderName('');
    } catch (reason) {
      if (id === request.current) setCreateError(reason instanceof Error ? reason.message : 'Could not create folder');
    } finally { if (id === request.current) setCreatingFolder(false); }
  }
  useEffect(() => {
    void open(initialPath);
    return () => { request.current++; };
  }, [machineId]); // Picker is mounted fresh for each browse action.
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return createPortal(<dialog ref={dialog} onCancel={event => { event.preventDefault(); if (!creatingFolder) onClose(); }} onClick={event => { if (event.target === event.currentTarget && !creatingFolder) onClose(); }} aria-label="Choose project folder" className="m-auto w-[min(576px,calc(100vw-48px))] max-h-[calc(100vh-48px)] overflow-y-auto rounded-2xl border border-(--border-subtle) bg-(--surface-1) text-(--text-primary) shadow-2xl p-0 backdrop:bg-black/50 backdrop:backdrop-blur-xs">
    <div onClick={event => event.stopPropagation()} className="p-6 space-y-4">
      <header className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">Choose project folder</h2><p className="text-xs text-(--text-secondary) mt-1">{machineName}</p></div><button type="button" aria-label="Close folder picker" disabled={creatingFolder} onClick={onClose}><X size={18} /></button></header>
      <form className="flex gap-2" onSubmit={event => { event.preventDefault(); if (!creatingFolder && !loading) void open(path); }}>
        <button type="button" title="Parent folder" aria-label="Parent folder" disabled={creatingFolder || loading || !listing || listing.path === '/'} onClick={() => listing && void open(listing.parent)} className="btn-secondary px-3 disabled:opacity-40"><ArrowUp size={16} /></button>
        <input autoFocus spellCheck={false} aria-label="Folder path" value={path} onChange={event => setPath(event.target.value)} placeholder="Home directory" className="min-w-0 flex-1 h-10 rounded-lg border border-(--border-subtle) bg-(--surface-0) px-3 text-sm" />
        <button type="submit" disabled={loading || creatingFolder} className="btn-secondary px-3 text-xs">Go</button>
      </form>
      <div className="flex items-center gap-2">
        <label className="flex min-w-0 flex-1 items-center gap-2 h-10 px-3 rounded-lg border border-(--border-subtle) bg-(--surface-0) text-(--text-secondary) focus-within:border-(--sentinel-blue)"><Search size={15} className="shrink-0" /><input type="search" aria-label="Search folders" placeholder="Search folders…" value={search} onChange={event => setSearch(event.target.value)} className="bg-transparent text-sm outline-hidden min-w-0 w-full" /></label>
        <button type="button" disabled={creatingFolder || loading || !!error || !listing} onClick={() => { setNewFolder(true); setFolderName(''); setCreateError(''); }} className="btn-secondary h-10 px-3 text-xs gap-2 whitespace-nowrap"><FolderPlus size={15} />New folder</button>
      </div>
      {newFolder && <form className="rounded-xl border border-(--border-subtle) bg-(--surface-0) p-3 space-y-3" onSubmit={event => { event.preventDefault(); void createFolder(); }}>
        <label className="block text-xs text-(--text-secondary)">New folder name<input autoFocus required maxLength={255} value={folderName} disabled={creatingFolder} onChange={event => setFolderName(event.target.value)} placeholder="e.g. my-project" className="block w-full mt-2 h-10 px-3 rounded-lg border border-(--border-subtle) bg-(--surface-1) text-sm text-(--text-primary)" /></label>
        <p className="text-xs text-(--text-secondary) break-all">Create in {listing?.path}</p>
        {createError && <p role="alert" className="text-xs text-rose-400">{createError}</p>}
        <div className="flex justify-end gap-2"><button type="button" disabled={creatingFolder} onClick={() => setNewFolder(false)} className="btn-secondary px-3 h-8 text-xs">Cancel</button><button type="submit" disabled={creatingFolder || !folderName.trim()} className="btn-primary px-3 h-8 text-xs gap-2">{creatingFolder && <Loader2 size={13} className="animate-spin" />}Create folder</button></div>
      </form>}

      <div className="h-64 overflow-y-auto rounded-xl border border-(--border-subtle) p-2" aria-busy={loading}>
        {loading ? <div className="h-full flex items-center justify-center"><Loader2 size={22} className="animate-spin" /></div> : error ? <div role="alert" className="p-3 text-sm text-(--text-secondary)">{error}<button type="button" className="block mt-3 underline" onClick={() => void open('')}>Open home folder</button></div> : <>
          {listing?.directories.filter(name => name.toLowerCase().includes(search.toLowerCase())).map(name => <button type="button" disabled={creatingFolder} key={name} onClick={() => listing && void open(`${listing.path === '/' ? '' : listing.path}/${name}`)} className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-left text-sm hover:bg-(--surface-2)"><Folder size={17} className="shrink-0 text-(--accent-solid)" /><span className="truncate">{name}</span></button>)}
          {!listing?.directories.some(name => name.toLowerCase().includes(search.toLowerCase())) && <p className="p-3 text-sm text-(--text-secondary)">{search ? 'No matching folders' : 'No subfolders'}</p>}
        </>}
      </div>
      <footer className="flex justify-end gap-3"><button type="button" disabled={creatingFolder} onClick={onClose} className="btn-secondary h-10 px-4 text-xs">Cancel</button><button type="button" disabled={creatingFolder || loading || !!error || !listing} onClick={() => listing && onSelect(listing.path)} className="btn-primary h-10 px-4 text-xs">Use this folder</button></footer>
    </div>
  </dialog>, document.body);
}
