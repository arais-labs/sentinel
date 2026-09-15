import type { DockviewApi } from 'dockview-react';
import { useFocusModeStore } from '../../store/focus-mode-store';

type Bounds = { left: number; top: number; width: number; height: number };
type Motion = { animation: Animation; from: Bounds; to: Bounds };
const duration = 260;

function interpolate(from: Bounds, to: Bounds, progress: number): Bounds {
  return {
    left: from.left + (to.left - from.left) * progress,
    top: from.top + (to.top - from.top) * progress,
    width: from.width + (to.width - from.width) * progress,
    height: from.height + (to.height - from.height) * progress,
  };
}

/** Animate the real pane elements after Dockview commits a layout. No remounts or snapshots. */
export function animatePaneLayout(api: DockviewApi): () => void {
  const previous = new Map<string, Bounds>();
  const motions = new Map<string, Motion>();
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let frame = 0;
  let resizing = false;
  let initialized = false;
  let wasFocused = Boolean(useFocusModeStore.getState().paneId);

  const update = () => {
    frame = 0;
    const focused = Boolean(useFocusModeStore.getState().paneId);
    // Focus mode already owns its own transition. Divider dragging stays direct.
    const animate = initialized && !resizing && !reduced.matches && !focused && !wasFocused;
    wasFocused = focused;
    const ids = new Set(api.groups.map(group => group.id));
    for (const [id, motion] of motions) {
      if (!ids.has(id)) { motion.animation.cancel(); motions.delete(id); }
    }
    for (const id of previous.keys()) if (!ids.has(id)) previous.delete(id);

    const changes = api.groups.map(group => {
      const motion = motions.get(group.id);
      const progress = motion?.animation.effect?.getComputedTiming().progress;
      const from = motion && typeof progress === 'number'
        ? interpolate(motion.from, motion.to, progress) : previous.get(group.id);
      motion?.animation.cancel();
      motions.delete(group.id);
      return { group, from };
    });
    // Read all final sizes before starting any animations, so siblings agree on geometry.
    const measured = changes.map(({ group, from }) => {
      const rect = group.element.getBoundingClientRect();
      return { group, from, to: { left: rect.left, top: rect.top, width: rect.width, height: rect.height } };
    });
    for (const { group, from, to } of measured) {
      previous.set(group.id, to);
      if (!animate || !from || from.width < 1 || from.height < 1 || to.width < 1 || to.height < 1) continue;
      if (Math.abs(from.left - to.left) + Math.abs(from.top - to.top) + Math.abs(from.width - to.width) + Math.abs(from.height - to.height) < 1) continue;
      const animation = group.element.animate([
        { transformOrigin: '0 0', transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})` },
        { transformOrigin: '0 0', transform: 'none' },
      ], { duration, easing: 'cubic-bezier(.22, 1, .36, 1)' });
      motions.set(group.id, { animation, from, to });
      animation.onfinish = () => { if (motions.get(group.id)?.animation === animation) motions.delete(group.id); };
    }
    initialized = true;
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  const down = (event: PointerEvent) => {
    if (!(event.target instanceof Element) || !event.target.closest('.dv-sash')) return;
    resizing = true;
    schedule();
  };
  const up = () => {
    if (!resizing) return;
    // Capture the final drag position before enabling automatic transitions again.
    if (frame) cancelAnimationFrame(frame);
    update();
    resizing = false;
  };
  const layout = api.onDidLayoutChange(schedule);
  document.addEventListener('pointerdown', down, true);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
  reduced.addEventListener('change', schedule);
  schedule();
  return () => {
    cancelAnimationFrame(frame);
    layout.dispose();
    document.removeEventListener('pointerdown', down, true);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
    reduced.removeEventListener('change', schedule);
    for (const motion of motions.values()) motion.animation.cancel();
    motions.clear();
    previous.clear();
  };
}
