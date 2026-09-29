import '../session/chat-header.css';
import { useContext, useRef, useState, type SyntheticEvent } from 'react';
import { createPortal } from 'react-dom';
import type { IDockviewPanelHeaderProps } from 'dockview-react';
import {
  ChevronDown,
  Check,
  SplitSquareHorizontal,
  Maximize2,
  Minimize2,
  PictureInPicture2,
  X,
} from 'lucide-react';

import {
  WORKSPACE_TABS,
  getWorkspaceTab,
  isWorkspaceTabId,
  type WorkspaceTabId,
} from '../../lib/workspace-tabs';
import {
  useWorkspaceStore,
  useOpenTabIds,
  type WorkspacePaneParams,
  type SplitDirection,
} from '../../store/workspace-store';
import { useFocusModeStore } from '../../store/focus-mode-store';
import { RetainedSessionContext, WorkspaceInstanceContext } from '../../lib/workspace-context-values';
import { PaneActions } from './PaneActions';
import { usePaneActions } from '../../store/pane-actions-store';
import { useAnchorRect, useDismissOnOutside } from '../../lib/portal-menu';

/** Read the current `tabId` off the panel params, validated against the registry. */
function readTabId(params: Partial<WorkspacePaneParams> | undefined): WorkspaceTabId | null {
  const raw = params?.tabId;
  if (typeof raw === 'string' && isWorkspaceTabId(raw)) {
    return raw;
  }
  return null;
}

/**
 * Stop a pointer/mouse interaction on a header control from arming a pane drag.
 * dockview's tab is draggable via two backends — native HTML5 (`dragstart`,
 * blocked by `preventDefault` on pointerdown) and a pointer drag source (blocked
 * by `stopPropagation` so the tab element never sees the event). The built-in
 * close button does the same `preventDefault`; we add `stopPropagation` to also
 * neutralise the pointer backend.
 */
function blockDrag(event: SyntheticEvent) {
  // React portals bubble through this header, but their inputs must retain normal focus.
  if (!event.currentTarget.contains(event.target as Node)) return;
  event.stopPropagation();
  event.preventDefault();
}

const DRAG_BLOCKERS = {
  onPointerDown: blockDrag,
  onMouseDown: blockDrag,
} as const;

export interface TabPickerProps {
  paneId: string;
  /** Currently selected tab in this pane (null for a fresh/empty pane). */
  activeTabId: WorkspaceTabId | null;
  /** Compact prompt styling for an empty pane vs. the inline header trigger. */
  variant: 'header' | 'empty';
}

/**
 * Dropdown listing every workspace tab. Tabs already open in another pane are
 * disabled to enforce the store's at-most-once rule; the tab hosted by this pane
 * stays selectable so it reads as the current value. The menu is portaled to
 * document.body so it paints above sibling panes.
 */
