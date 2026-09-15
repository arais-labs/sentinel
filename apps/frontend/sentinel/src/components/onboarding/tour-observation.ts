import { useActiveSessionStore } from '../../store/active-session-store';
import { useFocusModeStore } from '../../store/focus-mode-store';
import { useWorkspaceStore } from '../../store/workspace-store';
import type { ShortcutId, TourTask } from './product-tour-content';

export type TourKey = Pick<KeyboardEvent, 'key' | 'code' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>;
export type TourEvidence = {
  kind: 'key' | 'input' | 'pointer' | 'drag';
  key?: TourKey;
  target: Element | null;
};
export function visibleTarget(selector: string): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find((element) => {
    if (element.closest('[data-product-tour], [inert]')) return false;
    const rect = element.getBoundingClientRect(),
      style = getComputedStyle(element);
    if (!rect.width || !rect.height || style.visibility === 'hidden' || style.display === 'none')
      return false;
    let left = Math.max(0, rect.left),
      right = Math.min(innerWidth, rect.right);
    let top = Math.max(0, rect.top),
      bottom = Math.min(innerHeight, rect.bottom);
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const css = getComputedStyle(parent),
        bounds = parent.getBoundingClientRect();
      if (css.visibility === 'hidden' || css.display === 'none') return false;
      if (/(hidden|clip|auto|scroll)/.test(css.overflowX)) {
        left = Math.max(left, bounds.left);
        right = Math.min(right, bounds.right);
      }
      if (/(hidden|clip|auto|scroll)/.test(css.overflowY)) {
        top = Math.max(top, bounds.top);
        bottom = Math.min(bottom, bounds.bottom);
      }
    }
    return right - left > 3 && bottom - top > 3;
  });
}

export function readTourSnapshot(instance: string) {
  const sessions = useActiveSessionStore.getState();
  const workspace = useWorkspaceStore.getState();
  const composer = visibleTarget('.chat-composer-input') as HTMLTextAreaElement | undefined;
  const present = (selector: string) => Boolean(visibleTarget(selector));
  return {
    active: sessions.byInstance[instance] ?? null,
    recent: [...(sessions.recentByInstance[instance] ?? [])],
    visibleDots: Array.from(document.querySelectorAll<HTMLElement>('[data-session-dot]')).map(
      (dot) => dot.dataset.sessionDot!
    ),
    focus: useFocusModeStore.getState().paneId,
    panes: { ...workspace.openTabs },
    paneCount: Object.keys(workspace.layout?.panels ?? {}).length,
    arrangement: workspace.layout
      ? JSON.stringify([workspace.layout.grid.orientation, paneArrangement(workspace.layout.grid.root)])
      : null,
    text: composer?.value ?? '',
    composerFocused: document.activeElement === composer,
    switcher: present('.global-session-menu'),
    hints: document.querySelector('.recent-session-dots')?.getAttribute('data-expanded') === 'true',
    deletion: present('[data-session-delete-confirm]'),
    attachment: present('dialog[open][aria-label="Attach a workspace"]'),
    fileSearch: document.activeElement?.getAttribute('aria-label') === 'Find a file',
    inspector: present('.sentinel-tool-card[data-inspect-open="true"]'),
    toolInput: Array.from(
      document.querySelectorAll(
        '.sentinel-tool-card[data-inspect-open="true"] .tool-card-views button[aria-pressed="true"]'
      )
    ).some((button) => button.textContent?.trim().toLowerCase() === 'input'),
    runSettings: present('[role="dialog"][aria-label="Run settings"]'),
    connection: present('[role="dialog"][aria-label="Connection and context details"]'),
    subagents: present('.subagent-float[data-open="true"]'),
  };
}
export type TourSnapshot = ReturnType<typeof readTourSnapshot>;

type GridNode = NonNullable<ReturnType<typeof useWorkspaceStore.getState>['layout']>['grid']['root'];
// Compare the pane positions and split directions, excluding generated group
// IDs, active selection, and divider sizes. Moving a live pane keeps its count.
function paneArrangement(node: GridNode): unknown {
  return Array.isArray(node.data) ? node.data.map(paneArrangement) : node.data.views;
}

