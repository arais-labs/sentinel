import { useEffect, useState } from 'react';
import { useSessionStream } from './useSessionStream';
import { api } from '../lib/api';
import type { Workspace } from '../types/api';

export function useSessionWorkspace(sessionId: string | null, instanceName: string | null) {
  const [resolved, setResolved] = useState<{ sessionId: string; instanceName: string; workspace: Workspace | null } | null>(null);
  const [revision, setRevision] = useState(0);
  useSessionStream(instanceName, sessionId, { onEvent: event => {
    if (event.type === 'workspace_changed' || event.type === 'connected') setRevision(value => value + 1);
  } });
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.sessionId === sessionId) setRevision(value => value + 1);
    };
    window.addEventListener('sentinel:workspace-changed', listener);
    return () => window.removeEventListener('sentinel:workspace-changed', listener);
  }, [sessionId]);
  useEffect(() => {
    let cancelled = false;
    if (!sessionId || !instanceName) return;
    api.get<Workspace | null>(`/instances/${encodeURIComponent(instanceName)}/sessions/${sessionId}/workspace`).then(value => {
      if (!cancelled) setResolved({ sessionId, instanceName, workspace: value });
    }).catch(() => {
      if (!cancelled) setResolved({ sessionId, instanceName, workspace: null });
    });
    return () => { cancelled = true; };
  }, [sessionId, instanceName, revision]);
  const workspace = resolved?.sessionId === sessionId && resolved?.instanceName === instanceName
    ? resolved.workspace : null;
  // Revalidation keeps the existing pane mounted. A different conversation must
  // still resolve its attachment before showing any workspace content.
  const loading = Boolean(sessionId && instanceName &&
    (resolved?.sessionId !== sessionId || resolved?.instanceName !== instanceName));
  return { workspace, loading, revision };
}
