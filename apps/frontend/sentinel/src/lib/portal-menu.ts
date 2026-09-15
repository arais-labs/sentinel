import { useCallback, useEffect, useLayoutEffect, useState } from 'react';

export interface MenuRect {
  top: number;
  left: number;
  /** Width of the trigger, so the menu can size/align relative to it. */
  triggerWidth: number;
}

/**
 * Track the viewport rect of a trigger element while `open`, recomputing on
 * open and on scroll/resize so a menu portaled to `document.body` (position:
 * fixed) stays glued to its trigger. Returns null while closed.
 */
export function useAnchorRect(
  triggerRef: React.RefObject<HTMLElement | null>,
  open: boolean,
): MenuRect | null {
  const [rect, setRect] = useState<MenuRect | null>(null);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect({ top: r.bottom, left: r.left, triggerWidth: r.width });
  }, [triggerRef]);

  useLayoutEffect(() => {
    if (!open) {
      setRect(null);
      return;
    }
    measure();
    // Capture-phase scroll catches nested scroll containers (e.g. a pane body).
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [open, measure]);

  return rect;
}

/**
 * Close `open` when a pointer goes down outside both the trigger and the
 * portaled menu. Both refs are checked because the menu lives in document.body,
 * outside the trigger's DOM subtree.
 */
export function useDismissOnOutside(
  open: boolean,
  setOpen: (value: boolean) => void,
  triggerRef: React.RefObject<HTMLElement | null>,
  menuRef: React.RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
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
  }, [open, setOpen, triggerRef, menuRef]);
}
