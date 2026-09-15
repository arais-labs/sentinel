import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Loader2, ShieldAlert, X, type LucideIcon } from 'lucide-react';
import { useAnchorRect, useDismissOnOutside } from '../../lib/portal-menu';

export type ActivityPill = { id: string; label: string; busy?: boolean; needsApproval?: boolean; title?: string };

type Item = { entry: ActivityPill; present: boolean };
type Props = {
  entries: ActivityPill[];
  focusedId: string | null;
  onOpen: (id: string) => void;
  onClose?: (id: string) => void;
  icon: LucideIcon;
  label: string;
  variant?: 'subagent';
};

function ActivityPillView({ item, label, focused, visible, onOpen, onClose, onExited, icon: Icon }: {
  icon: LucideIcon;
  item: Item;
  label: string;
  focused: boolean;
  visible: boolean;
  onOpen: Props['onOpen'];
  onClose: Props['onClose'];
  onExited: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { entry, present } = item;

  useLayoutEffect(() => {
    if (!visible) {
      if (!present) onExited(entry.id);
      return;
    }
    const slot = ref.current;
    const pill = slot?.firstElementChild as HTMLElement | null;
    if (!slot || !pill) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (!present) onExited(entry.id);
      return;
    }

    const width = slot.getBoundingClientRect().width;
    const options = { duration: present ? 320 : 240, easing: 'cubic-bezier(.22, 1, .36, 1)' };
    const expanded = { width: `${width}px`, paddingLeft: '4px', paddingRight: '4px', opacity: 1 };
    const collapsed = { width: '0px', paddingLeft: '0px', paddingRight: '0px', opacity: 0 };
    const layout = slot.animate(present ? [collapsed, expanded] : [expanded, collapsed], {
      ...options, fill: present ? 'none' : 'forwards',
    });
    const style = getComputedStyle(pill);
    const pressed = {
      transform: 'translateY(3px) scale(.92)',
      boxShadow: style.getPropertyValue('--composer-pressed-shadow').trim(),
    };
    const raised = { transform: 'translateY(0) scale(1)', boxShadow: style.boxShadow };
    const depth = pill.animate(present ? [pressed, raised] : [raised, pressed], options);
    if (!present) void layout.finished.then(() => onExited(entry.id)).catch(() => {});
    return () => { layout.cancel(); depth.cancel(); };
  }, [present, visible, entry.id, onExited]);

  if (!visible) return null;
  return (
    <div ref={ref} className="session-terminal-slot" inert={!present} aria-hidden={!present || undefined}>
      <div className="session-terminal-pill" data-focused={focused}>
        <button type="button" className="session-terminal-open" onClick={() => onOpen(entry.id)}
          title={entry.title || entry.label} aria-label={entry.needsApproval ? `${entry.label}: Needs approval` : entry.label}>
          <Icon size={12} />
          <span className="session-terminal-label">{label}</span>
          {entry.needsApproval ? <span className="subagent-approval-indicator"><ShieldAlert size={12} />Needs approval</span> : entry.busy ? <Loader2 size={10} className="animate-spin" /> : null}
        </button>
        {onClose && <button type="button" className="session-terminal-close" onClick={() => onClose(entry.id)}
          title={`Close ${label}`} aria-label={`Close ${label}`}>
          <X size={11} />
        </button>}
      </div>
    </div>
  );
}

