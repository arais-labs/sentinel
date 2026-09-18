import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
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

import { useFocusModeStore } from '../../store/focus-mode-store';
import { useThemeStore } from '../../store/theme-store';
import { useActiveSessionStore } from '../../store/active-session-store';
import {
  WORKSPACE_PANEL_COMPONENT,
  WORKSPACE_DND_MIME,
  useWorkspaceStore,
  useOpenTabIds,
  sessionLayoutKey,
  executeSessionLayout,
} from '../../store/workspace-store';
import { WORKSPACE_TABS, isWorkspaceTabId } from '../../lib/workspace-tabs';
import { WorkspacePane } from './WorkspacePane';
import { WorkspaceInstanceContext, RetainedSessionContext } from '../../lib/workspace-context-values';
import { PaneHeaderTab } from './PaneHeaderTab';
import { animatePaneLayout } from './animate-pane-layout';
import { DesktopSocket } from '../../lib/desktop-socket';
import { wsSessionsBaseUrl } from '../../lib/env';

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
::view-transition-old(root), ::view-transition-new(root) { animation-duration: 220ms; }
::view-transition { pointer-events: none; }
@media (prefers-reduced-motion: reduce) {
  ::view-transition-old(root), ::view-transition-new(root) { animation-duration: 0s; }
}
.dockview-pane-header .dv-tabs-and-actions-container {
  height: 40px !important;
}
.dockview-pane-header .dv-tab {
  padding: 0 !important;
  border: none !important;
  min-width: 0 !important;
  max-width: 100%;
  overflow: hidden;
}
.dockview-pane-header .dv-tab::before {
  display: none !important;
}
.dockview-pane-header {
  --dv-group-view-background-color: var(--app-bg);
}
`;

/** Keep header drops from stacking tabs; center content drops replace the view. */
function blockStackingDrops(event: DockviewWillShowOverlayLocationEvent) {
  if (event.kind === 'tab' || event.kind === 'header_space') event.preventDefault();
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
 * `DockviewApi` (the store restores the saved layout before subscribing to
 * changes and persisting `toJSON()`). Splitting / closing / resizing /
 * drag-and-drop are all owned by dockview; the store mirrors the result.
 *
 * The dockview theme tracks the app's light/dark mode so the chrome (tab strips,
 * sashes, drop overlays) matches the surrounding UI.
 */
export function Workspace(props: WorkspaceProps) {
  const sessionId = useActiveSessionStore(state => props.instanceName ? state.byInstance[props.instanceName] ?? null : null);
  const layoutKey = sessionLayoutKey(props.instanceName, sessionId);
  const openSessions = useActiveSessionStore(state => props.instanceName ? state.recentByInstance[props.instanceName] : undefined);
  const [recent, setRecent] = useState<string[]>([]);
  const keys = [layoutKey, ...recent.filter(key => key !== layoutKey && JSON.parse(key)[0] === (props.instanceName ?? null) && (JSON.parse(key)[1] === null || openSessions?.includes(JSON.parse(key)[1])))].slice(0, 10);
  useEffect(() => { setRecent(keys); }, [layoutKey, openSessions]);
  return <>{keys.map(key => <div key={key} hidden={key !== layoutKey} className="h-full w-full" inert={key !== layoutKey}>
    <RetainedSessionContext.Provider value={{ sessionId: JSON.parse(key)[1], visible: key === layoutKey }}>
      <SessionWorkspace {...props} layoutKey={key} visible={key === layoutKey} />
    </RetainedSessionContext.Provider>
  </div>)}</>;
}

function SessionWorkspace({ instanceName, className = '', layoutKey, visible }: WorkspaceProps & { layoutKey: string; visible: boolean }) {
  const theme = useThemeStore((state) => state.theme);
  const openTab = useWorkspaceStore((state) => state.openTab);
  const openTabIds = useOpenTabIds();

  // Track when the api is bound so the empty-state CTA can open the first tab.
  const apiRef = useRef<DockviewApi | null>(null);
  const [apiReady, setApiReady] = useState(false);
  useEffect(() => {
    const [, sessionId] = JSON.parse(layoutKey) as [string | null, string | null];
    if (!visible || !instanceName || !sessionId || !apiReady) return;
    let socket: DesktopSocket | null = null;
    let disposed = false;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const send = (payload: unknown) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
    };
    const ready = () => send({ type: 'layout_ready', ready: !document.hidden });
    const connect = () => {
      if (disposed) return;
      const current = new DesktopSocket(`${wsSessionsBaseUrl(instanceName)}/${sessionId}/layout`);
      socket = current;
      current.onopen = ready;
      current.onclose = () => {
        if (!disposed) reconnect = setTimeout(connect, 1000);
      };
      current.onmessage = message => {
        if (disposed || socket !== current) return;
        let event: Record<string, unknown>;
        try { event = JSON.parse(String(message.data)); } catch { return; }
        if (event.type !== 'layout_request' || typeof event.request_id !== 'string') return;
        let result: unknown;
        try {
          if (document.hidden || typeof event.expires_at !== 'number' || Date.now() > event.expires_at) throw new Error('Layout request expired or session is not visible; inspect again');
          if (!event.command || typeof event.command !== 'object') throw new Error('Invalid layout command');
          result = executeSessionLayout(layoutKey, event.command as Record<string, unknown>);
        } catch (error) {
          result = { ok: false, error: error instanceof Error ? error.message : 'Unable to change layout' };
        }
        send({ type: 'layout_result', request_id: event.request_id, result });
      };
    };
    connect();
    document.addEventListener('visibilitychange', ready);
    return () => {
      disposed = true;
      clearTimeout(reconnect);
      document.removeEventListener('visibilitychange', ready);
      socket?.close();
    };
  }, [apiReady, instanceName, layoutKey, visible]);
  const focusPaneId = useFocusModeStore(state => state.paneId);
  useEffect(() => {
    if (!visible) return;
    const keyboard = (event: KeyboardEvent) => {
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey || event.isComposing || event.key.toLowerCase() !== 'f') return;
      const { paneId, setPaneId } = useFocusModeStore.getState();
      const activePaneId = apiRef.current?.activePanel?.id;
      if (!paneId && !activePaneId) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      setPaneId(paneId ? null : activePaneId!);
    };
    // Capture before editors and terminals consume their own key bindings.
    window.addEventListener('keydown', keyboard, true);
    return () => window.removeEventListener('keydown', keyboard, true);
  }, [visible]);
  useLayoutEffect(() => {
    if (!visible || !apiReady || !focusPaneId) return;
    const panel = apiRef.current?.getPanel(focusPaneId);
    if (!panel) {
      useFocusModeStore.getState().setPaneId(null);
      return;
    }
    panel.api.maximize();
    panel.group.model.header.hidden = true;
    const focusContent = () => {
      window.focus();
      const input = panel.group.element.querySelector<HTMLElement>('textarea:not(:disabled), canvas[tabindex]');
      input?.focus({ preventScroll: true });
    };
    const focusFrame = requestAnimationFrame(focusContent);
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') useFocusModeStore.getState().setPaneId(null);
    };
    const removed = apiRef.current!.onDidRemovePanel(removedPanel => {
      if (removedPanel.id === focusPaneId) useFocusModeStore.getState().setPaneId(null);
    });
    document.addEventListener('keydown', keyboard, true);
    return () => {
      document.removeEventListener('keydown', keyboard, true);
      cancelAnimationFrame(focusFrame);
      if (apiRef.current?.getPanel(focusPaneId)) panel.group.model.header.hidden = false;
      removed.dispose();
      if (apiRef.current?.getPanel(focusPaneId)) {
        if (panel.api.isMaximized()) panel.api.exitMaximized();
        requestAnimationFrame(focusContent);
      }
    };
  }, [apiReady, focusPaneId, visible]);
  useEffect(() => { if (visible) useFocusModeStore.setState({ paneId: null }); }, [visible]);
  // A closed pane window re-docks its tab next to the active pane of the layout it came from.
  useEffect(() => {
    const desktop = window.sentinelDesktop;
    if (!desktop || !apiReady) return;
    return desktop.onPaneWindowClosed(info => {
      const [, sessionId] = JSON.parse(layoutKey) as [string | null, string | null];
      const api = apiRef.current;
      if (!api || info.instance !== instanceName || (info.session ?? null) !== sessionId || !isWorkspaceTabId(info.tabId)) return;
      if (api.panels.some(panel => panel.params?.tabId === info.tabId)) return;
      const reference = api.activePanel ?? api.activeGroup?.activePanel ?? api.panels.at(-1);
      api.addPanel({
        id: `pane-${info.tabId}-${Math.random().toString(36).slice(2, 8)}`,
        component: WORKSPACE_PANEL_COMPONENT,
        params: { tabId: info.tabId },
        ...(reference ? { position: { referencePanel: reference.id, direction: 'right' } } : {}),
      });
      if (visible) useWorkspaceStore.getState().syncFromApi();
    });
  }, [apiReady, instanceName, layoutKey, visible]);
  // Reactive count of live panes. Driven by dockview's onDidLayoutChange (plus a
  // seed in onReady) so the empty-state overlay reliably hides whenever a pane
  // exists — independent of when panel params land or whether the open-tab
  // selector happens to change. Reading apiRef.current?.panels.length during
  // render would not re-render on its own (a ref is not reactive).
  const [paneCount, setPaneCount] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const addRef = useRef<HTMLDivElement>(null);

  const bindRef = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    if (!apiReady || !apiRef.current) return;
    bindRef.current?.();
    bindRef.current = visible ? useWorkspaceStore.getState().bindApi(apiRef.current, layoutKey, false) : null;
    return () => { bindRef.current?.(); bindRef.current = null; };
  }, [visible, apiReady, layoutKey]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    const store = useWorkspaceStore.getState();
    apiRef.current = event.api;
    // bindApi restores and synchronizes the layout before subscribing to changes.
    bindRef.current = store.bindApi(event.api, layoutKey);
    // Prevent stacking drops so every group keeps exactly one panel.
    const overlay = event.api.onWillShowOverlay(blockStackingDrops);
    // Sidebar tabs are external drag sources: accept our tagged drag so dockview
    // renders its drop zones, then add (or move, if already open) a pane on drop.
    const dragOver = event.api.onUnhandledDragOver((dragEvent) => {
      const native = dragEvent.nativeEvent;
      if (native instanceof DragEvent && native.dataTransfer?.types?.includes(WORKSPACE_DND_MIME)) {
        dragEvent.accept();
      }
    });
    const replaceDrop = event.api.onWillDrop(dropEvent => {
      if (dropEvent.position !== 'center') return;
      const transfer = dropEvent.getData();
      if (!transfer || transfer.viewId !== event.api.id) return;
      const source = transfer.panelId ? event.api.getPanel(transfer.panelId) : event.api.getGroup(transfer.groupId)?.activePanel;
      const target = dropEvent.group?.activePanel;
      const tabId = source?.params?.tabId;
      if (!target || !isWorkspaceTabId(tabId)) return;
      dropEvent.preventDefault();
      useWorkspaceStore.getState().dropTab(tabId, target.id, 'center');
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
    // presence. Removing the last panel leaves no layout to change, so count on
    // add/remove as well as layout changes.
    const count = () => setPaneCount(event.api.panels.length);
    const layout = event.api.onDidLayoutChange(count);
    const added = event.api.onDidAddPanel(count);
    const removedPanel = event.api.onDidRemovePanel(count);
    const disposeAnimations = animatePaneLayout(event.api);
    setApiReady(true);
    // Seed the count after any restore so the overlay starts in the right state.
    setPaneCount(event.api.panels.length);
    disposeRef.current = () => {
      disposeAnimations();
      layout.dispose();
      added.dispose();
      removedPanel.dispose();
      overlay.dispose();
      dragOver.dispose();
      drop.dispose();
      replaceDrop.dispose();
      bindRef.current?.();
    };
  }, [layoutKey]);

  // Hold the combined disposer so it runs on unmount.
  const disposeRef = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
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
        data-focus-workspace
        className={`${focusPaneId ? 'workspace-focused fixed inset-0 z-20000' : 'relative'} flex h-full w-full flex-col overflow-hidden bg-(--app-bg) ${className}`}
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
            disableTabsOverflowList
            disableFloatingGroups
            className="dockview-pane-header h-full w-full"
          />

          {/* Empty-state overlay: no panes yet -> invite the user to open a tab.
              dockview renders nothing meaningful until the first panel exists. */}
          {apiReady && !hasPanels && (
            <div className="absolute inset-0 z-1000 flex flex-col items-center justify-center gap-5 bg-(--app-bg) p-8 text-center">
              <div className="pointer-events-auto flex flex-col items-center gap-4">
                <div>
                  <p className="text-sm font-medium text-(--text-primary)">
                    No panels open
                  </p>
                  <p className="mt-1 text-xs text-(--text-secondary)">
                    Open a tab to start. Split panes to view several at once.
                  </p>
                </div>
                <div ref={addRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setAddOpen((value) => !value)}
                    aria-haspopup="listbox"
                    aria-expanded={addOpen}
                    className="flex items-center gap-2 rounded-md bg-(--accent-solid) px-4 py-2 text-sm font-medium text-(--app-bg) shadow-xs transition-opacity hover:opacity-90"
                  >
                    <Plus size={16} />
                    Open a tab
                  </button>
                  {addOpen && (
                    <div
                      role="listbox"
                      className="absolute left-1/2 top-[calc(100%+6px)] z-50 max-h-80 w-56 -translate-x-1/2 overflow-y-auto rounded-md border border-(--border-strong) bg-(--surface-1) py-1 text-left shadow-lg shadow-black/30"
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
                          className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm text-(--text-secondary) transition-colors hover:bg-(--surface-2) hover:text-(--text-primary)"
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
