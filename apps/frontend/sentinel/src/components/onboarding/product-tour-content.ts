import type { WorkspaceTabId } from '../../lib/workspace-tabs';

export type TourCheck =
  | 'new-chat'
  | 'write'
  | 'newline'
  | 'switcher-open'
  | 'switcher-close'
  | 'hints'
  | 'jump'
  | 'neighbor'
  | 'history'
  | 'reorder'
  | 'close-chat'
  | 'delete-open'
  | 'delete-cancel'
  | 'split'
  | 'focus-in'
  | 'focus-out'
  | 'pane'
  | 'attachment'
  | 'find-file'
  | 'inspect'
  | 'tool-input'
  | 'run-settings'
  | 'connection'
  | 'subagents';
export type ShortcutId =
  | 'new'
  | 'newline'
  | 'switcher'
  | 'escape'
  | 'command'
  | 'jump'
  | 'neighbor'
  | 'history'
  | 'reorder'
  | 'close'
  | 'delete'
  | 'focus'
  | 'file';
export interface TourTask {
  id: string;
  label: string;
  success: string;
  check: TourCheck;
  shortcut?: ShortcutId;
  pane?: WorkspaceTabId;
}
export interface TourStep {
  id: string;
  chapter: 'Conversations' | 'Your workspace' | 'Follow the work';
  title: string;
  instruction: string;
  detail: string;
  target: string;
  preparePane?: WorkspaceTabId;
  unavailable: string;
  tasks: TourTask[];
}
export const tourChapters = ['Conversations', 'Your workspace', 'Follow the work'] as const;