export function TabPicker({ paneId, activeTabId, variant }: TabPickerProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const setPaneTab = useWorkspaceStore((state) => state.setPaneTab);
  const openTabIds = useOpenTabIds();

  const rect = useAnchorRect(triggerRef, open);
  useDismissOnOutside(open, setOpen, triggerRef, menuRef);

  const activeTab = activeTabId ? getWorkspaceTab(activeTabId) : undefined;

  const handleSelect = (tabId: WorkspaceTabId) => {
    if (tabId !== activeTabId) {
      setPaneTab(paneId, tabId);
    }
    setOpen(false);
  };

  const triggerClass =
    variant === 'empty'
      ? 'flex items-center gap-2 rounded-md border border-(--border-subtle) bg-(--surface-1) px-3 py-2 text-sm font-medium text-(--text-primary) hover:border-(--border-strong) hover:bg-(--surface-2) transition-colors'
      : 'flex max-w-48 items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-(--text-primary) hover:bg-(--surface-2) transition-colors';

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        {...DRAG_BLOCKERS}
        className={triggerClass}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={variant === 'empty' ? 'Choose a tab for this pane' : 'Switch this pane’s view'}
      >
        {activeTab ? (
          <>
            <activeTab.icon size={15} className="shrink-0 text-(--text-muted)" />
            <span className="truncate">{activeTab.label}</span>
          </>
        ) : (
          <span className="truncate text-(--text-secondary)">Choose a tab…</span>
        )}
        <ChevronDown size={14} className="shrink-0 text-(--text-muted)" />
      </button>

      {open &&
        rect &&
        createPortal(
          <div
            ref={menuRef}
            data-pane-menu
            role="listbox"
            style={{ position: 'fixed', top: rect.top + 4, left: rect.left, zIndex: 10000 }}
            className="max-h-80 w-56 overflow-y-auto rounded-md border border-(--border-subtle) bg-(--surface-0) py-1 shadow-lg shadow-black/20"
          >
            {WORKSPACE_TABS.filter(tab => !tab.hidden).map((tab) => {
              const isCurrent = tab.id === activeTabId;
              // Disabled when open in a *different* pane (at-most-once).
              const disabled = !isCurrent && openTabIds.includes(tab.id);
              return (
                <button
                  type="button"
                  key={tab.id}
                  role="option"
                  aria-selected={isCurrent}
                  disabled={disabled}
                  onClick={() => handleSelect(tab.id)}
                  className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm transition-colors ${
                    disabled
                      ? 'cursor-not-allowed text-(--text-muted) opacity-50'
                      : isCurrent
                        ? 'bg-(--surface-accent) text-(--text-primary)'
                        : 'text-(--text-secondary) hover:bg-(--surface-1) hover:text-(--text-primary)'
                  }`}
                >
                  <tab.icon size={15} className="shrink-0" />
                  <span className="flex-1 truncate">{tab.label}</span>
                  {isCurrent && <Check size={14} className="shrink-0 text-(--text-primary)" />}
                  {disabled && (
                    <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide">
                      Open
                    </span>
                  )}
                </button>
              );
            })}
          </div>,
          document.body,
        )}
    </>
  );
}

interface SplitMenuProps {
  paneId: string;
}

type EdgeDirection = 'left' | 'right' | 'above' | 'below';

const SPLIT_OPTIONS: { direction: EdgeDirection; label: string }[] = [
  { direction: 'right', label: 'Split right' },
  { direction: 'below', label: 'Split down' },
  { direction: 'left', label: 'Split left' },
  { direction: 'above', label: 'Split up' },
];

// Half of the pane (the side the new pane will land on) drawn as a filled path,
// so the rounded outer rect + divider read as "panel goes here".
const SPLIT_FILL_PATHS: Record<EdgeDirection, string> = {
  right: 'M12 3 H19 a2 2 0 0 1 2 2 V19 a2 2 0 0 1 -2 2 H12 Z',
  left: 'M12 3 H5 a2 2 0 0 0 -2 2 V19 a2 2 0 0 0 2 2 H12 Z',
  below: 'M3 12 V19 a2 2 0 0 0 2 2 H19 a2 2 0 0 0 2 -2 V12 Z',
  above: 'M3 12 V5 a2 2 0 0 1 2 -2 H19 a2 2 0 0 1 2 2 V12 Z',
};

function SplitDirectionIcon({ direction, size = 15 }: { direction: EdgeDirection; size?: number }) {
  const divider = direction === 'left' || direction === 'right' ? 'M12 3 V21' : 'M3 12 H21';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={SPLIT_FILL_PATHS[direction]} fill="currentColor" stroke="none" />
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d={divider} />
    </svg>
  );
}

/**
 * Split control: opens a new pane in the chosen direction hosting a tab that is
 * not already open elsewhere (the store rejects duplicates). The menu is
 * portaled to document.body so it paints above sibling panes; it is right-
 * aligned under the trigger.
 */
function SplitMenu({ paneId }: SplitMenuProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const splitPane = useWorkspaceStore((state) => state.splitPane);
  const openTabIds = useOpenTabIds();

  // Tabs not yet open anywhere are eligible to seed a new pane.
  const availableTabs = WORKSPACE_TABS.filter((tab) => !tab.hidden && !openTabIds.includes(tab.id));
  const canSplit = availableTabs.length > 0;

  const rect = useAnchorRect(triggerRef, open);
  useDismissOnOutside(open, setOpen, triggerRef, menuRef);

  const [direction, setDirection] = useState<SplitDirection>('right');

  const handleSplit = (tabId: WorkspaceTabId) => {
    splitPane(paneId, tabId, direction);
    setOpen(false);
  };

  const MENU_WIDTH = 224; // w-56

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => canSplit && setOpen((value) => !value)}
        {...DRAG_BLOCKERS}
        disabled={!canSplit}
        title={canSplit ? 'Split pane' : 'All tabs are already open'}
        className={`rounded-md p-1.5 transition-colors ${
          canSplit
            ? 'text-(--text-primary) hover:bg-(--surface-2)'
            : 'cursor-not-allowed text-(--text-muted) opacity-40'
        }`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <SplitSquareHorizontal size={16} />
      </button>

      {open &&
        rect &&
        createPortal(
          <div
            ref={menuRef}
            data-pane-menu
            role="menu"
            style={{
              position: 'fixed',
              top: rect.top + 4,
              // Right-align the menu under the trigger's right edge.
              left: Math.max(8, rect.left + rect.triggerWidth - MENU_WIDTH),
              zIndex: 10000,
            }}
            className="w-56 rounded-md border border-(--border-subtle) bg-(--surface-0) py-1 shadow-lg shadow-black/20"
          >
            <div className="flex items-center gap-1 border-b border-(--border-subtle) px-2 pb-1.5 pt-1">
              {SPLIT_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.direction}
                  onClick={() => setDirection(option.direction)}
                  title={option.label}
                  className={`flex flex-1 items-center justify-center rounded p-1.5 transition-colors ${
                    direction === option.direction
                      ? 'bg-(--surface-accent) text-(--text-primary)'
                      : 'text-(--text-muted) hover:bg-(--surface-1) hover:text-(--text-primary)'
                  }`}
                >
                  <SplitDirectionIcon direction={option.direction} size={15} />
                </button>
              ))}
            </div>
            <div className="max-h-64 overflow-y-auto py-1">
              <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-(--text-muted)">
                Open in new pane
              </p>
              {availableTabs.map((tab) => (
                <button
                  type="button"
                  key={tab.id}
                  onClick={() => handleSplit(tab.id)}
                  className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm text-(--text-secondary) transition-colors hover:bg-(--surface-1) hover:text-(--text-primary)"
                >
                  <tab.icon size={15} className="shrink-0" />
                  <span className="flex-1 truncate">{tab.label}</span>
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

/**
 * Custom dockview tab, registered as `defaultTabComponent`. With
 * `singleTabMode="fullwidth"` this fills the group's tab strip, so it doubles as
 * the pane's header bar: dockview makes the tab element draggable, so dragging
 * the empty areas of this bar repositions the pane natively (the host's
 * `onWillShowOverlay` guard still blocks stacking, leaving only edge splits /
 * repositioning). Interactive controls call {@link blockDrag} on pointer/mouse
 * down so clicking them never starts a drag.
 *
 * dockview re-invokes this with fresh `params` whenever `updateParameters`
 * runs, so reading `params.tabId` reflects tab switches without extra state.
 */
export function PaneHeaderTab(props: IDockviewPanelHeaderProps<WorkspacePaneParams>) {
  const paneId = props.api.id;
  const tabId = readTabId(props.params);
  const tab = tabId ? getWorkspaceTab(tabId) : undefined;
  const closePane = useWorkspaceStore((state) => state.closePane);
  // The hosted page registers its header actions under this pane id (the page's
  // AppShell and this tab live in separate React trees, bridged by the store).
  const actions = usePaneActions(paneId);
  const focused = useFocusModeStore(state => state.paneId === paneId);
  const setFocusPane = useFocusModeStore(state => state.setPaneId);
  const instanceName = useContext(WorkspaceInstanceContext);
  const retained = useContext(RetainedSessionContext);
  const desktop = window.sentinelDesktop;
  const canPopOut = Boolean(desktop) && typeof desktop?.openPaneWindow === 'function';
  const popOut = () => {
    if (!desktop || !canPopOut || !tabId || !tab || !instanceName) return;
    void desktop.openPaneWindow({ instance: instanceName, session: retained?.sessionId ?? null, tabId, paneId, title: tab.label })
      .then(() => closePane(paneId))
      .catch(() => { /* the pane stays docked when the window can't open */ });
  };

  return (
    <div data-pane-header-id={paneId} data-tour-pane={tabId ?? undefined} className={`sentinel-pane-header ${tabId === 'sessions' ? 'chat-pane-header ' : ''}flex h-full w-full items-center gap-2 border-b border-(--border-subtle) bg-(--surface-0) px-2 text-(--text-primary)`}>
      <div className="flex min-w-0 shrink-0 items-center gap-1">
        <div className="pane-header-title flex max-w-48 items-center gap-1.5 px-2 py-1 text-sm font-medium">
          {tab && <tab.icon size={15} className="shrink-0 text-(--text-muted)" />}
          <span className={tab?.id === 'settings' ? 'truncate' : 'sr-only'}>{tab?.label ?? 'Empty pane'}</span>
        </div>
      </div>
      {actions != null && (
        <div {...DRAG_BLOCKERS} className="pane-header-actions flex min-w-8 flex-1 items-center overflow-hidden">
          <PaneActions>{actions}</PaneActions>
        </div>
      )}
      <div className={`pane-header-tools flex shrink-0 items-center gap-0.5 ${actions != null ? '' : 'ml-auto'}`}>
        {tabId && (
          <button
            type="button"
            onClick={() => setFocusPane(focused ? null : paneId)}
            {...DRAG_BLOCKERS}
            title={focused ? 'Exit focus mode (⌘F)' : 'Enter focus mode (⌘F)'}
            aria-label={focused ? 'Exit focus mode' : 'Enter focus mode'}
            aria-pressed={focused}
            className="rounded-md p-1.5 text-(--text-primary) transition-colors hover:bg-(--surface-2)"
          >
            {focused ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        )}
        {tabId && canPopOut && !focused && (
          <button
            type="button"
            onClick={popOut}
            {...DRAG_BLOCKERS}
            title="Open in new window"
            aria-label="Open in new window"
            className="rounded-md p-1.5 text-(--text-primary) transition-colors hover:bg-(--surface-2)"
          >
            <PictureInPicture2 size={16} />
          </button>
        )}
        {!focused && <SplitMenu paneId={paneId} />}
        <button
          type="button"
          onClick={() => closePane(paneId)}
          {...DRAG_BLOCKERS}
          title="Close pane"
          className="rounded-md p-1.5 text-(--text-primary) transition-colors hover:bg-(--surface-2)"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}
