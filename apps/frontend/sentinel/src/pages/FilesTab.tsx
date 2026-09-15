import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { useActiveSessionId } from '../store/active-session-store';
import { useSessionWorkspace } from '../hooks/useSessionWorkspace';
import { WorkspaceBrowser } from '../components/files/WorkspaceBrowser';
import { resolveFilesWorkspace } from '../components/files/workspaceSelection';
import type { Workspace } from '../types/api';

export function FilesTab() {
  const instance = useInstanceName() ?? '';
  return <SessionFiles key={instance} instance={instance} />;
}
function SessionFiles({ instance }: { instance: string }) {
  const sessionId = useActiveSessionId();
  const { workspace: attached, loading: attachmentLoading } = useSessionWorkspace(sessionId, instance);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selection, setSelection] = useState(() => {
    try { return { id: sessionStorage.getItem(`files-workspace:${instance}`) ?? '', pinned: sessionStorage.getItem(`files-pinned:${instance}`) === 'true' }; }
    catch { return { id: '', pinned: false }; }
  });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api.get<Workspace[]>('/workspaces').then(value => { if (active) { setWorkspaces(value); setError(''); } })
      .catch(reason => { if (active) setError(reason.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instance]);
  const available = attached && !workspaces.some(item => item.id === attached.id) ? [...workspaces, attached] : workspaces;
  const workspace = resolveFilesWorkspace(available, selection.id, selection.pinned, attached);
  useEffect(() => {
    // Remember the followed workspace as a fallback for sessions without one.
    if (!selection.pinned && attached && selection.id !== attached.id) setSelection({ id: attached.id, pinned: false });
  }, [attached, selection]);
  useEffect(() => {
    try {
      sessionStorage.setItem(`files-workspace:${instance}`, selection.id);
      sessionStorage.setItem(`files-pinned:${instance}`, String(selection.pinned));
    } catch { /* Browsing remains available without preference storage. */ }
  }, [instance, selection]);
  if (error && !attached) return <div className="p-6 text-sm text-red-400" role="alert">{error}</div>;
  if (!workspace) return <div className="flex h-full items-center justify-center p-6 text-sm text-(--text-muted)">{loading || attachmentLoading ? 'Loading projects…' : 'Create a workspace to browse its files.'}</div>;
  return <WorkspaceBrowser key={`${instance}:${workspace.id}`} instance={instance} workspace={workspace} workspaces={available}
    pinned={selection.pinned} followingAttachment={Boolean(attached)} attachmentLoading={attachmentLoading}
    onPin={() => setSelection({ id: workspace.id, pinned: !selection.pinned })}
    onWorkspace={id => setSelection({ id, pinned: true })} />;
}
