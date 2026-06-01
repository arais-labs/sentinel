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

import {
  WORKSPACE_TAB_IDS,
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

/** Direction a split can target. Mirrors dockview's positional Direction. */
export type SplitDirection = 'left' | 'right' | 'above' | 'below' | 'within';

/** Params attached to every dockview panel created by the workspace. */
export interface WorkspacePaneParams {
  tabId: WorkspaceTabId;
}

export interface WorkspaceState {
  /**
   * Serialized dockview layout, persisted across reloads. `null` before the
   * first layout is created. Compatible with `DockviewApi.toJSON()/fromJSON()`.
   */
  layout: SerializedDockview | null;
  /**
   * Maps an open tab id to the pane (dockview panel) id that hosts it.
   * Source of truth for the at-most-once rule and duplicate-disable selectors.
   * Persisted so reloads can be reconciled against the restored layout.
   */
  openTabs: Partial<Record<WorkspaceTabId, string>>;

  // --- actions ---------------------------------------------------------------

  /** Register the live DockviewApi. Returns a disposer to call on unmount. */
  bindApi: (api: DockviewApi | null) => () => void;
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
   * coerced to a right split so panes never stack. Returns the hosting pane id.
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

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set, get) => ({
      layout: null,
      openTabs: {},

      bindApi: (api) => {
        liveApi = api;
        if (!api) {
          return () => {};
        }
        const disposable = api.onDidLayoutChange(() => {
          get().syncFromApi();
        });
        // Capture initial state immediately after binding.
        get().syncFromApi();
        return () => {
          disposable.dispose();
          if (liveApi === api) {
            liveApi = null;
          }
        };
      },

      syncFromApi: () => {
        if (!liveApi) return;
        set({
          layout: liveApi.toJSON(),
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
        // Never stack onto a pane: a center drop becomes a right split.
        const splitPos: Position = position === 'center' ? 'right' : position;
        const refPaneId = referencePaneId ?? resolveActivePane(liveApi) ?? null;
        const reference = refPaneId ? liveApi.getPanel(refPaneId) : undefined;

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
        }
        set({ layout: null, openTabs: {} });
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
      // Only persist the serializable layout + open-tab map; actions and the
      // live api are runtime-only.
      partialize: (state) => ({
        layout: state.layout,
        openTabs: state.openTabs,
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
