export type NotificationSeverity = 'info' | 'success' | 'warning' | 'error' | 'urgent';
export interface NotificationTarget { instanceName?: string; sessionId?: string; workspaceId?: string }
export interface NotificationInput {
  source: string;
  key?: string;
  title: string;
  message: string;
  severity?: NotificationSeverity;
  progress?: boolean;
  durationMs?: number;
  target?: NotificationTarget;
}
export interface NotificationPublication { item: AppNotification; meaningful: boolean }
export interface AppNotification extends NotificationInput {
  id: string;
  severity: NotificationSeverity;
  progress: boolean;
  createdAt: string;
  updatedAt: string;
  read: boolean;
  dismissed: boolean;
}
