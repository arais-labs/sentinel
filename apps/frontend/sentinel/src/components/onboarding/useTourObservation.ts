import { useEffect, useRef, useState } from 'react';
import { useActiveSessionStore } from '../../store/active-session-store';
import { useFocusModeStore } from '../../store/focus-mode-store';
import { useWorkspaceStore } from '../../store/workspace-store';
import type { TourStep, TourTask } from './product-tour-content';
import { observeTourKeydown } from './tour-keyboard';
import {
  readTourSnapshot,
  taskSucceeded,
  taskUnavailable,
  visibleTarget,
  matchesShortcut,
  type TourEvidence,
  type TourSnapshot,
} from './tour-observation';

export type TourRect = { left: number; top: number; width: number; height: number; borderRadius: string };
type Observation = {
  rect: TourRect | null;
  unavailable: boolean;
  nativeModal: boolean;
  width: number;
  height: number;
  pressed: string[];
  shortcutHint: string | null;
  dragging: boolean;
};
const keyLabel = (key: string) =>
  ({
    Meta: '⌘',
    Control: '⌃',
    Alt: '⌥',
    Shift: '⇧',
    Enter: '↵',
    Escape: 'Esc',
    Backspace: '⌫',
    ArrowLeft: '←',
    ArrowRight: '→',
    ArrowUp: '↑',
    ArrowDown: '↓',
  })[key] ?? key.toUpperCase();

