import { useEffect } from 'react';
import { create } from 'zustand';

import { api } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { useActiveSessionId } from '../store/active-session-store';
import { unsupportedViews } from '../components/environment/workspaceCatalog';
import type { WorkspaceCatalog } from '../types/api';
import { useSessionWorkspace } from './useSessionWorkspace';

/** Views the active session's workspace cannot serve. Empty while its OS is unknown. */
const useStore = create<{ views: string[] }>(() => ({ views: [] }));
const catalogs = new Map<string, Promise<WorkspaceCatalog>>();

export const useUnsupportedViews = () => useStore(state => state.views);

/** Resolves the active session's workspace OS; mounted once by the instance shell. */
export function useWatchUnsupportedViews(enabled: boolean) {
  const instanceName = useInstanceName() ?? null;
  const sessionId = useActiveSessionId();
  const { workspace } = useSessionWorkspace(enabled ? sessionId : null, enabled ? instanceName : null);
  const machine = workspace?.machine_id, distribution = workspace?.distribution;
  useEffect(() => {
    if (!enabled) return;
    useStore.setState({ views: [] });
    if (!machine || !distribution) return;
    let cancelled = false;
    const catalog = catalogs.get(machine) ?? api.get<WorkspaceCatalog>(`/machines/${machine}/development-tools`);
    catalogs.set(machine, catalog);
    catalog.then(value => { if (!cancelled) useStore.setState({ views: unsupportedViews(value, distribution) }); })
      .catch(() => { catalogs.delete(machine); });
    return () => { cancelled = true; };
  }, [enabled, machine, distribution]);
}
