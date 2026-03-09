import { PropsWithChildren, ReactNode, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Database,
  Zap,
  Target,
  Wrench,
  Send,
  MonitorPlay,
  Settings,
  GitBranch,
  Moon,
  Sun,
  Menu,
  X,
  ChevronRight,
  Activity,
} from 'lucide-react';

import { APP_VERSION } from '../lib/env';
import { api } from '../lib/api';
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

interface AppSwitchItem {
  id: 'araios' | 'sentinel';
  label: string;
  href: string;
}

interface AraiosIntegrationStatus {
  araios_frontend_url: string | null;
}

const navItems: NavItem[] = [
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

function isActive(pathname: string, candidate: string) {
  if (candidate === '/sessions') {
    return pathname.startsWith('/sessions');
  }
  if (candidate === '/triggers') {
    return pathname.startsWith('/triggers');
  }
  if (candidate === '/settings') {
    return pathname.startsWith('/settings');
  }
  if (candidate === '/telegram') {
    return pathname.startsWith('/telegram');
  }
  if (candidate === '/git') {
    return pathname.startsWith('/git');
  }
  return pathname === candidate;
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
  const defaultSentinelHref = `${window.location.origin.replace(/\/+$/, '')}/sentinel`;
  const [switchLinks, setSwitchLinks] = useState<{ sentinelHref: string; araiosHref: string }>({
    sentinelHref: defaultSentinelHref,
    araiosHref: '',
  });

  useEffect(() => {
    let mounted = true;

    const normalizeHref = (value: string | null | undefined): string => {
      const trimmed = (value ?? '').trim().replace(/\/+$/, '');
      if (!trimmed) {
        return '';
      }
      if (trimmed.startsWith('/')) {
        return trimmed;
      }
      try {
        const parsed = new URL(trimmed);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
          return parsed.toString().replace(/\/+$/, '');
        }
      } catch {
        return '';
      }
      return '';
    };

    const syncSwitchLinks = async () => {
      try {
        const integration = await api.get<AraiosIntegrationStatus>('/settings/araios');
        if (mounted) {
          setSwitchLinks({
            sentinelHref: defaultSentinelHref,
            araiosHref: normalizeHref(integration.araios_frontend_url),
          });
        }
      } catch {
        // Keep disabled links when URL settings cannot be loaded.
      }
    };

    void syncSwitchLinks();
    return () => {
      mounted = false;
    };
  }, []);

  const renderAppSwitcher = (currentApp: 'araios' | 'sentinel') => {
    const appSwitchItems: AppSwitchItem[] = [
      { id: 'araios', label: 'araiOS', href: switchLinks.araiosHref },
      { id: 'sentinel', label: 'Sentinel', href: switchLinks.sentinelHref },
    ];
    const activeIndex = appSwitchItems.findIndex((item) => item.id === currentApp);

    return (
      <div className="relative inline-grid grid-cols-2 gap-0 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-2)] p-0.5 overflow-hidden">
        <div
          className={`absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-full bg-[color:var(--surface-0)] shadow-sm transition-all duration-300 ease-out ${
            activeIndex <= 0 ? 'left-0.5' : 'left-[calc(50%)]'
          }`}
        />
        {appSwitchItems.map((item) => {
          const active = item.id === currentApp;
          const itemClasses =
            'relative z-10 inline-flex h-7 items-center justify-center rounded-full px-3 text-[10px] font-bold uppercase tracking-wider transition-colors duration-200';
          if (active) {
            return (
              <span key={item.id} className={`${itemClasses} text-[color:var(--text-primary)]`}>
                {item.label}
              </span>
            );
          }
          if (!item.href) {
            return (
              <span
                key={item.id}
                className={`${itemClasses} text-[color:var(--text-muted)] opacity-50 cursor-not-allowed`}
              >
                {item.label}
              </span>
            );
          }
          return (
            <a
              key={item.id}
              href={item.href}
              className={`${itemClasses} text-[color:var(--text-muted)] hover:text-[color:var(--text-secondary)]`}
            >
              {item.label}
            </a>
          );
        })}
      </div>
    );
  };

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

        <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2 space-y-1">
          {navItems.map((item) => {
            const active = isActive(location.pathname, item.path);
            return (
              <button
                key={item.path}
                onClick={() => navigate(item.path)}
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
            {renderAppSwitcher('sentinel')}
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
            <nav className="flex-1 py-4 px-2 space-y-1">
               {navItems.map((item) => {
                const active = isActive(location.pathname, item.path);
                return (
                  <button
                    key={item.path}
                    onClick={() => {
                      navigate(item.path);
                      setIsMobileMenuOpen(false);
                    }}
                    className={`flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors ${
                      active
                        ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                        : 'text-[color:var(--text-secondary)]'
                    }`}
                  >
                    <item.icon size={18} className={active ? 'text-[color:var(--text-primary)]' : 'text-[color:var(--text-muted)]'} />
                    {item.label}
                  </button>
                );
              })}
            </nav>
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
