import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';
import type {
  DockviewApi,
  SerializedDockview,
  AddPanelOptions,
  Direction,
  Position,
} from 'dockview-react';
import { positionToDirection } from 'dockview-react';
import { flushSync } from 'react-dom';
import { useFocusModeStore } from './focus-mode-store';

import {
  WORKSPACE_TAB_IDS,
  WORKSPACE_TABS,
  isWorkspaceTabId,
  type WorkspaceTabId,
} from '../lib/workspace-tabs';

/**
 * Workspace store
 * ---------------
 * dockview owns the live tiling UI (split / resize / drag / close). This store:
 *   1. Persists the serialized dockview layout to localStorage so it survives
 *      reloads.
 *   2. Tracks which tab each pane shows and enforces the "each tab open at most
 *      once across all panes" rule.
 *   3. Exposes imperative actions that drive the live `DockviewApi` once bound.
 *
 * One-view-per-pane model:
 *   - Every dockview group holds exactly one panel. Panels are never stacked
 *     into a tab group (`Workspace.tsx` blocks center / tab-strip drops, the
 *     split control creates new groups, and `openTab` swaps a pane's content
 *     rather than adding panels).
 *
 * Vocabulary:
 *   - "pane" == a dockview panel (and its sole-occupant group). `paneId` is the
 *     dockview panel id.
 *   - Every panel carries `params.tabId` so a deserialized layout is
 *     self-describing and `openTabs` can be rebuilt on reload.
 */

/** The single content component id registered with DockviewReact. */
export const WORKSPACE_PANEL_COMPONENT = 'workspace-pane';

/** dataTransfer MIME marking a workspace-tab dragged out of the sidebar launcher. */
export const WORKSPACE_DND_MIME = 'application/x-sentinel-workspace-tab';

/** localStorage key for the persisted layout. */
const STORAGE_KEY = 'sentinel.workspace';

/** Dockview permits empty groups; Sentinel's controls live on panels, so it must not.
 * Run at the completed-layout boundary, never during an intermediate panel move.
 * A real empty-picker pane still contains a panel and is deliberately preserved.
 */
function removeVacantGroups(api: DockviewApi): void {
  for (const group of [...api.groups]) {
    if (group.panels.length === 0) api.removeGroup(group);
  }
}

/** Focus belongs to the live UI, never to a restored layout or undo snapshot. */
function unfocusedLayout(snapshot: SerializedDockview): SerializedDockview {
  const layout = structuredClone(snapshot);
  const clearGrid = (grid: SerializedDockview['grid'] & { maximizedNode?: unknown }) => {
    delete grid.maximizedNode;
    const visit = (node: typeof grid.root) => {
      if (Array.isArray(node.data)) node.data.forEach(visit);
      else delete node.data.hideHeader;
    };
    if (grid.root) visit(grid.root);
  };
  if (layout.grid) clearGrid(layout.grid);
  for (const group of [...(layout.floatingGroups ?? []), ...(layout.popoutGroups ?? [])]) {
    if (group.data) delete group.data.hideHeader;
    if (group.grid) clearGrid(group.grid);
  }
  return layout;
}

/** Direction a split can target. Mirrors dockview's positional Direction. */
export type SplitDirection = 'left' | 'right' | 'above' | 'below' | 'within';

/** Params attached to every dockview panel created by the workspace. */
export interface WorkspacePaneParams {
  tabId: WorkspaceTabId;
}

export interface WorkspaceState {
  /** Layouts keyed by instance and session; never shared between sessions. */
  sessionLayouts: Record<string, SerializedDockview>;
  /**
   * Active serialized layout, restored from sessionLayouts on bind. `null`
   * before the first layout is created.
   */
  layout: SerializedDockview | null;
  /**
   * Maps an open tab id to the pane (dockview panel) id that hosts it.
   * Source of truth for the at-most-once rule and duplicate-disable selectors.
   * Rebuilt from the active session's restored layout.
   */
  openTabs: Partial<Record<WorkspaceTabId, string>>;

  // --- actions ---------------------------------------------------------------

  /** Register the live DockviewApi. Returns a disposer to call on unmount. */
  bindApi: (api: DockviewApi | null, layoutKey: string, restore?: boolean) => () => void;
  /** Pull the current serialized layout + open-tab map out of the live api. */
  syncFromApi: () => void;

