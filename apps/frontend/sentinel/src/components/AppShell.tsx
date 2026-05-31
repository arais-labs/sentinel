import { PropsWithChildren, ReactNode, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Database,
  Zap,
  Send,
  MonitorPlay,
  Settings,
  GitBranch,
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

import { APP_VERSION } from '../lib/env';
import { instancePrefixFromPath, instanceRouteFromPath } from '../lib/routes';
import { useWorkspaceMode } from '../lib/workspace-context';
import { isWorkspaceTabId } from '../lib/workspace-tabs';
import { useOpenTabIds, useWorkspaceStore } from '../store/workspace-store';
import { useThemeStore } from '../store/theme-store';
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
  icon: typeof LayoutDashboard;
}

const navItems: NavItem[] = [
  { label: 'Sessions', route: 'sessions', icon: LayoutDashboard },
  { label: 'Desktop', route: 'desktop', icon: Globe },
  { label: 'Terminal', route: 'terminal', icon: Terminal },
  { label: 'Files', route: 'files', icon: Folder },
  { label: 'Session Logs', route: 'logs', icon: Activity },
  { label: 'Memory', route: 'memory', icon: Database },
  { label: 'Triggers', route: 'triggers', icon: Zap },
  { label: 'Modules', route: 'modules', icon: LayoutGrid },
  { label: 'Approvals', route: 'approvals', icon: CheckCircle },
  { label: 'Permissions', route: 'permissions', icon: Lock },
  { label: 'Git', route: 'git', icon: GitBranch },
  { label: 'Telegram', route: 'telegram', icon: Send },
  ...(import.meta.env.DEV ? [{ label: 'Showcase', route: 'showcase', icon: MonitorPlay }] : []),
  { label: 'Settings', route: 'settings', icon: Settings },
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
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const instanceMatch = location.pathname.match(/^\/instances\/([^/]+)/);
  const hasInstanceScope = Boolean(instanceMatch?.[1]);

  // When the shell is hosting the tiling workspace, the left nav acts as a tab
  // launcher: clicking an item opens/focuses that pane instead of navigating to
  // a standalone page. Detected by the dedicated `/workspace` route.
  const instancePrefix = instancePrefixFromPath(location.pathname);
  const workspaceRoute = instancePrefix ? `${instancePrefix}/workspace` : null;
  const launcherMode = Boolean(workspaceRoute) && location.pathname === workspaceRoute;

  // A nav item launches a workspace tab when we are in launcher mode and the
  // item's route maps to a real workspace tab id (e.g. dev-only `showcase` has
  // no tab, so it always router-navigates).
  const handleNavClick = (item: NavItem, onNavigate?: () => void) => {
    if (launcherMode && isWorkspaceTabId(item.route)) {
      openTab(item.route);
      onNavigate?.();
      return;
    }
    navigate(instanceRouteFromPath(location.pathname, item.route));
    onNavigate?.();
  };

  const renderNav = (items: NavItem[], onNavigate?: () => void) => (
    <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2 space-y-1">
      {items.map((item) => {
        const itemPath = instanceRouteFromPath(location.pathname, item.route);
        // In launcher mode the "active" cue follows which tabs are open rather
        // than the URL (which stays on `/workspace`).
        const active = launcherMode
          ? isWorkspaceTabId(item.route) && openTabIds.includes(item.route)
          : isActive(location.pathname, itemPath);
        return (
          <button
            key={item.route}
            onClick={() => handleNavClick(item, onNavigate)}
            className={`group flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                : 'text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
            }`}
          >
            <item.icon
              size={18}
              className={`shrink-0 transition-colors ${active ? 'text-[color:var(--text-primary)]' : 'text-[color:var(--text-muted)] group-hover:text-[color:var(--text-primary)]'}`}
            />
            <span className={`transition-opacity duration-200 whitespace-nowrap ${isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
              {item.label}
            </span>
          </button>
        );
      })}
    </nav>
  );

  // Inside a tiling workspace pane the global chrome (sidebar + header) is
  // already provided by the outer shell hosting <Workspace/>, and the pane
  // supplies its own header (tab picker / split / close). Pages still wrap their
  // body in <AppShell> for the route case, so here we collapse to bare content
  // and let the optional header actions ride along in a slim strip.
  if (workspaceMode) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
        {!hideHeader && actions && (
          <div className="flex shrink-0 items-center justify-end gap-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-4 py-2">
            {actions}
          </div>
        )}
        <main className={`flex-1 overflow-y-auto p-4 md:p-6 ${contentClassName}`}>
          {children}
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
      {/* Sidebar Desktop */}
      <aside
        aria-hidden={hideSidebar}
        className={`hidden md:flex flex-col overflow-hidden border-r bg-[color:var(--surface-0)] transition-[width,opacity,border-color] duration-250 ease-out ${
          hideSidebar
            ? 'w-0 opacity-0 border-r-transparent pointer-events-none'
            : `${isSidebarExpanded ? 'w-64' : 'w-16'} opacity-100 border-r-[color:var(--border-subtle)]`
        }`}
        onMouseEnter={() => setIsSidebarExpanded(true)}
        onMouseLeave={() => setIsSidebarExpanded(false)}
      >
        <div className="flex h-16 items-center px-4 border-b border-[color:var(--border-subtle)] overflow-hidden">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]">
              <Logo size={27} />
            </div>
            <div className={`transition-opacity duration-200 ${isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
              <p className="font-bold text-sm tracking-tight">SENTINEL</p>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium">CONSOLE v{APP_VERSION}</p>
            </div>
          </div>
        </div>

        {hasInstanceScope ? renderNav(navItems) : <div className="flex-1" />}

        <div className="p-2 border-t border-[color:var(--border-subtle)]">
           <button
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)] transition-colors"
          >
            {theme === 'dark' ? <Sun size={18} className="text-[color:var(--text-muted)]" /> : <Moon size={18} className="text-[color:var(--text-muted)]" />}
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
          className={`flex shrink-0 items-center justify-between overflow-hidden bg-[color:var(--surface-0)] gap-2 transition-[height,opacity,padding,border-color] duration-250 ease-out ${
            hideHeader
              ? 'h-0 opacity-0 px-0 md:px-0 border-b border-b-transparent pointer-events-none'
              : 'h-16 opacity-100 px-4 md:px-6 border-b border-[color:var(--border-subtle)]'
          }`}
        >
          <div className="flex items-center gap-4 min-w-0">
            <button
              onClick={() => setIsMobileMenuOpen(true)}
              className="md:hidden p-2 -ml-2 text-[color:var(--text-secondary)]"
            >
              <Menu size={20} />
            </button>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold truncate">{title}</h1>
              {subtitle && (
                <p className="text-[11px] text-[color:var(--text-muted)] font-mono">{subtitle}</p>
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
        <div className="fixed inset-0 z-50 flex md:hidden">
          <div className="fixed inset-0 bg-black/40 backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
          <div className="relative flex w-64 flex-col bg-[color:var(--surface-0)] animate-in slide-in-from-left duration-200">
             <div className="flex h-16 items-center justify-between px-4 border-b border-[color:var(--border-subtle)]">
              <div className="flex items-center gap-2">
                 <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]">
                  <Logo size={18} />
                </div>
                <span className="font-bold text-sm tracking-tight">SENTINEL</span>
              </div>
              <button onClick={() => setIsMobileMenuOpen(false)} className="p-1 text-[color:var(--text-muted)]">
                <X size={20} />
              </button>
            </div>
            {hasInstanceScope ? renderNav(navItems, () => setIsMobileMenuOpen(false)) : <div className="flex-1" />}
            <div className="p-4 border-t border-[color:var(--border-subtle)]">
              <button
                onClick={toggleTheme}
                className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
              >
                {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
