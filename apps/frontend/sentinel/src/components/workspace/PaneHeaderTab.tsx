import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type SyntheticEvent,
} from 'react';
import { createPortal } from 'react-dom';
import type { IDockviewPanelHeaderProps } from 'dockview-react';
import {
  ChevronDown,
  Check,
  SplitSquareHorizontal,
  SplitSquareVertical,
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
  event.stopPropagation();
  event.preventDefault();
}

const DRAG_BLOCKERS = {
  onPointerDown: blockDrag,
  onMouseDown: blockDrag,
} as const;

interface MenuRect {
  top: number;
  left: number;
  /** Width of the trigger, so the menu can size/align relative to it. */
  triggerWidth: number;
}

/**
 * Track the viewport rect of a trigger element while `open`, recomputing on
 * open and on scroll/resize so a menu portaled to `document.body` (position:
 * fixed) stays glued to its trigger. Returns null while closed.
 */
function useAnchorRect(
  triggerRef: React.RefObject<HTMLElement | null>,
  open: boolean,
): MenuRect | null {
  const [rect, setRect] = useState<MenuRect | null>(null);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect({ top: r.bottom, left: r.left, triggerWidth: r.width });
  }, [triggerRef]);

  useLayoutEffect(() => {
    if (!open) {
      setRect(null);
      return;
    }
    measure();
    // Capture-phase scroll catches nested scroll containers (e.g. a pane body).
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [open, measure]);

  return rect;
}

/**
 * Close `open` when a pointer goes down outside both the trigger and the
 * portaled menu. Both refs are checked because the menu lives in document.body,
 * outside the trigger's DOM subtree.
 */
function useDismissOnOutside(
  open: boolean,
  setOpen: (value: boolean) => void,
  triggerRef: React.RefObject<HTMLElement | null>,
  menuRef: React.RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, setOpen, triggerRef, menuRef]);
}

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
      ? 'flex items-center gap-2 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 py-2 text-sm font-medium text-[color:var(--text-primary)] hover:border-[color:var(--border-strong)] hover:bg-[color:var(--surface-2)] transition-colors'
      : 'flex max-w-[12rem] items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors';

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
            <activeTab.icon size={15} className="shrink-0 text-[color:var(--text-muted)]" />
            <span className="truncate">{activeTab.label}</span>
          </>
        ) : (
          <span className="truncate text-[color:var(--text-secondary)]">Choose a tab…</span>
        )}
        <ChevronDown size={14} className="shrink-0 text-[color:var(--text-muted)]" />
      </button>

      {open &&
        rect &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            style={{ position: 'fixed', top: rect.top + 4, left: rect.left, zIndex: 1000 }}
            className="max-h-80 w-56 overflow-y-auto rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] py-1 shadow-lg shadow-black/20"
          >
            {WORKSPACE_TABS.map((tab) => {
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
                      ? 'cursor-not-allowed text-[color:var(--text-muted)] opacity-50'
                      : isCurrent
                        ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                        : 'text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
                  }`}
                >
                  <tab.icon size={15} className="shrink-0" />
                  <span className="flex-1 truncate">{tab.label}</span>
                  {isCurrent && <Check size={14} className="shrink-0 text-[color:var(--text-primary)]" />}
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

const SPLIT_OPTIONS: { direction: SplitDirection; label: string }[] = [
  { direction: 'right', label: 'Split right' },
  { direction: 'below', label: 'Split down' },
  { direction: 'left', label: 'Split left' },
  { direction: 'above', label: 'Split up' },
];

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
  const availableTabs = WORKSPACE_TABS.filter((tab) => !openTabIds.includes(tab.id));
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
            ? 'text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)]'
            : 'cursor-not-allowed text-[color:var(--text-muted)] opacity-40'
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
            role="menu"
            style={{
              position: 'fixed',
              top: rect.top + 4,
              // Right-align the menu under the trigger's right edge.
              left: Math.max(8, rect.left + rect.triggerWidth - MENU_WIDTH),
              zIndex: 1000,
            }}
            className="w-56 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] py-1 shadow-lg shadow-black/20"
          >
            <div className="flex items-center gap-1 border-b border-[color:var(--border-subtle)] px-2 pb-1.5 pt-1">
              {SPLIT_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.direction}
                  onClick={() => setDirection(option.direction)}
                  title={option.label}
                  className={`flex flex-1 items-center justify-center rounded p-1.5 transition-colors ${
                    direction === option.direction
                      ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                      : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
                  }`}
                >
                  {option.direction === 'right' || option.direction === 'left' ? (
                    <SplitSquareHorizontal
                      size={15}
                      style={
                        option.direction === 'left'
                          ? ({ transform: 'scaleX(-1)' } as CSSProperties)
                          : undefined
                      }
                    />
                  ) : (
                    <SplitSquareVertical
                      size={15}
                      style={
                        option.direction === 'above'
                          ? ({ transform: 'scaleY(-1)' } as CSSProperties)
                          : undefined
                      }
                    />
                  )}
                </button>
              ))}
            </div>
            <div className="max-h-64 overflow-y-auto py-1">
              <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-[color:var(--text-muted)]">
                Open in new pane
              </p>
              {availableTabs.map((tab) => (
                <button
                  type="button"
                  key={tab.id}
                  onClick={() => handleSplit(tab.id)}
                  className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm text-[color:var(--text-secondary)] transition-colors hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]"
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
  const closePane = useWorkspaceStore((state) => state.closePane);

  return (
    <div className="flex h-full w-full items-center justify-between gap-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2 text-[color:var(--text-primary)]">
      <div className="flex min-w-0 items-center gap-1">
        <TabPicker paneId={paneId} activeTabId={tabId} variant="header" />
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <SplitMenu paneId={paneId} />
        <button
          type="button"
          onClick={() => closePane(paneId)}
          {...DRAG_BLOCKERS}
          title="Close pane"
          className="rounded-md p-1.5 text-[color:var(--text-primary)] transition-colors hover:bg-[color:var(--surface-2)]"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}