  /**
   * Open a tab from the nav launcher. Behaviour (one-view-per-pane):
   *   - Already open anywhere -> focus/activate that pane (no duplicate, no
   *     move; preserves at-most-once).
   *   - No panes yet -> create the first pane hosting the tab.
   *   - Otherwise -> replace the *active* pane's content with this tab in place
   *     (swap params, never add/stack a panel).
   * Returns the pane id now hosting the tab, or null if no api is bound.
   */
  openTab: (tabId: WorkspaceTabId) => string | null;
  /** Reveal the terminal, or place it where both it and the current views fit. */
  openTerminalPane: (referencePaneId?: string) => string | null;
  /**
   * Switch an existing pane to show `tabId`. Rejected (returns false) when that
   * tab is already open in a different pane.
   */
  setPaneTab: (paneId: string, tabId: WorkspaceTabId) => boolean;
  /** Close (remove) a pane. */
  closePane: (paneId: string) => void;
  /**
   * Split `paneId` in `direction`, placing `tabId` in a brand-new pane (its own
   * group — never stacked into a tab group). Rejected (returns null) if `tabId`
   * is already open elsewhere. Returns the new pane id, or null on rejection /
   * no bound api.
   */
  splitPane: (
    paneId: string,
    tabId: WorkspaceTabId,
    direction: SplitDirection,
  ) => string | null;
  /**
   * Handle a sidebar tab dropped onto the workspace at `referencePaneId` /
   * `position`. New tab -> add a pane there; already-open tab -> move its pane
   * there (one-view-per-pane, never duplicated). `referencePaneId` null falls
   * back to the active pane (or the first pane when empty). A center drop is
   * replaces the reference view without leaving stacked tabs. Returns its hosting pane id.
   */
  dropTab: (
    tabId: WorkspaceTabId,
    referencePaneId: string | null,
    position: Position,
  ) => string | null;
  /** Remove every pane and clear the layout. */
  resetWorkspace: () => void;
}

let liveApi: DockviewApi | null = null;
let liveLayoutKey: string | null = null;
const layoutUndo = new Map<string, { layout: SerializedDockview; focused: string | null }>();

