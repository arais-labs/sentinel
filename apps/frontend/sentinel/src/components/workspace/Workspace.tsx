import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DockviewReact,
  themeDark,
  themeLight,
  type DockviewApi,
  type DockviewReadyEvent,
} from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';

import { Plus } from 'lucide-react';

import { useThemeStore } from '../../store/theme-store';
import {
  WORKSPACE_PANEL_COMPONENT,
  useWorkspaceStore,
  useOpenTabIds,
} from '../../store/workspace-store';
import { WORKSPACE_TABS } from '../../lib/workspace-tabs';
import { WorkspacePane, WorkspaceInstanceContext } from './WorkspacePane';

/** dockview-react requires the single content component registered by id. */
const DOCKVIEW_COMPONENTS = { [WORKSPACE_PANEL_COMPONENT]: WorkspacePane };

export interface WorkspaceProps {
  /**
   * Instance the whole workspace is scoped to. Supplied to every pane through
   * {@link WorkspaceInstanceContext} so pages can resolve their instance via
   * `useInstanceName()` even though panes are not the active route.
   */
  instanceName: string | undefined;
  /** Optional extra classes for the outer fill container. */
  className?: string;
}

/**
 * Tiling workspace host.
 *
 * Wires dockview to the workspace store: on ready it binds the live
 * `DockviewApi` (the store subscribes to layout changes and persists
 * `toJSON()`), then restores any saved layout. Splitting / closing / resizing /
 * drag-and-drop are all owned by dockview; the store mirrors the result.
 *
 * The dockview theme tracks the app's light/dark mode so the chrome (tab strips,
 * sashes, drop overlays) matches the surrounding UI.
 */
export function Workspace({ instanceName, className = '' }: WorkspaceProps) {
  const theme = useThemeStore((state) => state.theme);
  const openTab = useWorkspaceStore((state) => state.openTab);
  const openTabIds = useOpenTabIds();

  // Track when the api is bound so the empty-state CTA can open the first tab.
  const apiRef = useRef<DockviewApi | null>(null);
  const [apiReady, setApiReady] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const addRef = useRef<HTMLDivElement>(null);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const store = useWorkspaceStore.getState();
    apiRef.current = event.api;
    // bindApi seeds state immediately and subscribes to layout changes.
    const dispose = store.bindApi(event.api);
    const saved = store.layout;
    if (saved) {
      try {
        event.api.fromJSON(saved);
      } catch {
        // A corrupt / incompatible persisted layout should not wedge the UI:
        // drop it and start from an empty workspace.
        store.resetWorkspace();
      }
    }
    setApiReady(true);
    disposeRef.current = dispose;
  }, []);

  // Hold the bindApi disposer so it runs on unmount.
  const disposeRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    return () => {
      disposeRef.current?.();
      disposeRef.current = null;
      apiRef.current = null;
    };
  }, []);

  const dockviewTheme = useMemo(
    () => (theme === 'dark' ? themeDark : themeLight),
    [theme],
  );

  const panelCount = apiReady ? apiRef.current?.panels.length ?? 0 : 0;
  const hasPanels = panelCount > 0;

  const availableTabs = useMemo(
    () => WORKSPACE_TABS.filter((tab) => !openTabIds.includes(tab.id)),
    [openTabIds],
  );

  useEffect(() => {
    if (!addOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(event.target as Node)) {
        setAddOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [addOpen]);

  return (
    <WorkspaceInstanceContext.Provider value={instanceName}>
      <div
        className={`relative flex h-full w-full flex-col overflow-hidden bg-[color:var(--app-bg)] ${className}`}
      >
        <div className="relative min-h-0 flex-1">
          <DockviewReact
            components={DOCKVIEW_COMPONENTS}
            onReady={onReady}
            theme={dockviewTheme}
            className="h-full w-full"
          />

          {/* Empty-state overlay: no panes yet -> invite the user to open a tab.
              dockview renders nothing meaningful until the first panel exists. */}
          {apiReady && !hasPanels && (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-5 p-8 text-center">
              <div className="pointer-events-auto flex flex-col items-center gap-4">
                <div>
                  <p className="text-sm font-medium text-[color:var(--text-primary)]">
                    Your workspace is empty
                  </p>
                  <p className="mt-1 text-xs text-[color:var(--text-secondary)]">
                    Open a tab to start. Split panes to view several at once.
                  </p>
                </div>
                <div ref={addRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setAddOpen((value) => !value)}
                    className="flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-4 py-2 text-sm font-medium text-[color:var(--text-primary)] transition-colors hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-2)]"
                  >
                    <Plus size={16} className="text-[color:var(--text-muted)]" />
                    Open a tab
                  </button>
                  {addOpen && (
                    <div
                      role="listbox"
                      className="absolute left-1/2 top-[calc(100%+6px)] z-50 max-h-80 w-56 -translate-x-1/2 overflow-y-auto rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] py-1 text-left shadow-lg shadow-black/20"
                    >
                      {availableTabs.map((tab) => (
                        <button
                          type="button"
                          key={tab.id}
                          role="option"
                          aria-selected={false}
                          onClick={() => {
                            openTab(tab.id);
                            setAddOpen(false);
                          }}
                          className="flex w-full items-center gap-2.5 px-3 py-1.5 text-sm text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]"
                        >
                          <tab.icon size={15} className="shrink-0" />
                          <span className="flex-1 truncate">{tab.label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </WorkspaceInstanceContext.Provider>
  );
}

/**
 * Re-exported so integrators can wire a "reset layout" affordance without
 * reaching into the store directly.
 */
export function useResetWorkspace() {
  return useWorkspaceStore((state) => state.resetWorkspace);
}
