import React, { useState } from 'react';
import {
  Users,
  Briefcase,
  FileText,
  Target,
  Shield,
  Rocket,
  Compass,
  GitBranch,
  CheckCircle,
  Lock,
  MessageCircle,
  MessageSquare,
  FileCode,
  Menu,
  X,
  LogOut,
  Sun,
  Moon,
  Zap,
  Box,
} from 'lucide-react';
import { clsx } from 'clsx';
import { Logo } from './Icons';

// Map icon name strings (from module config) to Lucide components
const ICON_MAP = {
  Users, Briefcase, FileText, Target, Shield, Rocket, Compass,
  GitBranch, CheckCircle, Lock, MessageCircle, MessageSquare, FileCode, Box,
};

const DEFAULT_ARAIOS_APP_URL = '/araios/';
const DEFAULT_SENTINEL_APP_URL = '/sentinel/';

function resolveAppUrl(value, fallback) {
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) {
    return fallback;
  }
  if (trimmed.startsWith('/')) {
    return trimmed;
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.toString();
    }
  } catch {
    // Fall through to fallback.
  }
  return fallback;
}

const APP_SWITCH_ITEMS = [
  {
    id: 'araios',
    label: 'araiOS',
    href: resolveAppUrl(import.meta.env.APP_ARAIOS_URL, DEFAULT_ARAIOS_APP_URL),
  },
  {
    id: 'sentinel',
    label: 'Sentinel',
    href: resolveAppUrl(import.meta.env.APP_SENTINEL_URL, DEFAULT_SENTINEL_APP_URL),
  },
];

function NavIcon({ name, size = 18, className }) {
  const Icon = ICON_MAP[name] || Box;
  return <Icon size={size} className={className} />;
}

function NavItem({ item, active, onSelect, isSidebarExpanded, badge }) {
  return (
    <button
      key={item.id}
      onClick={() => onSelect(item.id)}
      className={clsx(
        "group flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors relative",
        active
          ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
          : 'text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
      )}
    >
      <NavIcon
        name={item.icon}
        size={18}
        className={clsx(
          "shrink-0 transition-colors",
          active ? 'text-[color:var(--text-primary)]' : 'text-[color:var(--text-muted)] group-hover:text-[color:var(--text-primary)]'
        )}
      />
      <span className={clsx(
        "transition-opacity duration-200 whitespace-nowrap",
        isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
      )}>
        {item.label}
      </span>
      {badge != null && badge > 0 && (
        <span className={clsx(
          "absolute right-2 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-rose-500 px-1 text-[9px] font-bold text-white",
          !isSidebarExpanded && "right-1 top-1"
        )}>
          {badge}
        </span>
      )}
    </button>
  );
}

