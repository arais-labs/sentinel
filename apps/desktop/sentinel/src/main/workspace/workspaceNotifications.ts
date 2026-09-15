import path from 'node:path';
import type { NotificationCenter } from '../app/notifications.js';
import type { WorkspaceSpec } from './workspaceLifecycle.js';

export function publishWorkspaceNotification(center: NotificationCenter, id: string, entry: WorkspaceSpec): void {
  const key = `preparation:${id}`;
  const previous = center.list().find(item => item.source === 'workspaces' && item.key === key);
  if (!previous && !['preparing', 'failed'].includes(entry.state)) return;
  // Ordinary stop/start status is not another preparation alert.
  if (previous && !previous.progress && ['stopped', 'stopping'].includes(entry.state)) return;
  const name = entry.notificationContext?.name || path.basename(entry.project);
  const progress = entry.state === 'preparing' || entry.state === 'stopping';
  const failed = entry.state === 'failed';
  center.publish({ source: 'workspaces', key,
    title: `${name} · ${failed ? 'Needs attention' : entry.state === 'running' ? 'Ready' : progress ? 'Preparing workspace' : 'Preparation stopped'}`,
    message: failed ? entry.error || 'Workspace preparation failed. Open the workspace to retry.'
      : entry.state === 'running' ? 'Your workspace and selected tools are ready.'
      : entry.state === 'stopping' ? 'Stopping workspace preparation…'
      : entry.state === 'stopped' ? 'Workspace preparation was stopped.' : entry.message || 'Preparing your workspace…',
    severity: failed ? 'error' : entry.state === 'running' ? 'success' : 'info', progress,
    target: { workspaceId: id, ...(entry.notificationContext ? { instanceName: entry.notificationContext.instanceName } : {}) },
  });
}
