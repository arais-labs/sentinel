import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { Check, ChevronDown, Home, Server } from 'lucide-react';

import { api } from '../lib/api';
import { instanceRoute } from '../lib/routes';
import { useInstanceName } from '../lib/workspace-context';

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
  runtime_id: string | null;
}

interface SidebarInstanceSwitcherProps {
  /** Mirrors the sidebar hover-expand state so labels fade in/out in sync. */
  expanded: boolean;
}

/**
 * App-level instance switcher + Home button for the left sidebar. Present in
 * both normal and workspace modes (the sidebar is global chrome), so global
 * navigation lives here instead of being duplicated in per-page/per-pane
 * headers. Selecting an instance navigates to that instance's `/workspace`
 * route; Home returns to the instance picker at `/`.
 */
export function SidebarInstanceSwitcher({ expanded }: SidebarInstanceSwitcherProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const activeInstanceName = useInstanceName() ?? null;

  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [menuRect, setMenuRect] = useState<{ left: number; top: number; width: number } | null>(null);

  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Only show the switcher once we are scoped to an instance; outside an
  // instance scope (e.g. the `/` picker) there is nothing to switch.
  const hasInstanceScope = Boolean(activeInstanceName);

  useEffect(() => {
    let cancelled = false;
    if (!hasInstanceScope) {
      setInstances([]);
      return;
    }
    api
      .get<SentinelInstance[]>('/instances')
      .then((list) => {
        if (!cancelled) setInstances(list);
      })
      .catch(() => {
        if (!cancelled) setInstances([]);
      });
    return () => {
      cancelled = true;
    };
  }, [hasInstanceScope]);

  const activeInstance = instances.find((instance) => instance.name === activeInstanceName) ?? null;
  const label =
    activeInstance?.display_name?.trim() ||
    activeInstance?.name ||
    activeInstanceName ||
    'Instance';

  const updateMenuRect = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    setMenuRect({
      left: rect.left,
      top: rect.bottom + 6,
      width: rect.width,
    });
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    updateMenuRect();
    const handleReposition = () => updateMenuRect();
    window.addEventListener('resize', handleReposition);
    window.addEventListener('scroll', handleReposition, true);
    return () => {
      window.removeEventListener('resize', handleReposition);
      window.removeEventListener('scroll', handleReposition, true);
    };
  }, [isOpen, updateMenuRect]);

  useEffect(() => {
    if (!isOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (buttonRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setIsOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [isOpen]);

  // Close the menu whenever the route changes (a selection navigated away).
  useEffect(() => {
    setIsOpen(false);
  }, [location.pathname]);

  if (!hasInstanceScope) return null;

  const onSelectInstance = (instanceName: string) => {
    setIsOpen(false);
    if (instanceName === activeInstanceName) return;
    navigate(instanceRoute(instanceName, 'workspace'));
  };

  const labelClass = `transition-opacity duration-200 whitespace-nowrap ${
    expanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
  }`;

  return (
    <div className="px-2 pt-3 space-y-1">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        onClick={() => {
          updateMenuRect();
          setIsOpen((open) => !open);
        }}
        className="group flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)] transition-colors"
      >
        <Server
          size={18}
          className="shrink-0 text-[color:var(--text-muted)] transition-colors group-hover:text-[color:var(--text-primary)]"
        />
        <span className={`min-w-0 flex-1 truncate text-left ${labelClass}`}>{label}</span>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={`shrink-0 text-[color:var(--text-muted)] transition-[transform,opacity] duration-200 ${
            expanded ? 'opacity-100' : 'opacity-0 pointer-events-none'
          } ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      <button
        type="button"
        onClick={() => navigate('/')}
        className="group flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)] transition-colors"
      >
        <Home
          size={18}
          className="shrink-0 text-[color:var(--text-muted)] transition-colors group-hover:text-[color:var(--text-primary)]"
        />
        <span className={labelClass}>Home</span>
      </button>

      {isOpen && menuRect &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            aria-label="Switch instance"
            style={{
              left: menuRect.left,
              top: menuRect.top,
              width: Math.max(menuRect.width, 240),
            }}
            className="fixed z-[10000] max-h-80 overflow-y-auto rounded-2xl border border-[color:var(--border-strong)] bg-[color:var(--surface-0)] py-1.5 shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 origin-top-left"
          >
            {instances.length === 0 ? (
              <div className="px-3 py-3 text-xs text-[color:var(--text-muted)]">No instances</div>
            ) : (
              instances.map((instance) => {
                const title = (instance.display_name || instance.name).trim() || instance.name;
                const active = instance.name === activeInstanceName;
                return (
                  <button
                    key={instance.name}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => onSelectInstance(instance.name)}
                    className={`group flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                      active
                        ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]'
                        : 'text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] hover:text-[color:var(--text-primary)]'
                    }`}
                  >
                    <div
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        active
                          ? 'bg-[color:var(--accent-solid)]'
                          : 'bg-[color:var(--text-muted)]/35 group-hover:bg-[color:var(--text-secondary)]'
                      }`}
                    />
                    <span className="min-w-0 flex-1 truncate text-xs font-semibold">{title}</span>
                    {active && <Check size={13} className="shrink-0 text-[color:var(--accent-solid)]" />}
                  </button>
                );
              })
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
