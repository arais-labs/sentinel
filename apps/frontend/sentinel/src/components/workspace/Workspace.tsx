import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DockviewReact,
  themeDark,
  themeLight,
  type DockviewApi,
  type DockviewReadyEvent,
  type DockviewWillShowOverlayLocationEvent,
} from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';

import { Plus } from 'lucide-react';

import { useThemeStore } from '../../store/theme-store';
import {
  WORKSPACE_PANEL_COMPONENT,
  WORKSPACE_DND_MIME,
  useWorkspaceStore,
  useOpenTabIds,
} from '../../store/workspace-store';
import { WORKSPACE_TABS, isWorkspaceTabId } from '../../lib/workspace-tabs';
import { WorkspacePane, WorkspaceInstanceContext } from './WorkspacePane';
import { PaneHeaderTab } from './PaneHeaderTab';

/** dockview-react requires the single content component registered by id. */
const DOCKVIEW_COMPONENTS = { [WORKSPACE_PANEL_COMPONENT]: WorkspacePane };

/**
 * The custom dockview tab doubling as the pane header. Registered as
 * `defaultTabComponent` and rendered full-width (`singleTabMode="fullwidth"`),
 * so the whole header bar is dockview's draggable tab element — dragging its
 * empty areas repositions the pane. Per-tab renderers keyed by id.
 */
const DOCKVIEW_TAB_COMPONENTS = { [WORKSPACE_PANEL_COMPONENT]: PaneHeaderTab };

/**
 * Scoped CSS for the workspace host. One-view-per-pane means dockview's tab strip
 * holds exactly one full-width tab, which we use as the pane header
 * ({@link PaneHeaderTab}). We size that strip to the header height and strip the
 * default chrome (divider/border/min-width) so only our themed header shows.
 * Injected here rather than the global stylesheet to keep the override
 * co-located with the host; `!important` overrides the theme cascade.
 */
const DOCKVIEW_TAB_STRIP_CSS = `
.dockview-pane-header .dv-tabs-and-actions-container {
  height: 40px !important;
}
.dockview-pane-header .dv-tab {
  padding: 0 !important;
  border: none !important;
  min-width: 0 !important;
}
.dockview-pane-header .dv-tab::before {
  display: none !important;
}
.dockview-pane-header {
  --dv-group-view-background-color: var(--app-bg);
}
`;

/**
 * Block any drop that would stack panels into a tab group, preserving the
 * one-view-per-pane invariant. Edge drops (top/bottom/left/right onto content
 * or the root edge) reposition / split and are allowed; drops onto a tab strip,
 * the header free space, or the centre of a pane would stack, so they are
 * prevented.
 */
function blockStackingDrops(event: DockviewWillShowOverlayLocationEvent) {
  if (
    event.kind === 'tab' ||
    event.kind === 'header_space' ||
    event.position === 'center'
  ) {
    event.preventDefault();
  }
}

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
  // Reactive count of live panes. Driven by dockview's onDidLayoutChange (plus a
  // seed in onReady) so the empty-state overlay reliably hides whenever a pane
  // exists — independent of when panel params land or whether the open-tab
  // selector happens to change. Reading apiRef.current?.panels.length during
  // render would not re-render on its own (a ref is not reactive).
  const [paneCount, setPaneCount] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const addRef = useRef<HTMLDivElement>(null);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const store = useWorkspaceStore.getState();
    apiRef.current = event.api;
    // bindApi seeds state immediately and subscribes to layout changes.
    const disposeBind = store.bindApi(event.api);
    // Prevent stacking drops so every group keeps exactly one panel.
    const overlay = event.api.onWillShowOverlay(blockStackingDrops);
    // Sidebar tabs are external drag sources: accept our tagged drag so dockview
    // renders its drop zones, then add (or move, if already open) a pane on drop.
    const dragOver = event.api.onUnhandledDragOverEvent((dragEvent) => {
      const native = dragEvent.nativeEvent;
      if (native instanceof DragEvent && native.dataTransfer?.types?.includes(WORKSPACE_DND_MIME)) {
        dragEvent.accept();
      }
    });
    const drop = event.api.onDidDrop((dropEvent) => {
      const tabId =
        dropEvent.nativeEvent instanceof DragEvent
          ? dropEvent.nativeEvent.dataTransfer?.getData(WORKSPACE_DND_MIME)
          : undefined;
      if (!tabId || !isWorkspaceTabId(tabId)) return;
      const refPaneId =
        dropEvent.group?.activePanel?.id ?? dropEvent.group?.panels[0]?.id ?? null;
      useWorkspaceStore.getState().dropTab(tabId, refPaneId, dropEvent.position);
    });
    // Keep paneCount in sync with the live layout so the overlay reflects pane
    // presence. onDidLayoutChange fires (asynchronously) on every add/remove.
    const layout = event.api.onDidLayoutChange(() => {
      setPaneCount(event.api.panels.length);
    });
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
    // Seed the count after any restore so the overlay starts in the right state.
    setPaneCount(event.api.panels.length);
    disposeRef.current = () => {
      layout.dispose();
      overlay.dispose();
      dragOver.dispose();
      drop.dispose();
      disposeBind();
    };
  }, []);

  // Hold the combined disposer so it runs on unmount.
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

  const hasPanels = paneCount > 0;

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
          <style>{DOCKVIEW_TAB_STRIP_CSS}</style>
          <DockviewReact
            components={DOCKVIEW_COMPONENTS}
            tabComponents={DOCKVIEW_TAB_COMPONENTS}
            defaultTabComponent={PaneHeaderTab}
            onReady={onReady}
            theme={dockviewTheme}
            singleTabMode="fullwidth"
            disableFloatingGroups
            className="dockview-pane-header h-full w-full"
          />

          {/* Empty-state overlay: no panes yet -> invite the user to open a tab.
              dockview renders nothing meaningful until the first panel exists. */}
          {apiReady && !hasPanels && (
            <div className="absolute inset-0 z-[1000] flex flex-col items-center justify-center gap-5 bg-[color:var(--app-bg)] p-8 text-center">
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
                    aria-haspopup="listbox"
                    aria-expanded={addOpen}
                    className="flex items-center gap-2 rounded-md bg-[color:var(--accent-solid)] px-4 py-2 text-sm font-medium text-[color:var(--app-bg)] shadow-sm transition-opacity hover:opacity-90"
                  >
                    <Plus size={16} />
                    Open a tab
                  </button>
                  {addOpen && (
                    <div
                      role="listbox"
                      className="absolute left-1/2 top-[calc(100%+6px)] z-50 max-h-80 w-56 -translate-x-1/2 overflow-y-auto rounded-md border border-[color:var(--border-strong)] bg-[color:var(--surface-1)] py-1 text-left shadow-lg shadow-black/30"
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
                          className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)]"
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
