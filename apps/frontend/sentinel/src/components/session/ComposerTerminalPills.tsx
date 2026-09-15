import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Loader2, Terminal, X } from 'lucide-react';
import type { ActivePane } from '../../hooks/useSessionRuntimeStream';
import { useAnchorRect, useDismissOnOutside } from '../../lib/portal-menu';

type Item = { pane: ActivePane; present: boolean };
type Props = {
  panes: ActivePane[];
  focusedPaneId: string | null;
  onOpen: (id: string) => void;
  onClose: (id: string) => void;
};

function TerminalPill({ item, label, focused, visible, onOpen, onClose, onExited }: {
  item: Item;
  label: string;
  focused: boolean;
  visible: boolean;
  onOpen: Props['onOpen'];
  onClose: Props['onClose'];
  onExited: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { pane, present } = item;

  useLayoutEffect(() => {
    if (!visible) {
      if (!present) onExited(pane.id);
      return;
    }
    const slot = ref.current;
    const pill = slot?.firstElementChild as HTMLElement | null;
    if (!slot || !pill) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (!present) onExited(pane.id);
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
    if (!present) void layout.finished.then(() => onExited(pane.id)).catch(() => {});
    return () => { layout.cancel(); depth.cancel(); };
  }, [present, visible, pane.id, onExited]);

  if (!visible) return null;
  return (
    <div ref={ref} className="session-terminal-slot" inert={!present} aria-hidden={!present || undefined}>
      <div className="session-terminal-pill" data-focused={focused}>
        <button type="button" className="session-terminal-open" onClick={() => onOpen(pane.id)}
          title={`${label} (${pane.windowId} / ${pane.id})`}>
          <Terminal size={12} />
          <span className="session-terminal-label">{label}</span>
          {pane.busy ? <Loader2 size={10} className="animate-spin" /> : null}
        </button>
        <button type="button" className="session-terminal-close" onClick={() => onClose(pane.id)}
          title={`Close ${label}`} aria-label={`Close pane ${label}`}>
          <X size={11} />
        </button>
      </div>
    </div>
  );
}

export function ComposerTerminalPills({ panes, focusedPaneId, onOpen, onClose }: Props) {
  const [previous, setPrevious] = useState(panes);
  const [items, setItems] = useState<Item[]>(() => panes.map(pane => ({ pane, present: true })));
  const [selectedId, setSelectedId] = useState(focusedPaneId ?? panes[0]?.id);
  const [open, setOpen] = useState(false);
  const [fitting, setFitting] = useState<string[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const measurements = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuOpen = open && panes.length > 0;
  const anchor = useAnchorRect(trigger, menuOpen);
  useDismissOnOutside(menuOpen, setOpen, trigger, menu);
  useEffect(() => {
    if (!panes.length) setOpen(false);
  }, [panes.length]);
  useEffect(() => {
    if (menuOpen && anchor) menu.current?.querySelector<HTMLButtonElement>('.session-terminal-menu-open')?.focus();
  }, [menuOpen, Boolean(anchor)]);
  if (previous !== panes) {
    setPrevious(panes);
    // Keep departing slots in place until they finish contracting. A pane that
    // reappears during its exit becomes interactive again and cancels that exit.
    const next = items.map(item => {
      const pane = panes.find(pane => pane.id === item.pane.id);
      return { pane: pane ?? item.pane, present: Boolean(pane) };
    });
    for (const pane of panes) {
      if (!next.some(item => item.pane.id === pane.id)) next.push({ pane, present: true });
    }
    setItems(next);
  }
  const onExited = useCallback((id: string) => {
    setItems(current => current.filter(item => item.pane.id !== id || item.present));
  }, []);
  const labelFor = (pane: ActivePane) => [
    pane.windowName, pane.title, pane.dead ? 'Exited' : pane.lastCommand || 'Shell',
  ].filter(Boolean).join(' · ');
  const displayFor = (pane: ActivePane) => {
    const label = labelFor(pane);
    const duplicates = items.filter(other => labelFor(other.pane) === label);
    return duplicates.length > 1
      ? `${label} · ${duplicates.findIndex(other => other.pane.id === pane.id) + 1}` : label;
  };
  // Keep the selected slot until its exit finishes and prioritize it when space
  // is limited. Fit the remaining complete pills using their rendered widths.
  const primary = items.find(item => item.present && item.pane.id === focusedPaneId)
    ?? items.find(item => item.pane.id === selectedId) ?? items[0];
  if (primary?.pane.id !== selectedId) setSelectedId(primary?.pane.id);
  const measureKey = JSON.stringify(items.map(item => [item.pane.id, displayFor(item.pane), item.pane.busy]));
  useLayoutEffect(() => {
    const container = root.current;
    const measure = measurements.current;
    if (!container || !measure) return;
    const update = () => {
      const widths = new Map(Array.from(measure.querySelectorAll<HTMLElement>('[data-pane-measure]'))
        .map(element => [element.dataset.paneMeasure!, element.getBoundingClientRect().width + 8]));
      const available = container.clientWidth;
      const total = [...widths.values()].reduce((sum, width) => sum + width, 0) + Math.max(0, widths.size - 1) * 4;
      let next: string[];
      if (total <= available && !menuOpen) {
        next = [...widths.keys()];
      } else if (widths.size === 1 && !menuOpen && available >= 66) {
        next = [...widths.keys()];
      } else {
        const overflowWidth = measure.querySelector<HTMLElement>('[data-overflow-measure]')!.getBoundingClientRect().width;
        let remaining = available - overflowWidth - 4;
        const priority = primary ? [primary, ...items.filter(item => item !== primary)] : items;
        next = [];
        for (const item of priority) {
          const width = widths.get(item.pane.id) ?? 288;
          if (width <= remaining || (item === primary && remaining >= 110)) {
            next.push(item.pane.id);
            remaining -= Math.min(width, remaining) + 4;
          }
        }
      }
      // Preserve runtime order even when the focused pane receives first choice.
      next = items.filter(item => next.includes(item.pane.id)).map(item => item.pane.id);
      setFitting(current => current.length === next.length && current.every((id, index) => id === next[index]) ? current : next);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(container);
    observer.observe(measure.firstElementChild!);
    return () => observer.disconnect();
  }, [measureKey, primary?.pane.id, menuOpen]);
  const overflowCount = panes.filter(pane => !fitting.includes(pane.id)).length;
  const showOverflow = overflowCount > 0 || menuOpen;

  if (!items.length) return null;
  return (
    <div ref={root} className="chat-composer-terminals" role="group" aria-label="Session terminals" data-compact={!fitting.length}>
      <div className="session-terminal-row">
        <div className="session-terminal-selection">
          {items.map(item => <TerminalPill key={item.pane.id} item={item} label={displayFor(item.pane)}
            visible={fitting.includes(item.pane.id)} focused={focusedPaneId === item.pane.id}
            onOpen={onOpen} onClose={onClose} onExited={onExited} />)}
        </div>
        {showOverflow && <button ref={trigger} type="button" className="session-terminal-overflow"
          aria-label={`Show all ${panes.length} terminals`} aria-haspopup="dialog" aria-expanded={menuOpen}
          title={`Show all ${panes.length} terminals`} onClick={() => setOpen(value => !value)}>
          <span className="session-terminal-overflow-more">+{overflowCount}</span>
          <span className="session-terminal-overflow-all"><Terminal size={12} />{panes.length}</span>
        </button>}
      </div>
      <div ref={measurements} className="session-terminal-measure" aria-hidden="true" inert>
        <div>
          {items.map(item => <div key={item.pane.id} data-pane-measure={item.pane.id} className="session-terminal-pill">
            <button type="button" className="session-terminal-open" tabIndex={-1}>
              <Terminal size={12} /><span className="session-terminal-label">{displayFor(item.pane)}</span>
              {item.pane.busy ? <Loader2 size={10} /> : null}
            </button>
            <button type="button" className="session-terminal-close" tabIndex={-1}><X size={11} /></button>
          </div>)}
          <button type="button" data-overflow-measure className="session-terminal-overflow" tabIndex={-1}>+{panes.length}</button>
        </div>
      </div>
      {menuOpen && anchor && createPortal(
        <div ref={menu} className="session-terminal-menu" role="dialog" aria-label="Session terminals" data-pane-menu
          style={{ left: Math.max(12, Math.min(anchor.left + anchor.triggerWidth / 2 - 170, window.innerWidth - 352)),
            bottom: Math.max(12, window.innerHeight - anchor.top + 42), maxHeight: Math.max(80, Math.min(340, anchor.top - 54)) }}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus();
            }
          }}>
          <div className="session-terminal-menu-heading"><span>Terminals <span>{panes.length}</span></span>
            <button type="button" aria-label="Close terminals menu" onClick={() => { setOpen(false); trigger.current?.focus(); }}><X size={14} /></button>
          </div>
          <div className="session-terminal-menu-list">
            {panes.map(pane => <div key={pane.id} className="session-terminal-menu-item" data-focused={pane.id === focusedPaneId}>
              <button type="button" className="session-terminal-menu-open" title={displayFor(pane)}
                onClick={() => { onOpen(pane.id); setOpen(false); trigger.current?.focus(); }}>
                <Terminal size={14} />
                <span>{displayFor(pane)}</span>
                {pane.busy ? <Loader2 size={12} className="animate-spin" /> : pane.id === focusedPaneId ? <Check size={13} /> : null}
              </button>
              <button type="button" className="session-terminal-menu-close" title={`Close ${displayFor(pane)}`}
                aria-label={`Close pane ${displayFor(pane)}`} onClick={() => onClose(pane.id)}><X size={13} /></button>
            </div>)}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}