// Completion is checked against live state. Nothing here sends messages,
// provisions tools, changes settings, or confirms a deletion for the user.
export const tourSteps: TourStep[] = [
  {
    id: 'new-chat',
    chapter: 'Conversations',
    title: 'One conversation per task.',
    instruction:
      'Press Command + N to create a fresh chat. Watch it appear in the top bar, with the composer ready for your instructions.',
    detail:
      'Each chat keeps its own conversation and pane layout. Separate tasks stay easy to return to, even when several agents are working at once.',
    target: '.global-new-chat',
    preparePane: 'sessions',
    unavailable: 'Open this instance in the desktop workspace to access the chat bar.',
    tasks: [
      {
        id: 'create',
        label: 'Create a chat with the shortcut',
        success: 'New chat created',
        check: 'new-chat',
        shortcut: 'new',
      },
    ],
  },
  {
    id: 'composer',
    chapter: 'Conversations',
    title: 'Give the agent a clear brief.',
    instruction:
      'Type a short practice brief, then add a new line with Shift + Enter. Include the outcome you want and any constraints.',
    detail:
      'Enter sends your message and starts real work. Leave this practice text unsent. Attach or paste images when visual context helps; you can edit your brief before sending.',
    target: '.chat-composer',
    preparePane: 'sessions',
    unavailable: 'Open a chat to use its composer.',
    tasks: [
      { id: 'write', label: 'Write a few words in the composer', success: 'Brief drafted', check: 'write' },
      {
        id: 'newline',
        label: 'Add a line without sending',
        success: 'New line added',
        check: 'newline',
        shortcut: 'newline',
      },
    ],
  },
  {
    id: 'switcher',
    chapter: 'Conversations',
    title: 'Your history, a keystroke away.',
    instruction:
      'Open All chats with Command + K. Search by title, or use the arrow keys and Enter to select a conversation. Press Escape to return.',
    detail:
      'All chats includes your saved history. The dots in the top bar are the conversations you are currently watching; these are two different ways to navigate.',
    target: '.global-session-trigger',
    unavailable: 'The chat switcher is in the desktop title bar.',
    tasks: [
      {
        id: 'open',
        label: 'Open All chats with the shortcut',
        success: 'Chat history opened',
        check: 'switcher-open',
        shortcut: 'switcher',
      },
      {
        id: 'close',
        label: 'Close the menu with Escape',
        success: 'Back to your conversation',
        check: 'switcher-close',
        shortcut: 'escape',
      },
    ],
  },
  {
    id: 'watched',
    chapter: 'Conversations',
    title: 'Move between the dots.',
    instruction:
      'Hold Command to reveal names and numbers. Use Command + a number to open a different watched chat, then move to a neighbor with Command + Left or Right.',
    detail:
      'Numbers follow the dot order. Hover a dot for its latest final response. Status indicates work in progress, a response to read, or a request for your input.',
    target: '.recent-session-dots',
    unavailable: 'Watch at least two chats: open another from All chats, or create one with Command + N.',
    tasks: [
      {
        id: 'reveal',
        label: 'Hold Command to reveal the bar',
        success: 'Shortcut hints revealed',
        check: 'hints',
        shortcut: 'command',
      },
      {
        id: 'jump',
        label: 'Jump to a different watched chat',
        success: 'Chat selected by number',
        check: 'jump',
        shortcut: 'jump',
      },
      {
        id: 'neighbor',
        label: 'Move one dot left or right',
        success: 'Moved to the neighboring chat',
        check: 'neighbor',
        shortcut: 'neighbor',
      },
    ],
  },
  {
    id: 'history',
    chapter: 'Conversations',
    title: 'Step through your history.',
    instruction:
      'Use Command + Down for an older chat, or Command + Up for a newer one. Choose a direction with another chat available.',
    detail:
      'Up and Down follow All chats, newest first. Left and Right follow your own dot order. Each chat’s layout returns when you select it.',
    target: '.global-session-trigger',
    unavailable: 'You need at least two saved chats to move through history.',
    tasks: [
      {
        id: 'move',
        label: 'Switch chats through history',
        success: 'History navigation confirmed',
        check: 'history',
        shortcut: 'history',
      },
    ],
  },
  {
    id: 'reorder',
    chapter: 'Conversations',
    title: 'Make the shortcuts yours.',
    instruction:
      'Drag a dot to a new position. You can also focus a dot with Tab, then press Option + Left or Right to move it.',
    detail:
      'The order stays where you put it. Reordering updates Command + 1 through 9, so your most-used chats can occupy familiar positions.',
    target: '.recent-session-dots',
    unavailable: 'Watch at least two chats before changing their order.',
    tasks: [
      { id: 'move', label: 'Reposition a watched chat', success: 'Your dot order changed', check: 'reorder' },
    ],
  },
  {
    id: 'close',
    chapter: 'Conversations',
    title: 'Close a view. Keep the work.',
    instruction:
      'Create a spare chat with Command + N, then close that empty chat with Command + W. You will return to another watched conversation.',
    detail:
      'Command + W removes a chat from the watched bar. Saved conversations remain in All chats; empty chats are discarded. Closing a view does not stop a running agent.',
    target: '.global-new-chat',
    unavailable: 'The desktop chat bar must be visible for these shortcuts.',
    tasks: [
      {
        id: 'spare',
        label: 'Create a spare chat',
        success: 'Spare chat ready',
        check: 'new-chat',
        shortcut: 'new',
      },
      {
        id: 'close',
        label: 'Close the spare with Command + W',
        success: 'Spare chat closed',
        check: 'close-chat',
        shortcut: 'close',
      },
    ],
  },
  {
    id: 'delete',
    chapter: 'Conversations',
    title: 'Deletion is a separate action.',
    instruction:
      'With a saved chat selected, press Command + Delete to open its confirmation. Read it, then press Escape to cancel this practice.',
    detail:
      'On Mac, Delete is the Backspace key. Enter approves the focused confirmation and can permanently delete messages. This exercise ends by cancelling; deleting anything is not required.',
    target: '.global-session-trigger',
    unavailable: 'Select a saved chat before opening the confirmation.',
    tasks: [
      {
        id: 'open',
        label: 'Open the deletion confirmation',
        success: 'Confirmation opened',
        check: 'delete-open',
        shortcut: 'delete',
      },
      {
        id: 'cancel',
        label: 'Cancel with Escape',
        success: 'Cancelled; the chat is still here',
        check: 'delete-cancel',
        shortcut: 'escape',
      },
    ],
  },
  {
    id: 'layout',
    chapter: 'Your workspace',
    title: 'Arrange the work around you.',
    instruction:
      'Drag a sidebar view, such as Terminal, to a pane edge. Release when the placement preview appears. The split menu in a pane header offers the same choices.',
    detail:
      'Edge drops add a pane, or move it if that view is already open. A center drop replaces a view. Drag dividers to resize. Terminal, Files, and Desktop are views of your workspace. Each chat remembers its arrangement. Clicking a terminal pill opens Terminal to the left when Chat is the only pane, or focuses the existing Terminal.',
    target: '[data-tour-pane="sessions"] button[title="Split pane"], [data-tour-nav="terminal"]',
    preparePane: 'sessions',
    unavailable: 'Show Chat, then use its split control or expand the sidebar.',
    tasks: [
      {
        id: 'split',
        label: 'Add or reposition a pane with an edge drop or split',
        success: 'Pane arrangement updated',
        check: 'split',
      },
    ],
  },
  {
    id: 'focus',
    chapter: 'Your workspace',
    title: 'Room to concentrate.',
    instruction:
      'Click inside the pane you want to focus, then press Command + F. Press Escape to bring your arrangement back.',
    detail:
      'Focus temporarily expands the active pane without discarding your other views. Command + F also toggles focus; the pane header’s expand button is the pointer alternative.',
    target: '[data-tour-pane="sessions"] button[aria-label="Enter focus mode"], [data-focus-workspace]',
    preparePane: 'sessions',
    unavailable: 'Open a workspace pane to try focus mode.',
    tasks: [
      {
        id: 'enter',
        label: 'Enter focus with the shortcut',
        success: 'Pane focused',
        check: 'focus-in',
        shortcut: 'focus',
      },
      {
        id: 'exit',
        label: 'Restore the layout with Escape',
        success: 'Your layout is back',
        check: 'focus-out',
        shortcut: 'escape',
      },
    ],
  },
  {
    id: 'workspaces',
    chapter: 'Your workspace',
    title: 'Choose where work runs.',
    instruction:
      'Open Workspaces from the sidebar to see project folders, machines, resources, and installed tools.',
    detail:
      'A workspace is the environment where commands run. A pane layout is how you view that work. CPU, memory, disk, and tool bundles belong to the environment. You can inspect the list without provisioning anything.',
    target: '[data-tour-nav="workspaces"]',
    unavailable: 'Expand the sidebar to find Workspaces.',
    tasks: [
      {
        id: 'open',
        label: 'Open the Workspaces view',
        success: 'Workspace management opened',
        check: 'pane',
        pane: 'workspaces',
      },
    ],
  },
  {
    id: 'attachment',
    chapter: 'Your workspace',
    title: 'Connect the conversation.',
    instruction:
      'In Chat, open the workspace pill to see available environments. Close the picker after looking, or attach one when you want to use it.',
    detail:
      'An attached chat uses that workspace for runtime commands, files, browser activity, and forwarded services. Desktop is an optional tool bundle in the environment, not a separate project.',
    target: '[data-tour="workspace-attachment"]',
    preparePane: 'sessions',
    unavailable: 'Select a chat and show its pane. The picker is disabled while the agent is busy.',
    tasks: [
      {
        id: 'open',
        label: 'Inspect the workspace picker',
        success: 'Workspace picker opened',
        check: 'attachment',
      },
    ],
  },
  {
    id: 'files',
    chapter: 'Your workspace',
    title: 'Find a file without hunting.',
    instruction:
      'Open Files, then press Command + P to open the explorer and focus Find a file. Type a path or filename when you want to narrow the tree.',
    detail:
      'This shortcut belongs to the Files pane. Changes and History show the repository alongside the files. A workspace must be ready for the file browser to load.',
    target: '.project-browser',
    preparePane: 'files',
    unavailable: 'Open Files with a ready workspace before using the shortcut.',
    tasks: [
      {
        id: 'find',
        label: 'Focus file search with the shortcut',
        success: 'File search focused',
        check: 'find-file',
        shortcut: 'file',
      },
    ],
  },
  {
    id: 'tool-cards',
    chapter: 'Follow the work',
    title: 'Read what actually happened.',
    instruction:
      'Open Inspect on a tool card, then select Input. Compare what the agent requested with the Result when you review its work.',
    detail:
      'Status marks explain success, failure, or a pending state. Input is the request, Result is the response, and Raw exposes the payload. Cards appear after an agent uses a tool; return later if this chat is still empty.',
    target: '.sentinel-tool-card',
    preparePane: 'sessions',
    unavailable: 'Open a chat with tool activity, or skip this step and return after your first task.',
    tasks: [
      { id: 'inspect', label: 'Open a tool’s inspector', success: 'Tool inspector opened', check: 'inspect' },
      { id: 'input', label: 'Select its Input view', success: 'Tool request displayed', check: 'tool-input' },
    ],
  },
  {
    id: 'run-settings',
    chapter: 'Follow the work',
    title: 'Set the pace of a task.',
    instruction:
      'Open Run settings. Explore Model & reasoning, Agent mode, and Step limit before choosing how the next task should run.',
    detail:
      'Model choices depend on your connected providers. These controls change the selected chat. You can inspect the options without choosing new settings.',
    target: 'button[aria-label="Run settings"]',
    preparePane: 'sessions',
    unavailable: 'Show Chat. In a narrow pane, look under More pane actions.',
    tasks: [
      { id: 'open', label: 'Open Run settings', success: 'Run settings opened', check: 'run-settings' },
    ],
  },
  {
    id: 'connection',
    chapter: 'Follow the work',
    title: 'Keep an eye on capacity.',
    instruction: 'Hover or open LIVE to inspect the connection, context usage, and workspace statistics.',
    detail:
      'Connected does not mean the agent is working. Context is the model’s conversation budget; CPU, memory, disk, and network describe the attached workspace. Compact summarizes earlier context to make room.',
    target: 'button[aria-label^="Connection, workspace and context usage"]',
    preparePane: 'sessions',
    unavailable: 'Show Chat, or open More pane actions to find LIVE.',
    tasks: [
      {
        id: 'open',
        label: 'Reveal connection details',
        success: 'Connection details visible',
        check: 'connection',
      },
    ],
  },
  {
    id: 'subagents',
    chapter: 'Follow the work',
    title: 'Follow delegated work.',
    instruction:
      'Open Sub-agents to see the tasks delegated from this conversation. Each task can be inspected individually.',
    detail:
      'The main agent coordinates independent tasks and brings their results back. An empty list is normal before work is delegated. Opening the panel does not launch another agent.',
    target: '.subagent-float-trigger',
    preparePane: 'sessions',
    unavailable: 'Select a chat. In a narrow pane, open More pane actions.',
    tasks: [
      {
        id: 'open',
        label: 'Open the sub-agent panel',
        success: 'Delegated work panel opened',
        check: 'subagents',
      },
    ],
  },
  {
    id: 'memory',
    chapter: 'Follow the work',
    title: 'Keep useful context close.',
    instruction: 'Open Memory to inspect the durable information this instance can use across conversations.',
    detail:
      'Project facts and preferences can outlive one chat. Nearby, Modules exposes capabilities, Permissions controls access, and Approvals holds actions that need a decision.',
    target: '[data-tour-nav="memory"]',
    unavailable: 'Expand the sidebar to find Memory.',
    tasks: [
      { id: 'open', label: 'Open Memory', success: 'Memory view opened', check: 'pane', pane: 'memory' },
    ],
  },
  {
    id: 'instance-settings',
    chapter: 'Follow the work',
    title: 'Settings have one home.',
    instruction: 'Open Settings from the sidebar. Its sections organize model providers, chat appearance, backup and restore, and—in the desktop app—Services and Updates.',
    detail: 'Services includes local service status, recovery controls, and service logs. Services and Updates affect the whole app; providers and backups belong to this instance. The Sentinel logo returns to the full-screen Instances home. Notifications stay in the notification center.',
    target: '[data-tour-nav="settings"]',
    unavailable: 'Expand the sidebar to find Settings.',
    tasks: [{ id: 'open', label: 'Open Settings', success: 'Settings opened', check: 'pane', pane: 'settings' }],
  },
  {
    id: 'automation',
    chapter: 'Follow the work',
    title: 'Return to the work that repeats.',
    instruction:
      'Open Triggers to explore scheduled and event-driven tasks. Inspect the controls without enabling a trigger.',
    detail:
      'Start with a task whose results you can review. The notification center collects alerts, Telegram, configured in Settings, offers another conversation channel, and Session Logs follows the selected chat. Reopen this guide from Help → Guided Tour.',
    target: '[data-tour-nav="triggers"]',
    unavailable: 'Expand the sidebar to find Triggers.',
    tasks: [
      {
        id: 'open',
        label: 'Open Triggers',
        success: 'Automation view opened',
        check: 'pane',
        pane: 'triggers',
      },
    ],
  },
];

