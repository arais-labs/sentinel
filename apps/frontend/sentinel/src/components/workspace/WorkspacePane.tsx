import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import {
  ChevronDown,
  Check,
  SplitSquareHorizontal,
  SplitSquareVertical,
  X,
} from 'lucide-react';

import { WorkspaceProvider } from '../../lib/workspace-context';
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

/**
 * Carries the active instance name from the {@link Workspace} host down into the
 * dockview-rendered panes. Panes are mounted via React portals, so ordinary
 * context still propagates, but the route-derived `:instanceName` is not
 * available inside a pane — this context supplies it explicitly.
 */
export const WorkspaceInstanceContext = createContext<string | undefined>(undefined);

function useHostInstanceName(): string | undefined {
  return useContext(WorkspaceInstanceContext);
}

/** Read the current `tabId` off the panel params, validated against the registry. */
function readTabId(params: Partial<WorkspacePaneParams> | undefined): WorkspaceTabId | null {
  const raw = params?.tabId;
  if (typeof raw === 'string' && isWorkspaceTabId(raw)) {
    return raw;
  }
  return null;
}

interface TabPickerProps {
  paneId: string;
  /** Currently selected tab in this pane (null for a fresh/empty pane). */
  activeTabId: WorkspaceTabId | null;
  /** Compact prompt styling for an empty pane vs. the inline header trigger. */
  variant: 'header' | 'empty';
}

/**
 * Dropdown listing every workspace tab. Tabs already open in another pane are
 * disabled to enforce the store's at-most-once rule; the tab hosted by this pane
 * stays selectable so it reads as the current value.
 */
function TabPicker({ paneId, activeTabId, variant }: TabPickerProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const setPaneTab = useWorkspaceStore((state) => state.setPaneTab);
  const openTabIds = useOpenTabIds();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
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
  }, [open]);

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
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={triggerClass}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={variant === 'empty' ? 'Choose a tab for this pane' : 'Switch tab'}
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

      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-[calc(100%+4px)] z-50 max-h-80 w-56 overflow-y-auto rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] py-1 shadow-lg shadow-black/20"
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
        </div>
      )}
    </div>
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
 * not already open elsewhere (the store rejects duplicates). The icon hints
 * horizontal vs. vertical based on the most-common right/down splits.
 */
function SplitMenu({ paneId }: SplitMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const splitPane = useWorkspaceStore((state) => state.splitPane);
  const openTabIds = useOpenTabIds();

  // Tabs not yet open anywhere are eligible to seed a new pane.
  const availableTabs = WORKSPACE_TABS.filter((tab) => !openTabIds.includes(tab.id));
  const canSplit = availableTabs.length > 0;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
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
  }, [open]);

  const [direction, setDirection] = useState<SplitDirection>('right');

  const handleSplit = (tabId: WorkspaceTabId) => {
    splitPane(paneId, tabId, direction);
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => canSplit && setOpen((value) => !value)}
        disabled={!canSplit}
        title={canSplit ? 'Split pane' : 'All tabs are already open'}
        className={`rounded-md p-1.5 transition-colors ${
          canSplit
            ? 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)]'
            : 'cursor-not-allowed text-[color:var(--text-muted)] opacity-40'
        }`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <SplitSquareHorizontal size={16} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+4px)] z-50 w-56 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] py-1 shadow-lg shadow-black/20"
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
        </div>
      )}
    </div>
  );
}

/**
 * Renderer for a single dockview panel. dockview re-invokes this with fresh
 * `props.params` whenever `updateParameters` runs, so reading `props.params.tabId`
 * reflects tab switches without extra state.
 *
 * Layout: a fixed header bar (tab picker + split + close) above a scrollable body
 * that hosts the selected page wrapped in {@link WorkspaceProvider}. An empty pane
 * shows a centered picker prompting the user to pick a tab.
 */
export function WorkspacePane(props: IDockviewPanelProps<WorkspacePaneParams>) {
  const paneId = props.api.id;
  const tabId = readTabId(props.params);
  const instanceName = useHostInstanceName();

  const closePane = useWorkspaceStore((state) => state.closePane);

  const tab = tabId ? getWorkspaceTab(tabId) : undefined;
  const TabComponent = tab?.component;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-[color:var(--surface-0)] text-[color:var(--text-primary)]">
      {/* Header bar */}
      <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-2">
        <div className="flex min-w-0 items-center gap-1">
          <TabPicker paneId={paneId} activeTabId={tabId} variant="header" />
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <SplitMenu paneId={paneId} />
          <button
            type="button"
            onClick={() => closePane(paneId)}
            title="Close pane"
            className="rounded-md p-1.5 text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)]"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-[color:var(--app-bg)]">
        {TabComponent ? (
          <WorkspaceProvider instanceName={instanceName} workspaceMode>
            <TabComponent />
          </WorkspaceProvider>
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-8 text-center">
            <p className="text-sm text-[color:var(--text-secondary)]">
              This pane is empty. Choose a tab to display here.
            </p>
            <TabPicker paneId={paneId} activeTabId={null} variant="empty" />
          </div>
        )}
      </div>
    </div>
  );
}