export function AppShell({
  title,
  subtitle,
  actions,
  children,
  activeModule,
  onModuleChange,
  onLogout,
  pendingCount = 0,
  dynamicModules = [],
  systemModules = [],
}) {
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [theme, setTheme] = useState('dark');

  React.useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  };

  const renderNavItems = (items, expanded, onSelect) => (
    <>
      {/* Dynamic modules */}
      {items.dynamic.map(item => (
        <NavItem
          key={item.id}
          item={item}
          active={activeModule === item.id}
          onSelect={onSelect}
          isSidebarExpanded={expanded}
        />
      ))}

      {/* Divider between dynamic and system */}
      {items.dynamic.length > 0 && items.system.length > 0 && (
        <div className="my-2 border-t border-[color:var(--border-subtle)]" />
      )}

      {/* System modules */}
      {items.system.map(item => (
        <NavItem
          key={item.id}
          item={item}
          active={activeModule === item.id}
          onSelect={onSelect}
          isSidebarExpanded={expanded}
          badge={item.id === 'approvals' ? pendingCount : undefined}
        />
      ))}
    </>
  );

  const renderAppSwitcher = (currentApp) => (
    <div className="inline-flex items-center rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-0.5">
      {APP_SWITCH_ITEMS.map((item) => {
        const active = item.id === currentApp;
        if (active) {
          return (
            <span
              key={item.id}
              className="inline-flex h-7 items-center rounded px-3 text-[10px] font-bold uppercase tracking-wider bg-[color:var(--surface-0)] text-[color:var(--text-primary)]"
            >
              {item.label}
            </span>
          );
        }
        return (
          <a
            key={item.id}
            href={item.href}
            className="inline-flex h-7 items-center rounded px-3 text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors"
          >
            {item.label}
          </a>
        );
      })}
    </div>
  );

  const navItems = { dynamic: dynamicModules, system: systemModules };

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[color:var(--app-bg)] text-[color:var(--text-primary)]">
      {/* Sidebar Desktop */}
      <aside
        className={clsx(
          "hidden md:flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] transition-all duration-200 ease-in-out z-30",
          isSidebarExpanded ? 'w-64' : 'w-16'
        )}
        onMouseEnter={() => setIsSidebarExpanded(true)}
        onMouseLeave={() => setIsSidebarExpanded(false)}
      >
        <div className="flex h-16 items-center px-4 border-b border-[color:var(--border-subtle)] overflow-hidden">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]">
              <Logo size={18} />
            </div>
            <div className={clsx(
              "transition-opacity duration-200",
              isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
            )}>
              <p className="font-bold text-sm tracking-tight">ARAIS</p>
              <p className="text-[10px] text-[color:var(--text-muted)] font-medium uppercase tracking-widest">araiOS</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2 space-y-1 scrollbar-hide">
          {renderNavItems(navItems, isSidebarExpanded, onModuleChange)}
        </nav>

        <div className="p-2 border-t border-[color:var(--border-subtle)] space-y-1">
          <button
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)] transition-colors"
          >
            {theme === 'dark' ? <Sun size={18} className="text-[color:var(--text-muted)]" /> : <Moon size={18} className="text-[color:var(--text-muted)]" />}
            <span className={clsx(
              "transition-opacity duration-200 whitespace-nowrap",
              isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
            )}>
              {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
            </span>
          </button>
          <button
            onClick={onLogout}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-rose-500 hover:bg-rose-500/10 transition-colors"
          >
            <LogOut size={18} className="shrink-0" />
            <span className={clsx(
              "transition-opacity duration-200 whitespace-nowrap",
              isSidebarExpanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
            )}>
              Sign Out
            </span>
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex flex-1 flex-col min-w-0 bg-[color:var(--app-bg)]">
        {/* Header */}
        <header className="grid h-16 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-4 md:px-6 gap-2">
          <div className="flex items-center gap-4 min-w-0">
            <button
              onClick={() => setIsMobileMenuOpen(true)}
              className="md:hidden p-2 -ml-2 text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] rounded-lg"
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
            {renderAppSwitcher('araios')}
          </div>

          <div className="flex items-center justify-end gap-2">
            {actions}
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden">
          {children}
        </main>
      </div>

      {/* Mobile Menu Overlay */}
      {isMobileMenuOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden">
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setIsMobileMenuOpen(false)} />
          <div className="relative flex w-64 flex-col bg-[color:var(--surface-0)] shadow-2xl animate-in slide-in-from-left duration-200">
            <div className="flex h-16 items-center justify-between px-4 border-b border-[color:var(--border-subtle)]">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]">
                  <Logo size={18} />
                </div>
                <span className="font-bold text-sm tracking-tight uppercase">ARAIS</span>
              </div>
              <button onClick={() => setIsMobileMenuOpen(false)} className="p-1 text-[color:var(--text-muted)] hover:bg-[color:var(--surface-1)] rounded-md transition-colors">
                <X size={20} />
              </button>
            </div>
            <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto">
              {renderNavItems(navItems, true, (id) => { onModuleChange(id); setIsMobileMenuOpen(false); })}
            </nav>
            <div className="p-4 border-t border-[color:var(--border-subtle)] space-y-2">
              <button
                onClick={toggleTheme}
                className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
              >
                {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
              </button>
              <button
                onClick={onLogout}
                className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-rose-500"
              >
                <LogOut size={18} />
                Sign Out
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