/** Metadata-only control surface; never reads editor, chat, or terminal contents. */
export function executeSessionLayout(layoutKey: string, command: Record<string, unknown>) {
  const api = liveApi;
  if (!api || liveLayoutKey !== layoutKey) throw new Error('This session layout is not displayed');
  const snapshot = () => ({
    width: api.width, height: api.height,
    coordinate_space: 'viewport',
    active_pane_id: api.activePanel?.id ?? null,
    maximized_pane_id: useFocusModeStore.getState().paneId,
    available_views: WORKSPACE_TABS.filter(tab => !tab.hidden).map(tab => ({ tab_id: tab.id, label: tab.label })),
    panes: api.panels.map(panel => {
      const tabId = readPanelTabId(panel);
      const rect = panel.group.element.getBoundingClientRect();
      return {
        pane_id: panel.id, tab_id: tabId,
        label: WORKSPACE_TABS.find(tab => tab.id === tabId)?.label ?? 'Empty',
        x: Math.round(rect.left), y: Math.round(rect.top),
        width: panel.api.width, height: panel.api.height,
      };
    }),
  });
  if (command.action === 'inspect') return { ok: true, ...snapshot() };
  if (!['apply', 'undo'].includes(String(command.action))) throw new Error('Unknown layout action');
  const before = { layout: unfocusedLayout(api.toJSON()), focused: useFocusModeStore.getState().paneId };
  const restoreFocus = (paneId: string | null) => flushSync(() => useFocusModeStore.getState().setPaneId(paneId, { animate: false }));
  try {
    if (command.action === 'undo') {
      const previous = layoutUndo.get(layoutKey);
      if (!previous) throw new Error('No previous agent layout change to undo');
      restoreFocus(null);
      api.fromJSON(previous.layout);
      restoreFocus(previous.focused);
      layoutUndo.delete(layoutKey);
    } else {
      if (!Array.isArray(command.operations) || command.operations.length < 1 || command.operations.length > 20) throw new Error('Supply 1–20 operations');
      for (const raw of command.operations) {
        if (!raw || typeof raw !== 'object') throw new Error('Invalid layout operation');
        const op = raw as Record<string, unknown>;
        const kind = String(op.operation);
        if (!['open', 'move', 'resize', 'close', 'focus', 'maximize', 'restore'].includes(kind)) throw new Error('Unknown layout operation');
        const panel = typeof op.pane_id === 'string' ? api.getPanel(op.pane_id) : undefined;
        if (!['open', 'restore'].includes(kind) && !panel) throw new Error('Unknown pane_id; inspect the layout again');
        const direction = op.direction ?? 'right';
        if (!['left', 'right', 'above', 'below'].includes(String(direction))) throw new Error('Invalid direction');
        for (const [dimension, minimum] of [['width', 160], ['height', 120]] as const) {
          const value = op[dimension];
          if (value !== undefined && (!Number.isInteger(value) || Number(value) < minimum || Number(value) > 20000)) throw new Error(`Invalid ${dimension}`);
        }
        const reference = typeof op.reference_pane_id === 'string' ? api.getPanel(op.reference_pane_id) : undefined;
        if ((op.reference_pane_id !== undefined || kind === 'move') && !reference) throw new Error('Unknown reference_pane_id');
        if (['open', 'move', 'resize', 'close', 'restore'].includes(kind)) {
          restoreFocus(null);
          if (api.hasMaximizedGroup()) api.exitMaximizedGroup();
        }
        let target = panel;
        if (kind === 'open') {
          if (typeof op.tab_id !== 'string' || !isWorkspaceTabId(op.tab_id)) throw new Error('Unknown tab_id; use available_views');
          const id = useWorkspaceStore.getState().dropTab(op.tab_id, reference?.id ?? null, direction === 'above' ? 'top' : direction === 'below' ? 'bottom' : direction as 'left' | 'right');
          if (!id) throw new Error('Unable to open pane');
          target = api.getPanel(id);
        } else if (kind === 'move') {
          if (panel!.id === reference!.id) throw new Error('Cannot move a pane beside itself');
          panel!.api.moveTo({ group: reference!.group, position: direction === 'above' ? 'top' : direction === 'below' ? 'bottom' : direction as 'left' | 'right' });
        } else if (kind === 'close') {
          api.removePanel(panel!);
        } else if (kind === 'focus') {
          if (useFocusModeStore.getState().paneId && useFocusModeStore.getState().paneId !== panel!.id) restoreFocus(null);
          panel!.api.setActive();
        } else if (kind === 'maximize') {
          panel!.api.setActive();
          restoreFocus(panel!.id);
        }
        if (kind === 'resize' && op.width === undefined && op.height === undefined) throw new Error('Resize requires width or height');
        if (target && ['open', 'move', 'resize'].includes(kind) && (op.width !== undefined || op.height !== undefined)) {
          target.api.setSize({ width: op.width as number | undefined, height: op.height as number | undefined });
        }
      }
      layoutUndo.set(layoutKey, before);
    }
    useWorkspaceStore.getState().syncFromApi();
    return { ok: true, ...snapshot() };
  } catch (error) {
    restoreFocus(null);
    api.fromJSON(before.layout);
    restoreFocus(before.focused);
    useWorkspaceStore.getState().syncFromApi();
    throw error;
  }
}
const pendingTabRequests = new Map<string, WorkspaceTabId>();

/** Open in the requested layout, including when switching sessions remounts Dockview. */
export function requestWorkspaceTab(layoutKey: string, tabId: WorkspaceTabId): void {
  if (liveApi && liveLayoutKey === layoutKey) {
    useWorkspaceStore.getState().openTab(tabId);
  } else {
    pendingTabRequests.set(layoutKey, tabId);
  }
}

export function sessionLayoutKey(instanceName: string | undefined, sessionId: string | null): string {
  return JSON.stringify([instanceName ?? null, sessionId]);
}