export function ComposerActivityPills({ entries, focusedId, onOpen, onClose, icon: Icon, label, variant }: Props) {
  const [previous, setPrevious] = useState(entries);
  const [items, setItems] = useState<Item[]>(() => entries.map(entry => ({ entry, present: true })));
  const [selectedId, setSelectedId] = useState(focusedId ?? entries[0]?.id);
  const [open, setOpen] = useState(false);
  const [fitting, setFitting] = useState<string[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const measurements = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuOpen = open && entries.length > 0;
  const anchor = useAnchorRect(trigger, menuOpen);
  useDismissOnOutside(menuOpen, setOpen, trigger, menu);
  useEffect(() => {
    if (!entries.length) setOpen(false);
  }, [entries.length]);
  useEffect(() => {
    if (menuOpen && anchor) menu.current?.querySelector<HTMLButtonElement>('.session-terminal-menu-open')?.focus();
  }, [menuOpen, Boolean(anchor)]);
  if (previous !== entries) {
    setPrevious(entries);
    // Keep departing slots in place until they finish contracting. An entry that
    // reappears during its exit becomes interactive again and cancels that exit.
    const next = items.map(item => {
      const entry = entries.find(entry => entry.id === item.entry.id);
      return { entry: entry ?? item.entry, present: Boolean(entry) };
    });
    for (const entry of entries) {
      if (!next.some(item => item.entry.id === entry.id)) next.push({ entry, present: true });
    }
    setItems(next);
  }
  const onExited = useCallback((id: string) => {
    setItems(current => current.filter(item => item.entry.id !== id || item.present));
  }, []);
  const displayFor = (entry: ActivityPill) => {
    const characters = Array.from(entry.label);
    return variant === 'subagent' && characters.length > 15
      ? characters.slice(0, 14).join('') + '…'
      : entry.label;
  };
  // Keep the selected slot until its exit finishes and prioritize it when space
  // is limited. Fit the remaining complete pills using their rendered widths.
  const primary = items.find(item => item.present && item.entry.id === focusedId)
    ?? items.find(item => item.entry.id === selectedId) ?? items[0];
  if (primary?.entry.id !== selectedId) setSelectedId(primary?.entry.id);
  const measureKey = JSON.stringify(items.map(item => [item.entry.id, displayFor(item.entry), item.entry.busy, item.entry.needsApproval]));
  useLayoutEffect(() => {
    const container = root.current;
    const measure = measurements.current;
    if (!container || !measure) return;
    const update = () => {
      const widths = new Map(Array.from(measure.querySelectorAll<HTMLElement>('[data-entry-measure]'))
        .map(element => [element.dataset.entryMeasure!, element.getBoundingClientRect().width + 8]));
      const available = container.clientWidth;
      const total = [...widths.values()].reduce((sum, width) => sum + width, 0) + Math.max(0, widths.size - 1) * 4;
      let next: string[];
      if (total <= available) {
        next = [...widths.keys()];
      } else if (widths.size === 1 && available >= 66) {
        next = [...widths.keys()];
      } else {
        const overflowWidth = measure.querySelector<HTMLElement>('[data-overflow-measure]')!.getBoundingClientRect().width;
        let remaining = available - overflowWidth - 4;
        const priority = primary ? [primary, ...items.filter(item => item !== primary)] : items;
        next = [];
        for (const item of priority) {
          const width = widths.get(item.entry.id) ?? 288;
          if (width <= remaining || (item === primary && remaining >= 110)) {
            next.push(item.entry.id);
            remaining -= Math.min(width, remaining) + 4;
          }
        }
      }
      // Preserve runtime order even when the focused entry receives first choice.
      next = items.filter(item => next.includes(item.entry.id)).map(item => item.entry.id);
      setFitting(current => current.length === next.length && current.every((id, index) => id === next[index]) ? current : next);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    observer.observe(measure.firstElementChild!);
    return () => observer.disconnect();
  }, [measureKey, primary?.entry.id, menuOpen]);
  const overflowEntries = entries.filter(entry => !fitting.includes(entry.id));
  const overflowCount = overflowEntries.length;
  const showOverflow = overflowCount > 0 || menuOpen;
  useEffect(() => {
    if (!overflowCount) setOpen(false);
  }, [overflowCount]);

  if (!items.length) return null;
  return (
    <div ref={root} className="composer-activity-group" data-variant={variant} role="group" aria-label={label} data-compact={!fitting.length}>
      <div className="session-terminal-row">
        <div className="session-terminal-selection">
          {items.map(item => <ActivityPillView key={item.entry.id} item={item} label={displayFor(item.entry)}
            visible={fitting.includes(item.entry.id)} focused={focusedId === item.entry.id}
            onOpen={onOpen} onClose={onClose} onExited={onExited} icon={Icon} />)}
        </div>
        {showOverflow && <button ref={trigger} type="button" className="session-terminal-overflow"
          aria-label={`Show ${overflowCount} more ${label.toLowerCase()}`} aria-haspopup="dialog" aria-expanded={menuOpen}
          title={`Show ${overflowCount} more ${label.toLowerCase()}`} onClick={() => setOpen(value => !value)}>
          {overflowEntries.some(entry => entry.needsApproval) && <ShieldAlert size={12} className="subagent-approval-indicator" aria-label="Sub-agent needs approval" />}
          <span className="session-terminal-overflow-more">+{overflowCount}</span>
          <span className="session-terminal-overflow-all"><Icon size={12} />{entries.length}</span>
        </button>}
      </div>
      <div ref={measurements} className="session-terminal-measure" aria-hidden="true" inert>
        <div>
          {items.map(item => <div key={item.entry.id} data-entry-measure={item.entry.id} className="session-terminal-pill">
            <button type="button" className="session-terminal-open" tabIndex={-1}>
              <Icon size={12} /><span className="session-terminal-label">{displayFor(item.entry)}</span>
              {item.entry.needsApproval ? <span className="flex items-center gap-1"><ShieldAlert size={12} />Needs approval</span> : item.entry.busy ? <Loader2 size={10} /> : null}
            </button>
            {onClose && <button type="button" className="session-terminal-close" tabIndex={-1}><X size={11} /></button>}
          </div>)}
          <button type="button" data-overflow-measure className="session-terminal-overflow" tabIndex={-1}><ShieldAlert size={12} />+{entries.length}</button>
        </div>
      </div>
      {menuOpen && anchor && createPortal(
        <div ref={menu} className="session-terminal-menu" data-variant={variant} role="dialog" aria-label={label} data-pane-menu
          style={{ left: Math.max(12, Math.min(anchor.left + anchor.triggerWidth / 2 - 170, window.innerWidth - 352)),
            bottom: Math.max(12, window.innerHeight - anchor.top + 42), maxHeight: Math.max(80, Math.min(340, anchor.top - 54)) }}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus();
            }
          }}>
          <div className="session-terminal-menu-heading"><span>{label} <span>{overflowCount}</span></span>
            <button type="button" aria-label={`Close ${label.toLowerCase()} menu`} onClick={() => { setOpen(false); trigger.current?.focus(); }}><X size={14} /></button>
          </div>
          <div className="session-terminal-menu-list">
            {overflowEntries.map(entry => <div key={entry.id} className="session-terminal-menu-item" data-focused={entry.id === focusedId}>
              <button type="button" className="session-terminal-menu-open" title={entry.title || entry.label} aria-label={entry.needsApproval ? `${entry.label}: Needs approval` : entry.label}
                onClick={() => { setOpen(false); onOpen(entry.id); }}>
                <Icon size={14} />
                <span>{displayFor(entry)}</span>
                {entry.needsApproval ? <span className="subagent-approval-indicator"><ShieldAlert size={12} />Needs approval</span> : entry.busy ? <Loader2 size={12} className="animate-spin" /> : entry.id === focusedId ? <Check size={13} /> : null}
              </button>
              {onClose && <button type="button" className="session-terminal-menu-close" title={`Close ${displayFor(entry)}`}
                aria-label={`Close ${displayFor(entry)}`} onClick={() => onClose(entry.id)}><X size={13} /></button>}
            </div>)}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}
