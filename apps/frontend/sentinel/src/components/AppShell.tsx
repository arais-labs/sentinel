import { PropsWithChildren, ReactNode, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  MessagesSquare,
  FolderCog,
  Database,
  Zap,
  MonitorPlay,
  Settings,
  GitPullRequest,
  Moon,
  Sun,
  Menu,
  X,
  Activity,
  LayoutGrid,
  CheckCircle,
  Lock,
  Globe,
  Terminal,
  Folder,
} from 'lucide-react';

import './navigation-menu.css';

import { APP_VERSION } from '../lib/env';
import { instancePrefixFromPath, instanceRouteFromPath } from '../lib/routes';
import { usePaneId, useWorkspaceMode } from '../lib/workspace-context';
import { isWorkspaceTabId } from '../lib/workspace-tabs';
import { openWorkspaceTab } from '../lib/workspace-navigation';
import { useOpenTabIds, useWorkspaceStore, WORKSPACE_DND_MIME } from '../store/workspace-store';
import { useThemeStore } from '../store/theme-store';
import { clearPaneActions, setPaneActions } from '../store/pane-actions-store';
import { SidebarInstanceSwitcher } from './SidebarInstanceSwitcher';
import { Logo } from './ui/Logo';

interface AppShellProps extends PropsWithChildren {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  contentClassName?: string;
  hideSidebar?: boolean;
  hideHeader?: boolean;
}

interface NavItem {
  label: string;
  route: string;
  icon: typeof MessagesSquare;
}

type NavIndicatorCallback = (item: NavItem) => boolean;

const navItems: NavItem[] = [
  { label: 'Chat', route: 'sessions', icon: MessagesSquare },
  { label: 'Desktop', route: 'desktop', icon: Globe },
  { label: 'Terminal', route: 'terminal', icon: Terminal },
  { label: 'Files', route: 'files', icon: Folder },
  { label: 'Pull requests', route: 'pull-requests', icon: GitPullRequest },
  { label: 'Session Logs', route: 'logs', icon: Activity },
  { label: 'Memory', route: 'memory', icon: Database },
  { label: 'Triggers', route: 'triggers', icon: Zap },
  { label: 'Modules', route: 'modules', icon: LayoutGrid },
  { label: 'Approvals', route: 'approvals', icon: CheckCircle },
  { label: 'Permissions', route: 'permissions', icon: Lock },
  ...(import.meta.env.DEV ? [{ label: 'Showcase', route: 'showcase', icon: MonitorPlay }] : []),
  { label: 'Workspaces', route: 'workspaces', icon: FolderCog },
  { label: 'Settings', route: 'settings', icon: Settings },
];

const navigationGroups = [
  { label: 'Workspace', routes: ['sessions', 'desktop', 'terminal', 'files', 'pull-requests'] },
  { label: 'Agent', routes: ['logs', 'memory', 'triggers', 'modules', 'approvals', 'permissions'] },
  { label: 'Manage', routes: ['showcase', 'workspaces', 'settings'] },
];

function isActive(pathname: string, candidate: string) {
  return pathname === candidate || pathname.startsWith(candidate + '/');
}