export function matchesShortcut(id: ShortcutId, event?: TourKey): boolean {
  if (!event) return false;
  const key = event.key.toLowerCase();
  const plain = !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey;
  const command = event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey;
  const primary = (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey;
  switch (id) {
    case 'escape':
      return plain && key === 'escape';
    case 'newline':
      return event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey && key === 'enter';
    case 'command':
      return key === 'meta' && !event.ctrlKey && !event.altKey && !event.shiftKey;
    case 'jump':
      return command && (/^Digit[1-9]$/.test(event.code) || /^[1-9]$/.test(key));
    case 'neighbor':
      return command && ['arrowleft', 'arrowright'].includes(key);
    case 'history':
      return command && ['arrowup', 'arrowdown'].includes(key);
    case 'reorder':
      return (
        event.altKey &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.shiftKey &&
        ['arrowleft', 'arrowright'].includes(key)
      );
    case 'new':
      return command && key === 'n';
    case 'close':
      return command && key === 'w';
    case 'delete':
      return command && key === 'backspace';
    case 'focus':
      return command && key === 'f';
    case 'switcher':
      return primary && key === 'k';
    case 'file':
      return primary && key === 'p';
  }
}

/** A keystroke is evidence of intent, never proof that the action succeeded. */
export function taskSucceeded(
  task: TourTask,
  before: TourSnapshot,
  after: TourSnapshot,
  evidence: TourEvidence
): boolean {
  if (task.shortcut && (evidence.kind !== 'key' || !matchesShortcut(task.shortcut, evidence.key)))
    return false;
  const changedChat = after.active !== before.active;
  switch (task.check) {
    case 'new-chat':
      return Boolean(
        after.active && changedChat && !before.recent.includes(after.active) && after.composerFocused
      );
    case 'write':
      return (
        evidence.kind === 'input' &&
        Boolean(evidence.target?.matches('.chat-composer-input')) &&
        after.text.trim().length >= 8 &&
        after.text !== before.text
      );
    case 'newline':
      return after.composerFocused && after.text.split('\n').length > before.text.split('\n').length;
    case 'switcher-open':
      return !before.switcher && after.switcher;
    case 'switcher-close':
      return before.switcher && !after.switcher;
    case 'hints':
      return after.hints;
    case 'jump': {
      const number = Number(/^Digit([1-9])$/.exec(evidence.key?.code ?? '')?.[1] ?? evidence.key?.key);
      return changedChat && Boolean(after.active) && after.active === before.visibleDots[number - 1];
    }
    case 'neighbor': {
      const index = before.visibleDots.indexOf(before.active ?? '');
      const right = evidence.key?.key === 'ArrowRight';
      const expected =
        before.visibleDots[
          index < 0 ? (right ? 0 : before.visibleDots.length - 1) : index + (right ? 1 : -1)
        ];
      return changedChat && Boolean(expected) && after.active === expected;
    }
    case 'history':
      return changedChat && Boolean(after.active);
    case 'reorder':
      return (
        (evidence.kind === 'drag' || matchesShortcut('reorder', evidence.key)) &&
        before.recent.length === after.recent.length &&
        before.recent.every((id) => after.recent.includes(id)) &&
        before.recent.join() !== after.recent.join()
      );
    case 'close-chat':
      return Boolean(before.active) && changedChat && !after.recent.includes(before.active!);
    case 'delete-open':
      return Boolean(before.active) && !before.deletion && after.deletion;
    case 'delete-cancel':
      return (
        before.deletion &&
        !after.deletion &&
        after.active !== null &&
        after.active === before.active &&
        after.recent.includes(after.active)
      );
    case 'split':
      return (
        (evidence.kind === 'pointer' || evidence.kind === 'drag') &&
        before.active === after.active &&
        after.paneCount > 1 &&
        (after.paneCount > before.paneCount ||
          (after.paneCount === before.paneCount && after.arrangement !== before.arrangement))
      );
    case 'focus-in':
      return !before.focus && Boolean(after.focus);
    case 'focus-out':
      return Boolean(before.focus) && !after.focus;
    case 'pane':
      return (
        evidence.kind === 'pointer' &&
        Boolean(evidence.target?.closest(`[data-tour-nav="${task.pane}"]`)) &&
        Boolean(task.pane && after.panes[task.pane])
      );
    case 'attachment':
      return !before.attachment && after.attachment;
    case 'find-file':
      return after.fileSearch;
    case 'inspect':
      return !before.inspector && after.inspector;
    case 'tool-input':
      return !before.toolInput && after.toolInput && after.inspector;
    case 'run-settings':
      return !before.runSettings && after.runSettings;
    case 'connection':
      return !before.connection && after.connection;
    case 'subagents':
      return !before.subagents && after.subagents;
  }
}

export function taskUnavailable(task: TourTask | undefined, snapshot: TourSnapshot): boolean {
  if (!task) return false;
  if (['jump', 'neighbor', 'reorder'].includes(task.check)) return snapshot.visibleDots.length < 2;
  if (['delete-open', 'close-chat', 'attachment', 'subagents'].includes(task.check)) return !snapshot.active;
  return false;
}
