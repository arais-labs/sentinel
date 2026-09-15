import {
  MessagesSquare,
  FolderCog,
  Activity,
  Database,
  Zap,
  LayoutGrid,
  CheckCircle,
  Lock,
  GitBranch,
  GitPullRequest,
  Send,
  Settings,
  Terminal,
  Globe,
  Folder,
  type LucideIcon,
} from 'lucide-react';
import { ComponentType } from 'react';

import { SessionsPage } from '../pages/SessionsPage';
import { LogsPage } from '../pages/LogsPage';
import { MemoryPage } from '../pages/MemoryPage';
import { TriggersPage } from '../pages/TriggersPage';
import { ModulesPage } from '../pages/ModulesPage';
import { SettingsPage } from '../pages/SettingsPage';
import { WorkspacesPage } from '../pages/WorkspacesPage';
import { TerminalTab } from '../components/workspace/TerminalTab';
import { DesktopTab } from '../components/workspace/tabs/DesktopTab';
import { PullRequestsTab } from '../pages/PullRequestsTab';
import { FilesTab } from '../pages/FilesTab';

// approvals/permissions reuse ModulesPage but need their section passed
// explicitly, since a pane is not the active route (no section in the URL).
const ModulesTab = () => <ModulesPage section="modules" />;
const ApprovalsTab = () => <ModulesPage section="approvals" />;
const PermissionsTab = () => <ModulesPage section="permissions" />;

/** Stable identifier for each workspace tab. Persisted into the layout. */
export type WorkspaceTabId =
  | 'sessions'
  | 'desktop'
  | 'terminal'
  | 'files'
  | 'pull-requests'
  | 'logs'
  | 'memory'
  | 'triggers'
  | 'modules'
  | 'approvals'
  | 'permissions'
  | 'git'
  | 'telegram'
  | 'workspaces'
  | 'settings';

export interface WorkspaceTab {
  id: WorkspaceTabId;
  label: string;
  icon: LucideIcon;
  component: ComponentType;
  hidden?: boolean;
}

/**
 * Every left-tab option that can be hosted inside a workspace pane. Labels and
 * icons mirror the AppShell sidebar nav. `approvals` and `permissions` reuse
 * ModulesPage but remain distinct tabs (each open at most once).
 */
export const WORKSPACE_TABS: WorkspaceTab[] = [
  { id: 'sessions', label: 'Chat', icon: MessagesSquare, component: SessionsPage },
  { id: 'desktop', label: 'Desktop', icon: Globe, component: DesktopTab },
  { id: 'terminal', label: 'Terminal', icon: Terminal, component: TerminalTab },
  { id: 'files', label: 'Files', icon: Folder, component: FilesTab },
  { id: 'pull-requests', label: 'Pull requests', icon: GitPullRequest, component: PullRequestsTab },
  { id: 'logs', label: 'Session Logs', icon: Activity, component: LogsPage },
  { id: 'memory', label: 'Memory', icon: Database, component: MemoryPage },
  { id: 'triggers', label: 'Triggers', icon: Zap, component: TriggersPage },
  { id: 'modules', label: 'Modules', icon: LayoutGrid, component: ModulesTab },
  { id: 'approvals', label: 'Approvals', icon: CheckCircle, component: ApprovalsTab },
  { id: 'permissions', label: 'Permissions', icon: Lock, component: PermissionsTab },
  { id: 'git', label: 'Git', icon: GitBranch, hidden: true, component: () => <SettingsPage initialSection="git" /> },
  { id: 'telegram', label: 'Telegram', icon: Send, hidden: true, component: () => <SettingsPage initialSection="telegram" /> },
  { id: 'workspaces', label: 'Workspaces', icon: FolderCog, component: WorkspacesPage },
  { id: 'settings', label: 'Settings', icon: Settings, component: SettingsPage },
];

/** Lookup map keyed by tab id for O(1) access from the workspace store. */
export const WORKSPACE_TABS_BY_ID: Record<WorkspaceTabId, WorkspaceTab> =
  WORKSPACE_TABS.reduce(
    (acc, tab) => {
      acc[tab.id] = tab;
      return acc;
    },
    {} as Record<WorkspaceTabId, WorkspaceTab>,
  );

/** Ordered list of every valid tab id. */
export const WORKSPACE_TAB_IDS: WorkspaceTabId[] = WORKSPACE_TABS.map((t) => t.id);

/** Type guard: is the given string a known workspace tab id. */
export function isWorkspaceTabId(value: string): value is WorkspaceTabId {
  return value in WORKSPACE_TABS_BY_ID;
}

/** Resolve a tab id to its registry entry (undefined when unknown). */
export function getWorkspaceTab(id: string): WorkspaceTab | undefined {
  return WORKSPACE_TABS_BY_ID[id as WorkspaceTabId];
}