/** All observers exist only while the guide is open. Product components emit no tour events. */
export function useTourObservation(
  step: TourStep | undefined,
  task: TourTask | undefined,
  instance: string,
  onVerified: () => void
) {
  const [view, setView] = useState<Observation>({
    rect: null,
    unavailable: false,
    nativeModal: false,
    width: innerWidth,
    height: innerHeight,
    pressed: [],
    shortcutHint: null,
    dragging: false,
  });
  const complete = useRef(onVerified);
  complete.current = onVerified;
  useEffect(() => {
    if (!step) return;
    let frame = 0,
      settled = false,
      target: HTMLElement | undefined;
    let baseline = readTourSnapshot(instance);
    let pending: { before: TourSnapshot; evidence: TourEvidence; at: number } | undefined;
    let dragging = false;
    const held = new Set<string>();
    let shortcutHint: string | null = null;
    const verify = (snapshot: TourSnapshot) => {
      if (
        !settled &&
        task &&
        pending &&
        (dragging || performance.now() - pending.at < 8000) &&
        taskSucceeded(task, pending.before, snapshot, pending.evidence)
      ) {
        settled = true;
        complete.current();
      }
    };
    const update = () => {
      frame = 0;
      const nextTarget = visibleTarget(step.target);
      if (target !== nextTarget) {
        if (target) resize.unobserve(target);
        if (nextTarget) resize.observe(nextTarget);
        target = nextTarget;
      }
      const bounds = target?.getBoundingClientRect();
      const gap = 6;
      const left = Math.max(2, (bounds?.left ?? 0) - gap),
        top = Math.max(2, (bounds?.top ?? 0) - gap);
      const style = target ? getComputedStyle(target) : null;
      const corner = (value: string | undefined) => {
        const radius = parseFloat(value ?? '0') || 0;
        const resolved =
          value?.includes('%') && bounds ? (Math.min(bounds.width, bounds.height) * radius) / 100 : radius;
        return `${Math.min(resolved, Math.min(bounds?.width ?? 0, bounds?.height ?? 0) / 2) + gap}px`;
      };
      const rect = bounds
        ? {
            left,
            top,
            width: Math.max(0, Math.min(innerWidth - 2, bounds.right + gap) - left),
            height: Math.max(0, Math.min(innerHeight - 2, bounds.bottom + gap) - top),
            borderRadius: [
              style?.borderTopLeftRadius,
              style?.borderTopRightRadius,
              style?.borderBottomRightRadius,
              style?.borderBottomLeftRadius,
            ]
              .map(corner)
              .join(' '),
          }
        : null;
      const snapshot = readTourSnapshot(instance);
      const disabled = target?.matches(':disabled') ?? false;
      const next = {
        rect,
        unavailable: !target || disabled || taskUnavailable(task, snapshot),
        nativeModal: Boolean(document.querySelector('dialog[open]')),
        width: innerWidth,
        height: innerHeight,
        pressed: [...held],
        shortcutHint,
        dragging,
      };
      setView((previous) => (JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
      verify(snapshot);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    const outside = (event: Event) =>
      event.isTrusted && !(event.target instanceof Element && event.target.closest('[data-product-tour]'));
    const practiceKey = (event: KeyboardEvent) =>
      event.isTrusted &&
      !(
        event.target instanceof Element &&
        event.target.closest('[data-product-tour] input, [data-product-tour] textarea')
      );
    const record = (event: Event, kind: TourEvidence['kind']) => {
      if (!outside(event)) return;
      const snapshot = readTourSnapshot(instance);
      verify(snapshot);
      if (settled) return;
      const evidence: TourEvidence = { kind, target: event.target instanceof Element ? event.target : null };
      pending = { before: snapshot, evidence, at: performance.now() };
      schedule();
    };
    const down = (event: KeyboardEvent) => {
      if (!practiceKey(event) || event.repeat || event.isComposing) return;
      baseline = readTourSnapshot(instance);
      held.add(keyLabel(event.key));
      if (task?.shortcut && matchesShortcut(task.shortcut, event)) shortcutHint = null;
      else if (
        task?.shortcut &&
        event.ctrlKey &&
        !event.metaKey &&
        ['new', 'close', 'delete', 'jump', 'neighbor', 'history', 'focus', 'command'].includes(task.shortcut)
      ) {
        shortcutHint = 'Use the Command (⌘) key for this shortcut on Mac.';
      }
      pending = {
        before: baseline,
        evidence: {
          kind: 'key',
          key: event,
          target: event.target instanceof Element ? event.target : null,
        },
        at: performance.now(),
      };
      schedule();
    };
    const up = (event: KeyboardEvent) => {
      if (!practiceKey(event) || event.isComposing) return;
      held.delete(keyLabel(event.key));
      schedule();
    };
    const pointer = (event: Event) => {
      if (!outside(event)) return;
      if (task?.shortcut) {
        // A later click must not turn a failed keyboard attempt into a pass.
        verify(readTourSnapshot(instance));
        pending = undefined;
        return;
      }
      record(event, 'pointer');
    };
    const hover = (event: Event) => {
      if (task?.check === 'connection') pointer(event);
    };
    const input = (event: Event) => {
      if (!outside(event)) return;
      if (task?.shortcut) {
        schedule();
        return;
      }
      pending = {
        before: baseline,
        evidence: { kind: 'input', target: event.target instanceof Element ? event.target : null },
        at: performance.now(),
      };
      schedule();
    };
    const beforeInput = (event: Event) => {
      if (outside(event)) baseline = readTourSnapshot(instance);
    };
    const drag = (event: DragEvent) => {
      if (!outside(event)) return;
      dragging = true;
      record(event, 'drag');
    };
    const drop = (event: DragEvent) => {
      if (!outside(event)) return;
      dragging = false;
      // Keep the pre-drag snapshot; Dockview applies the change after this
      // capture handler and may publish its layout on the following frame.
      if (pending?.evidence.kind === 'drag') pending.at = performance.now();
      schedule();
    };
    const dragEnd = () => {
      if (dragging) pending = undefined; // Drag ended without a drop.
      dragging = false;
      schedule();
    };
    const pointerUp = (event: PointerEvent) => {
      if (outside(event) && pending?.evidence.kind === 'pointer') {
        pending.at = performance.now();
        schedule();
      }
    };
    const transitionEnd = (event: TransitionEvent) => {
      if (
        event.target instanceof Element &&
        target &&
        (event.target === target || event.target.contains(target))
      )
        schedule();
    };
    const blur = () => {
      held.clear();
      // A native drop can change window focus before Dockview publishes its
      // layout (and before our next frame). Keep its pre-drag evidence through
      // that handoff. dragEnd clears cancelled drags; verify bounds completed
      // drops to the existing eight-second window.
      if (!dragging && pending?.evidence.kind !== 'drag') pending = undefined;
      schedule();
    };
    const resize = new ResizeObserver(schedule);
    const observer = new MutationObserver((records) => {
      if (
        records.some(
          (record) => !(record.target instanceof Element && record.target.closest('[data-product-tour]'))
        )
      )
        schedule();
    });
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: [
        'aria-expanded',
        'aria-current',
        'aria-pressed',
        'data-expanded',
        'data-inspect-open',
        'data-open',
        'open',
        'class',
        'disabled',
        'inert',
      ],
    });
    const subscriptions = [
      useActiveSessionStore.subscribe(schedule),
      useFocusModeStore.subscribe(schedule),
      useWorkspaceStore.subscribe(schedule),
    ];
    const stopKeys = observeTourKeydown(down);
    window.addEventListener('keyup', up, true);
    window.addEventListener('blur', blur);
    document.addEventListener('pointerdown', pointer, true);
    document.addEventListener('pointerover', hover, true);
    document.addEventListener('beforeinput', beforeInput, true);
    document.addEventListener('input', input, true);
    document.addEventListener('dragstart', drag, true);
    document.addEventListener('drop', drop, true);
    document.addEventListener('dragend', dragEnd, true);
    document.addEventListener('pointerup', pointerUp, true);
    document.addEventListener('scroll', schedule, true);
    document.addEventListener('focusin', schedule, true);
    document.addEventListener('transitionend', transitionEnd, true);
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      resize.disconnect();
      subscriptions.forEach((dispose) => dispose());
      stopKeys();
      window.removeEventListener('keyup', up, true);
      window.removeEventListener('blur', blur);
      document.removeEventListener('pointerdown', pointer, true);
      document.removeEventListener('pointerover', hover, true);
      document.removeEventListener('input', input, true);
      document.removeEventListener('beforeinput', beforeInput, true);
      document.removeEventListener('dragstart', drag, true);
      document.removeEventListener('drop', drop, true);
      document.removeEventListener('dragend', dragEnd, true);
      document.removeEventListener('pointerup', pointerUp, true);
      document.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      document.removeEventListener('focusin', schedule, true);
      document.removeEventListener('transitionend', transitionEnd, true);
    };
  }, [step?.id, task?.id, instance]);
  return view;
}