export function AppShell({
  title,
  subtitle,
  actions,
  children,
  contentClassName = '',
  hideSidebar = false,
  hideHeader = false,
}: AppShellProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const workspaceMode = useWorkspaceMode();
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  const openTab = useWorkspaceStore((state) => state.openTab);
  const openTabIds = useOpenTabIds();
  const activeWorkspaceTabId = useWorkspaceStore((state) => state.activeTabId);
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const navigationDialog = useRef<HTMLDivElement>(null);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!isMobileMenuOpen) return;
    const dialog = navigationDialog.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setIsMobileMenuOpen(false); }
      if (event.key !== 'Tab' || !dialog) return;
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input')).filter(el => el.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); navigationTrigger.current?.focus(); };
  }, [isMobileMenuOpen]);
  const paneId = usePaneId();
  const instanceMatch = location.pathname.match(/^\/instances\/([^/]+)/);
  const hasInstanceScope = Boolean(instanceMatch?.[1]);

  // In a tiling-workspace pane the page's header actions render inside the
  // pane's own tab ({@link PaneHeaderTab}), not in a second header bar here.
  // The tab lives in a separate React tree, so we bridge the `actions` node
  // through the pane-actions store keyed by `paneId`. The store is consumed by
  // the tab (a different component tree), so this write never re-renders this
  // AppShell — no update loop. Depend only on [paneId, actions]; clear on
  // unmount or when the pane id changes so a closed pane leaves no stale node.
  useEffect(() => {
    if (!workspaceMode || !paneId) return;
    setPaneActions(paneId, actions ?? null);
  }, [paneId, actions, workspaceMode]);
  useEffect(() => {
    if (!workspaceMode || !paneId) return;
    return () => clearPaneActions(paneId);
  }, [paneId, workspaceMode]);

  // When the shell is hosting the tiling workspace, the left nav acts as a tab
  // launcher: clicking an item opens/focuses that pane instead of navigating to
  // a standalone page. Detected by the dedicated `/workspace` route.
  const instancePrefix = instancePrefixFromPath(location.pathname);
  const workspaceRoute = instancePrefix ? `${instancePrefix}/workspace` : null;
  const launcherMode = Boolean(workspaceRoute) && location.pathname === workspaceRoute;

  // Pane destinations always enter the workspace, including from standalone
  // pages. Some panes have no standalone route (terminal, desktop, files).
  const handleNavClick = (item: NavItem, onNavigate?: () => void) => {
    if (instanceMatch?.[1] && isWorkspaceTabId(item.route)) {
      if (launcherMode) openTab(item.route);
      else openWorkspaceTab(navigate, decodeURIComponent(instanceMatch[1]), item.route);
      onNavigate?.();
      return;
    }
    navigate(instanceRouteFromPath(location.pathname, item.route));
    onNavigate?.();
  };

  const focusedPaneIndicator: NavIndicatorCallback = (item) =>
    launcherMode && item.route === activeWorkspaceTabId;

  const renderNav = (
    items: NavItem[],
    onNavigate?: () => void,
    expanded = isSidebarExpanded,
    showIndicator?: NavIndicatorCallback,
  ) => (
    <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2 space-y-1">
      {items.map((item) => {
        const itemPath = instanceRouteFromPath(location.pathname, item.route);
        // In launcher mode the "active" cue follows which tabs are open rather
        // than the URL (which stays on `/workspace`).
        const active = launcherMode
          ? isWorkspaceTabId(item.route) && openTabIds.includes(item.route)
          : isActive(location.pathname, itemPath);
        // In launcher mode each workspace-tab item is also a drag source: drag it
        // into the workspace to open (or move) that view as a pane at the drop spot.
        const dragSource = launcherMode && isWorkspaceTabId(item.route);
        return (
          <button
            key={item.route}
            data-tour-nav={item.route}
            onClick={() => handleNavClick(item, onNavigate)}
            draggable={dragSource}
            onDragStart={
              dragSource
                ? (event) => {
                    event.dataTransfer.setData(WORKSPACE_DND_MIME, item.route);
                    event.dataTransfer.effectAllowed = 'copy';
                  }
                : undefined
            }
            aria-current={active ? 'page' : undefined}
            title={dragSource ? `Drag to open ${item.label} in a new pane` : undefined}
            className={`group flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-[550] transition-colors ${
              active
                ? 'bg-(--surface-accent) text-(--text-primary)'
                : 'text-(--text-secondary) hover:bg-(--surface-1) hover:text-(--text-primary)'
            }`}
          >
            <item.icon
              size={18}
              className={`shrink-0 transition-colors ${active ? 'text-(--text-primary)' : 'text-(--text-secondary) group-hover:text-(--text-primary)'}`}
            />
            <span className={`transition-opacity duration-200 whitespace-nowrap ${expanded ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
              {item.label}
            </span>
            {showIndicator?.(item) && <span className="navigation-menu-indicator" aria-hidden="true" />}
          </button>
        );
      })}
    </nav>
  );

  // Inside a tiling workspace pane the global chrome (sidebar + header) is
  // already provided by the outer shell hosting <Workspace/>, and the pane
  // supplies its own header (tab picker / actions / split / close). The page's
  // `actions` are registered into the pane-actions store above and rendered by
  // the pane's tab, so here we render bare content with NO header bar.
  if (workspaceMode) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-(--app-bg) text-(--text-primary)">
        <main className={`flex-1 overflow-y-auto p-4 md:p-6 ${contentClassName}`}>
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-(--app-bg) text-(--text-primary)">
      {/* Sidebar Desktop */}
      <aside
        aria-hidden={hideSidebar}
        data-expanded={isSidebarExpanded}
        className={`desktop-navigation hidden md:flex flex-col overflow-hidden border-r bg-(--surface-0) transition-[width,opacity,border-color] duration-250 ease-out ${
          hideSidebar
            ? 'w-0 opacity-0 border-r-transparent pointer-events-none'
            : `${isSidebarExpanded ? 'w-64' : 'w-16'} opacity-100 border-r-(--border-subtle)`
        }`}
        onMouseEnter={() => setIsSidebarExpanded(true)}
        onMouseLeave={() => setIsSidebarExpanded(false)}
      >
        <div className="desktop-navigation-heading flex h-16 items-center px-4 overflow-hidden">
          <div className="flex items-center gap-3">
            <button type="button" aria-label="Back to instances" title="Instances" onClick={() => navigate('/desktop/instances')} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-(--accent-solid) text-(--app-bg)">
              <Logo size={27} />
            </button>
            <div className={`transition-opacity duration-200 ${isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
              <p className="font-bold text-sm tracking-tight">SENTINEL</p>
              <p className="text-[10px] text-(--text-muted) font-medium">v{APP_VERSION}</p>
            </div>
          </div>
        </div>

        {hasInstanceScope && <SidebarInstanceSwitcher expanded={isSidebarExpanded} />}

        <div className="desktop-navigation-scroll">
          {hasInstanceScope && navigationGroups.map(group => <section className="navigation-menu-section" key={group.label} aria-label={group.label} style={{ flexGrow: navItems.filter(item => group.routes.includes(item.route)).length }}>
            <h2>{group.label}</h2>
            {renderNav(navItems.filter(item => group.routes.includes(item.route)), undefined, isSidebarExpanded, focusedPaneIndicator)}
          </section>)}
        </div>

        <div className="desktop-navigation-footer p-2 border-t border-(--border-subtle)">
           <button
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-[550] text-(--text-secondary) hover:bg-(--surface-1) hover:text-(--text-primary) transition-colors"
          >
            {theme === 'dark' ? <Sun size={18} className="text-(--text-muted)" /> : <Moon size={18} className="text-(--text-muted)" />}
            <span className={`transition-opacity duration-200 whitespace-nowrap ${isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
              {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
            </span>
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Header */}
        <header
          aria-hidden={hideHeader}
          className={`flex shrink-0 items-center justify-between overflow-hidden bg-(--surface-0) gap-2 transition-[height,opacity,padding,border-color] duration-250 ease-out ${
            hideHeader
              ? 'h-0 opacity-0 px-0 md:px-0 border-b-0 pointer-events-none'
              : 'h-16 opacity-100 px-4 md:px-6 border-b border-(--border-subtle)'
          }`}
        >
          <div className="flex items-center gap-4 min-w-0">
            <button
              ref={navigationTrigger} aria-label="Open navigation" aria-expanded={isMobileMenuOpen} aria-haspopup="dialog"
              onClick={() => setIsMobileMenuOpen(true)}
              className="navigation-menu-trigger md:hidden"
            >
              <Menu size={20} />
            </button>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold truncate">{title}</h1>
              {subtitle && (
                <p className="text-[11px] text-(--text-muted) font-mono">{subtitle}</p>
              )}
            </div>
          </div>

          <div className="flex items-center justify-end gap-2">
            {actions}
          </div>
        </header>

        {/* Content */}
        <main className={`flex-1 overflow-y-auto p-4 md:p-6 ${contentClassName}`}>
          {children}
        </main>
      </div>

      {/* Mobile Menu Overlay */}
      {!hideSidebar && isMobileMenuOpen && (
        <div className="navigation-menu-overlay md:hidden">
          <div className="navigation-menu-backdrop" onClick={() => setIsMobileMenuOpen(false)} />
          <div ref={navigationDialog} className="navigation-menu" role="dialog" aria-modal="true" aria-label="Navigation">
            <header className="navigation-menu-heading">
              <button type="button" className="navigation-menu-brand" aria-label="Back to instances" onClick={() => navigate('/desktop/instances')}><Logo size={30} /><span>SENTINEL</span></button>
              <button aria-label="Close navigation" onClick={() => setIsMobileMenuOpen(false)} className="navigation-menu-close"><X size={18} /></button>
            </header>
            {hasInstanceScope && <SidebarInstanceSwitcher expanded />}
            <div className="navigation-menu-scroll">
              {hasInstanceScope && navigationGroups.map(group => <section className="navigation-menu-section" key={group.label} aria-label={group.label} style={{ flexGrow: navItems.filter(item => group.routes.includes(item.route)).length }}>
                <h2>{group.label}</h2>
                {renderNav(navItems.filter(item => group.routes.includes(item.route)), () => setIsMobileMenuOpen(false), true, focusedPaneIndicator)}
              </section>)}
            </div>
            <footer className="navigation-menu-footer">
              <button onClick={toggleTheme}>
                {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
                {theme === 'dark' ? 'Light mode' : 'Dark mode'}
              </button>
              <span>{APP_VERSION}</span>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
