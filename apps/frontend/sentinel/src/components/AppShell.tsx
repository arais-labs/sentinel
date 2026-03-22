import { PropsWithChildren, ReactNode, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Database,
  Zap,
  Wrench,
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
  FileCode,
  MessageCircle,
} from 'lucide-react';

import { APP_VERSION } from '../lib/env';
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
  path: string;
  icon: typeof LayoutDashboard;
}

const sentinelNavItems: NavItem[] = [
  { label: 'Sessions', path: '/sessions', icon: LayoutDashboard },
  { label: 'Session Logs', path: '/logs', icon: Activity },
  { label: 'Memory', path: '/memory', icon: Database },
  { label: 'Triggers', path: '/triggers', icon: Zap },
  { label: 'Tools', path: '/tools', icon: Wrench },
  { label: 'Git', path: '/git', icon: GitBranch },
  { label: 'Telegram', path: '/telegram', icon: Send },
  { label: 'Showcase', path: '/showcase', icon: MonitorPlay },
  { label: 'Settings', path: '/settings', icon: Settings },
];

const araiosNavItems: NavItem[] = [
  { label: 'Modules', path: '/araios/modules', icon: LayoutGrid },
  { label: 'Approvals', path: '/araios/approvals', icon: CheckCircle },
  { label: 'Permissions', path: '/araios/permissions', icon: Lock },
  { label: 'Documents', path: '/araios/documents', icon: FileCode },
  { label: 'Tasks', path: '/araios/tasks', icon: GitBranch },
  { label: 'Coordination', path: '/araios/coordination', icon: MessageCircle },
];

function isActive(pathname: string, candidate: string) {
  return pathname === candidate || pathname.startsWith(candidate + '/');
}

type AppMode = 'sentinel' | 'araios';

function detectMode(pathname: string): AppMode {
  return pathname.startsWith('/araios') ? 'araios' : 'sentinel';
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
  const theme = useThemeStore((state) => state.theme);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const currentMode = detectMode(location.pathname);
  const navItems = currentMode === 'araios' ? araiosNavItems : sentinelNavItems;

  const switchTo = (mode: AppMode) => {
    if (mode === currentMode) return;
    if (mode === 'araios') navigate('/araios/modules');
    else navigate('/sessions');
  };

  const renderSwitcher = () => (
    <div className="relative inline-grid grid-cols-2 gap-0 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-2)] p-0.5 overflow-hidden">
      <div
        className={`absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out ${
          currentMode === 'sentinel' ? 'left-0.5' : 'left-[calc(50%)]'
        }`}
      />
      {(['sentinel', 'araios'] as const).map((mode) => {
        const active = mode === currentMode;
        return (
          <button
            key={mode}
            type="button"
            onClick={() => switchTo(mode)}
            className={`relative z-10 inline-flex h-7 items-center justify-center rounded-full px-3 text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 ${
              active ? 'text-[color:var(--text-primary)]' : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]'
            }`}
          >
            {mode === 'sentinel' ? 'Sentinel' : 'araiOS'}
          </button>
        );
      })}
    </div>
  );

  const renderNav = (items: NavItem[], onNavigate?: () => void) => (
    <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2 space-y-1">
      {items.map((item) => {
        const active = isActive(location.pathname, item.path);
        return (
          <button
            key={item.path}
            onClick={() => { navigate(item.path); onNavigate?.(); }}
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

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
      {/* Sidebar Desktop */}
      {!hideSidebar ? (
      <aside
        className={`hidden md:flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] transition-all duration-200 ease-in-out ${
          isSidebarExpanded ? 'w-64' : 'w-16'
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

        {renderNav(navItems)}

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
      ) : null}

      {/* Main Content Area */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Header */}
        {!hideHeader ? (
        <header className="grid h-16 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-4 md:px-6 gap-2">
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
                <p className="text-[11px] text-[color:var(--text-muted)] font-medium truncate uppercase tracking-wider">{subtitle}</p>
              )}
            </div>
          </div>

          <div className="hidden md:flex items-center justify-center">
            {renderSwitcher()}
          </div>

          <div className="flex items-center justify-end gap-2">
            {actions}
          </div>
        </header>
        ) : null}

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
            <div className="p-3 border-b border-[color:var(--border-subtle)]">
              {renderSwitcher()}
            </div>
            {renderNav(navItems, () => setIsMobileMenuOpen(false))}
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