/** Build a stable, unique pane id for a freshly opened tab. */
function makePaneId(tabId: WorkspaceTabId): string {
  return `pane-${tabId}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Read `params.tabId` off a dockview panel, validated against the registry. */
function readPanelTabId(panel: { params?: Record<string, unknown> }): WorkspaceTabId | null {
  const raw = panel.params?.tabId;
  if (typeof raw === 'string' && isWorkspaceTabId(raw)) {
    return raw;
  }
  return null;
}

/** Rebuild the open-tab map from the panels currently held by the live api. */
function computeOpenTabs(api: DockviewApi): Partial<Record<WorkspaceTabId, string>> {
  const next: Partial<Record<WorkspaceTabId, string>> = {};
  for (const panel of api.panels) {
    const tabId = readPanelTabId(panel);
    if (tabId && !next[tabId]) {
      next[tabId] = panel.id;
    }
  }
  return next;
}

/**
 * Map our SplitDirection onto dockview's positional AddPanelOptions. A
 * directional add (left/right/above/below) relative to a panel always lands the
 * new panel in its own new group. `'within'` is the only stacking direction, so
 * we coerce it to `'right'` to keep the one-view-per-pane invariant.
 */
function toAddPanelPosition(
  referencePanel: string,
  direction: SplitDirection,
): AddPanelOptions['position'] {
  const positional: Direction = direction === 'within' ? 'right' : (direction as Direction);
  return {
    referencePanel,
    direction: positional,
  };
}

/**
 * Resolve the pane the launcher should replace: the active panel, else the
 * active group's active panel, else the last panel. `undefined` when empty.
 */
function resolveActivePane(api: DockviewApi): string | undefined {
  const active = api.activePanel;
  if (active) return active.id;
  const groupActive = api.activeGroup?.activePanel;
  if (groupActive) return groupActive.id;
  const panels = api.panels;
  return panels.length > 0 ? panels[panels.length - 1].id : undefined;
}

function terminalSplit(width: number, height: number) {
  // Keep a useful line width and enough rows in both halves of the split.
  const right = Math.min(width / 2 / 480, height / 300);
  const below = Math.min(width / 480, height / 2 / 300);
  return right >= below
    ? { direction: 'right' as const, score: right, width, height }
    : { direction: 'below' as const, score: below, width, height };
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      sessionLayouts: {},
      layout: null,
      openTabs: {},

      bindApi: (api, layoutKey, restore = true) => {
        // Capture any pending resize before replacing the live session's API.
        if (liveApi) get().syncFromApi();
        liveApi = api;
        liveLayoutKey = api ? layoutKey : null;
        if (!api) {
          return () => {};
        }
        // Restore before subscribing or writing: the new API starts empty.
        const saved = get().sessionLayouts[layoutKey];
        let restored = false;
        if (restore && saved) {
          try {
            // Also repair layouts saved while focus mode hid their headers.
            api.fromJSON(unfocusedLayout(saved));
            restored = true;
          } catch {
            api.clear();
          }
        }
        if (restore && !restored) {
          api.addPanel({
            id: makePaneId('sessions'),
            component: WORKSPACE_PANEL_COMPONENT,
            params: { tabId: 'sessions' },
          });
        }
        const sync = () => {
          if (liveApi === api) get().syncFromApi();
        };
        const disposable = api.onDidLayoutChange(sync);
        window.addEventListener('pagehide', sync);
        sync();
        const requestedTab = pendingTabRequests.get(layoutKey);
        if (requestedTab) {
          pendingTabRequests.delete(layoutKey);
          get().openTab(requestedTab);
        }
        return () => {
          // Dockview batches layout events. Capture the latest size/position
          // before its React component disposes the panels.
          sync();
          disposable.dispose();
          window.removeEventListener('pagehide', sync);
          if (liveApi === api) { liveApi = null; liveLayoutKey = null; }
        };
      },

      syncFromApi: () => {
        if (!liveApi || liveLayoutKey === null) return;
        // Normalize the live tree as well as the saved tree. This also recovers
        // legacy vacant groups immediately after bindApi restores the layout.
        removeVacantGroups(liveApi);
        const layout = unfocusedLayout(liveApi.toJSON());
        set({
          layout,
          sessionLayouts: { ...get().sessionLayouts, [liveLayoutKey]: layout },
          openTabs: computeOpenTabs(liveApi),
        });
      },

      openTab: (tabId) => {
        if (!liveApi) return null;

        // Already open somewhere: jump to it. Never duplicate or move.
        const existingPaneId = get().openTabs[tabId];
        if (existingPaneId) {
          const panel = liveApi.getPanel(existingPaneId);
          if (panel) {
            panel.api.setActive();
            return existingPaneId;
          }
        }

        const params: WorkspacePaneParams = { tabId };

        // Empty workspace: create the first pane (its own group).
        const activePaneId = resolveActivePane(liveApi);
        if (!activePaneId) {
          const paneId = makePaneId(tabId);
          liveApi.addPanel({
            id: paneId,
            component: WORKSPACE_PANEL_COMPONENT,
            params,
          });
          get().syncFromApi();
          return paneId;
        }

        // Replace the active pane's content in place: swap params, no new panel.
        const activePane = liveApi.getPanel(activePaneId);
        if (!activePane) return null;
        activePane.api.updateParameters(params);
        activePane.api.setActive();
        get().syncFromApi();
        return activePaneId;
      },

      openTerminalPane: (referencePaneId) => {
        if (!liveApi) return null;
        // Restore the real pane dimensions before choosing a split. Otherwise a
        // maximized chat can hide the terminal or make a tiny pane look roomy.
        if (useFocusModeStore.getState().paneId) {
          flushSync(() => useFocusModeStore.getState().setPaneId(null, { animate: false }));
        }
        if (liveApi.hasMaximizedGroup()) liveApi.exitMaximizedGroup();

        const existing = liveApi.panels.find(panel => readPanelTabId(panel) === 'terminal');
        if (existing) {
          existing.api.setActive();
          get().syncFromApi();
          return existing.id;
        }
        const empty = liveApi.panels.find(panel => readPanelTabId(panel) === null);
        if (empty) {
          empty.api.updateParameters({ tabId: 'terminal' } satisfies WorkspacePaneParams);
          empty.api.setActive();
          get().syncFromApi();
          return empty.id;
        }

        const reference = referencePaneId ?? get().openTabs.sessions ?? resolveActivePane(liveApi);
        const candidates = liveApi.panels.map(panel => ({
          panel, ...terminalSplit(panel.api.width, panel.api.height),
        }));
        // Prefer the clicked chat when it has room; otherwise use a roomier pane.
        const adjacent = candidates.find(candidate => candidate.panel.id === reference && candidate.score >= 1)
          ?? candidates.filter(candidate => candidate.score >= 1).sort((a, b) => b.score - a.score)[0];
        const chatOnly = liveApi.panels.length === 1 && readPanelTabId(liveApi.panels[0]) === 'sessions';
        const placement = chatOnly
          ? { direction: 'left' as const, width: liveApi.width, height: liveApi.height }
          : adjacent ?? terminalSplit(liveApi.width, liveApi.height);
        const panel = liveApi.addPanel({
          id: makePaneId('terminal'),
          component: WORKSPACE_PANEL_COMPONENT,
          params: { tabId: 'terminal' } satisfies WorkspacePaneParams,
          // If all panes are cramped, split the whole workspace along its more
          // useful axis instead of subdividing one already-small pane again.
          position: liveApi.panels.length ? {
            direction: placement.direction,
            ...(adjacent ? { referencePanel: adjacent.panel.id } : {}),
          } : undefined,
          ...((placement.direction === 'right' || placement.direction === 'left')
            ? { initialWidth: Math.round(placement.width / 2) }
            : { initialHeight: Math.round(placement.height / 2) }),
        });
        panel.api.setActive();
        get().syncFromApi();
        return panel.id;
      },

      setPaneTab: (paneId, tabId) => {
        if (!liveApi) return false;
        const owner = get().openTabs[tabId];
        if (owner && owner !== paneId) {
          return false;
        }
        const panel = liveApi.getPanel(paneId);
        if (!panel) return false;
        const params: WorkspacePaneParams = { tabId };
        panel.api.updateParameters(params);
        get().syncFromApi();
        return true;
      },

      closePane: (paneId) => {
        if (!liveApi) return;
        const panel = liveApi.getPanel(paneId);
        if (!panel) return;
        liveApi.removePanel(panel);
        get().syncFromApi();
      },

      splitPane: (paneId, tabId, direction) => {
        if (!liveApi) return null;
        const owner = get().openTabs[tabId];
        if (owner) {
          return null;
        }
        const reference = liveApi.getPanel(paneId);
        if (!reference) return null;
        const newPaneId = makePaneId(tabId);
        const params: WorkspacePaneParams = { tabId };
        liveApi.addPanel({
          id: newPaneId,
          component: WORKSPACE_PANEL_COMPONENT,
          params,
          position: toAddPanelPosition(paneId, direction),
        });
        get().syncFromApi();
        return newPaneId;
      },

      dropTab: (tabId, referencePaneId, position) => {
        if (!liveApi) return null;
        // With no reference, a center drop simply opens the first pane.
        const splitPos: Position = position === 'center' ? 'right' : position;
        const refPaneId = referencePaneId ?? resolveActivePane(liveApi) ?? null;
        const reference = refPaneId ? liveApi.getPanel(refPaneId) : undefined;

        if (position === 'center' && reference) {
          const existingId = get().openTabs[tabId];
          if (existingId === reference.id) { reference.api.setActive(); return reference.id; }
          if (existingId) {
            const existing = liveApi.getPanel(existingId);
            if (!existing) return null;
            // Move the live view into the destination before removing its old view.
            existing.api.moveTo({ group: reference.api.group, position: 'center' });
            liveApi.removePanel(reference);
            existing.api.setActive();
            get().syncFromApi();
            return existing.id;
          }
          reference.api.updateParameters({ tabId } satisfies WorkspacePaneParams);
          reference.api.setActive();
          get().syncFromApi();
          return reference.id;
        }

        // Already open: move its pane to the drop spot rather than duplicating.
        const existingPaneId = get().openTabs[tabId];
        if (existingPaneId) {
          const existing = liveApi.getPanel(existingPaneId);
          if (!existing) return null;
          if (reference && refPaneId !== existingPaneId) {
            existing.api.moveTo({ group: reference.api.group, position: splitPos });
          }
          existing.api.setActive();
          get().syncFromApi();
          return existingPaneId;
        }

        // New view: its own pane at the drop spot (or the first pane when empty).
        const params: WorkspacePaneParams = { tabId };
        const newPaneId = makePaneId(tabId);
        liveApi.addPanel({
          id: newPaneId,
          component: WORKSPACE_PANEL_COMPONENT,
          params,
          position: reference
            ? { referencePanel: refPaneId as string, direction: positionToDirection(splitPos) }
            : undefined,
        });
        get().syncFromApi();
        return newPaneId;
      },

      resetWorkspace: () => {
        if (liveApi) {
          liveApi.clear();
          get().syncFromApi();
        } else {
          set({ layout: null, openTabs: {} });
        }
      },
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      // v2 introduced one-view-per-pane; drop any pre-v2 layout (it may contain
      // stacked tab groups) so it doesn't render stacked after the upgrade.
      version: 2,
      migrate: (persisted, fromVersion) => {
        if (fromVersion < 2) return { layout: null, openTabs: {} };
        return persisted as { layout: SerializedDockview | null; openTabs: Partial<Record<WorkspaceTabId, string>> };
      },
      // The active layout and open-tab map are rebuilt from its session on bind.
      partialize: (state) => ({
        sessionLayouts: state.sessionLayouts,
      }),
    },
  ),
);

// --- selectors ---------------------------------------------------------------

/**
 * Hook: returns the set of tab ids currently open in any pane. Pickers use this
 * to disable tabs that would violate the at-most-once rule.
 */
export function useOpenTabIds(): WorkspaceTabId[] {
  return useWorkspaceStore(
    useShallow((state) => WORKSPACE_TAB_IDS.filter((id) => Boolean(state.openTabs[id]))),
  );
}

/** Hook: is a specific tab currently open in any pane. */
export function useIsTabOpen(tabId: WorkspaceTabId): boolean {
  return useWorkspaceStore((state) => Boolean(state.openTabs[tabId]));
}

/** Hook: the pane id hosting `tabId`, or undefined when closed. */
export function usePaneIdForTab(tabId: WorkspaceTabId): string | undefined {
  return useWorkspaceStore((state) => state.openTabs[tabId]);
}

/** Hook: the persisted serialized layout (null before first use). */
export function useWorkspaceLayout(): SerializedDockview | null {
  return useWorkspaceStore((state) => state.layout);
}

/** Non-reactive read of the full open-tab map (for imperative call sites). */
export function getOpenTabsSnapshot(): Partial<Record<WorkspaceTabId, string>> {
  return useWorkspaceStore.getState().openTabs;
}
