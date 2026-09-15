import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import { useAnchorRect } from '../../lib/portal-menu';
import { splitPaneActions } from '../../lib/pane-action-items';

export function PaneActions({ children }: { children: ReactNode }) {
  const { items, overlays } = splitPaneActions(children);
  const host = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const widths = useRef<number[]>([]);
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const rect = useAnchorRect(trigger, open);
  const overflow = count < items.length;
  const menuWidth = Math.min(280, Math.max(208, ...widths.current.slice(count).map((width) => width + 16)));

  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    const measure = () => {
      const slots = Array.from(element.querySelectorAll<HTMLElement>('[data-action-slot]'));
      slots.forEach((slot, index) => {
        if (slot.firstElementChild) widths.current[index] = slot.getBoundingClientRect().width;
      });
      const available = element.clientWidth;
      const total = items.reduce<number>((sum, _, i) => sum + (widths.current[i] ?? 0) + (i ? 6 : 0), 0);
      let used = 0;
      let fitting = 0;
      const budget = total <= available ? available : Math.max(0, available - 34);
      for (let i = 0; i < items.length; i++) {
        const width = (widths.current[i] ?? 0) + (i ? 6 : 0);
        if (used + width > budget) break;
        used += width;
        fitting++;
      }
      setCount(fitting);
      if (fitting === items.length) setOpen(false);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    element.querySelectorAll('[data-action-slot]').forEach((slot) => observer.observe(slot));
    return () => observer.disconnect();
  }, [children, open, items.length]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (trigger.current?.contains(target) || menu.current?.contains(target)) return;
      // Page selectors portal their own dropdowns to body; allow those interactions.
      if (target.closest('#root') || target === document.body) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener('mousedown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return (
    <>
    {overlays}
    <div ref={host} className="pane-actions-strip relative flex min-w-8 flex-1 items-center gap-1.5 overflow-hidden">
      {items.map((item, index) => (
        <div key={index} data-action-slot inert={index >= count}
          className={index < count ? 'shrink-0 whitespace-nowrap' : 'pointer-events-none invisible absolute left-0 top-0 w-max whitespace-nowrap'}>
          {index >= count && open ? null : item}
        </div>
      ))}
      {overflow && <button ref={trigger} type="button" aria-label="More pane actions" title="More pane actions"
        aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}
        className="ml-auto shrink-0 rounded-md p-1.5 text-(--text-secondary) hover:bg-(--surface-2) hover:text-(--text-primary)">
        <MoreHorizontal size={16} />
      </button>}
      {open && rect && createPortal(
        <div ref={menu} data-pane-menu role="dialog" aria-label="More pane actions"
          style={{ position: 'fixed', width: menuWidth, top: rect.top + 6, left: Math.max(8, Math.min(rect.left + rect.triggerWidth - menuWidth, window.innerWidth - menuWidth - 8)), maxHeight: Math.max(80, window.innerHeight - rect.top - 14), zIndex: 10000 }}
          onPointerDown={(event) => event.stopPropagation()} onMouseDown={(event) => event.stopPropagation()}
          className="pane-actions-menu flex max-w-[calc(100vw-16px)] flex-col gap-0.5 overflow-y-auto overflow-x-hidden rounded-lg border border-(--border-strong) bg-(--surface-0) p-1 text-(--text-primary) shadow-xl shadow-black/20">
          {items.slice(count).map((item, index) => <div key={count + index} className="min-w-0">{item}</div>)}
        </div>, document.body,
      )}
    </div>
    </>
  );
}