export const shortcutKeys: Record<ShortcutId, string[][]> = {
  new: [['⌘', 'N']],
  newline: [['⇧', '↵']],
  switcher: [['⌘', 'K']],
  escape: [['Esc']],
  command: [['⌘']],
  jump: [['⌘', '1–9']],
  neighbor: [
    ['⌘', '←'],
    ['⌘', '→'],
  ],
  history: [
    ['⌘', '↑'],
    ['⌘', '↓'],
  ],
  reorder: [
    ['⌥', '←'],
    ['⌥', '→'],
  ],
  close: [['⌘', 'W']],
  delete: [['⌘', '⌫']],
  focus: [['⌘', 'F']],
  file: [['⌘', 'P']],
};
export const shortcutReference = [
  {
    group: 'Conversations',
    keys: [['⌘', 'N']],
    title: 'New chat',
    detail: 'Creates and selects a new conversation.',
  },
  {
    group: 'Conversations',
    keys: [['⌘', 'K']],
    title: 'All chats',
    detail: 'Open or close searchable history. Ctrl + K also works.',
  },
  {
    group: 'Conversations',
    keys: [['↑'], ['↓'], ['↵']],
    title: 'Navigate All chats',
    detail: 'Move through the open menu; Enter opens the highlighted chat.',
  },
  {
    group: 'Conversations',
    keys: [['⌘']],
    title: 'Reveal watched chats',
    detail: 'Hold Command to expand names and number hints.',
  },
  {
    group: 'Conversations',
    keys: [['⌘', '1–9']],
    title: 'Jump to a watched chat',
    detail: 'Numbers follow the dot order, from left to right.',
  },
  {
    group: 'Conversations',
    keys: [
      ['⌘', '←'],
      ['⌘', '→'],
    ],
    title: 'Neighboring watched chat',
    detail: 'Follows your dot order; stops at either end.',
  },
  {
    group: 'Conversations',
    keys: [
      ['⌘', '↑'],
      ['⌘', '↓'],
    ],
    title: 'Newer or older chat',
    detail: 'Follows All chats, newest first.',
  },
  {
    group: 'Conversations',
    keys: [
      ['⌥', '←'],
      ['⌥', '→'],
    ],
    title: 'Reorder a watched chat',
    detail: 'Focus a dot with Tab first. Dragging also works.',
  },
  {
    group: 'Conversations',
    keys: [['⌘', 'W']],
    title: 'Close the selected chat',
    detail: 'Dismisses tracking. Empty chats are discarded; saved messages remain.',
  },
  {
    group: 'Conversations',
    keys: [['⌘', '⌫']],
    title: 'Request deletion',
    detail: 'Enter approves the confirmation; Escape cancels. Approval can permanently delete messages.',
  },
  {
    group: 'Writing & panes',
    keys: [['↵']],
    title: 'Send a message',
    detail: 'In the composer. During a run, sends an additional direction.',
  },
  { group: 'Writing & panes', keys: [['⇧', '↵']], title: 'New line', detail: 'Adds a line without sending.' },
  {
    group: 'Writing & panes',
    keys: [['⌘', 'F']],
    title: 'Focus the active pane',
    detail: 'Toggles focus in the workspace. Escape restores the arrangement.',
  },
  {
    group: 'Writing & panes',
    keys: [['Esc']],
    title: 'Close the current overlay',
    detail: 'Returns from focus or dismisses the active menu or confirmation.',
  },
  {
    group: 'Files & terminal',
    keys: [['⌘', 'P']],
    title: 'Find a file',
    detail: 'With Files open, Command + P opens and focuses file search. Ctrl + P also works.',
  },
  {
    group: 'Files & terminal',
    keys: [['⌘', 'C']],
    title: 'Copy terminal selection',
    detail: 'Select terminal text first. Ctrl + Shift + C also works.',
  },
  {
    group: 'Files & terminal',
    keys: [['⌃', '⇧', 'V']],
    title: 'Paste into the terminal',
    detail: 'Sends clipboard text to the focused terminal.',
  },
];
