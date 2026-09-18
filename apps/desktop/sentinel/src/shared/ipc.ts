import type { AppNotification, NotificationInput, NotificationPublication } from './notifications.js';
export type ServiceName = 'backend' | 'frontend';

export type ServiceState = 'stopped' | 'starting' | 'running' | 'stopping' | 'failed';

export interface ManagedServiceStatus {
  name: ServiceName;
  state: ServiceState;
  pid?: number;
  message?: string;
  startedAt?: string;
  exitedAt?: string;
  exitCode?: number | null;
}

export type ReleaseChannel = 'stable' | 'beta';

// Describes the app payload currently installed in userData. The shell DMG includes the React UI but no backend payload, so a fresh install reports installed=false until one is
// loaded from file or downloaded.
export interface PayloadInfo {
  installed: boolean;
  version: string | null;
  channel: ReleaseChannel | null;
  commit: string | null;
  builtAt: string | null;
}

// A payload that needs a newer shell than the one running; the shell is only
// updated by installing the DMG from the release page.
export interface ShellUpdate {
  version: string;
  minShellVersion: string;
  url: string;
}

export interface DesktopStatus {
  appUrl?: string;
  ready: boolean;
  shellVersion?: string;
  shellUpdate?: ShellUpdate | null;
  preparing?: boolean;
  payloadProgress?: PayloadProgress;
  development: boolean;
  operation?: 'starting' | 'stopping';
  error?: string;
  appSupportPath: string;
  payload: PayloadInfo;
  services: ManagedServiceStatus[];
}

export interface LogEntry {
  service: ServiceName | 'manager';
  line: string;
  at: string;
}

// A newer payload available on a release channel, discovered by comparing the
// installed manifest against the channel's release index.
export interface PayloadUpdate {
  channel: ReleaseChannel;
  version: string;
  commit: string;
  url: string;
  sha256: string;
  hasNewMigrations: boolean;
  shellUpdate: ShellUpdate | null;
}

export type PayloadPhase =
  | 'download'
  | 'verify'
  | 'extract'
  | 'swap'
  | 'restart'
  | 'health-check'
  | 'done';

export interface PayloadProgress {
  phase: PayloadPhase;
  message: string;
  fractionComplete?: number;
}

export interface PayloadFailure {
  phase: PayloadPhase;
  reason: string;
}

export type SocketEvent = { id: string } & (
  | { type: 'open' }
  | { type: 'message'; data: string | Uint8Array }
  | { type: 'close'; code: number; reason: string }
  | { type: 'error' }
);

export type CompletionSound = 'soft' | 'chime' | 'glass';
export interface NotificationSettings { forms: boolean; completionSounds: boolean; sound: CompletionSound; banners: boolean; inAppBanners: boolean; alertSounds: boolean; }
export interface SessionCompletion { sessionId: string; completionId: string | null; running: boolean; awaitingInput: boolean; }

export interface PendingFormWindow { sessionId: string; formId: string; title: string; }
export interface PaneWindowRequest { instance: string; session: string | null; tabId: string; paneId: string; title: string; }
export interface PaneWindowClosed { instance: string; session: string | null; tabId: string; }

export interface MicrophonePermission {
  status: 'not-determined' | 'granted' | 'denied' | 'restricted' | 'unknown';
  platform: string;
  appName: string;
  canOpenSettings: boolean;
}

export interface DesktopApi {
  getMicrophonePermission(): Promise<MicrophonePermission>;
  requestMicrophonePermission(): Promise<MicrophonePermission>;
  openMicrophoneSettings(): Promise<void>;
  getNotifications(): Promise<AppNotification[]>;
  publishNotification(input: NotificationInput): Promise<AppNotification>;
  onNotificationPublished(listener: (publication: NotificationPublication) => void): () => void;
  updateNotification(id: string | null, action: 'read' | 'dismiss'): Promise<void>;
  onNotifications(listener: (items: AppNotification[]) => void): () => void;
  openPreview(href: string): Promise<void>;
  syncForms(instanceName: string, forms: PendingFormWindow[], viewedSessionId: string | null): Promise<void>;
  onCompletionSound(listener: (sound: CompletionSound) => void): () => void;
  onNotificationSettings(listener: (settings: NotificationSettings) => void): () => void;
  getNotificationSettings(): Promise<NotificationSettings>;
  setNotificationSettings(settings: NotificationSettings): Promise<NotificationSettings>;
  syncCompletions(instanceName: string, sessions: SessionCompletion[]): Promise<void>;
  resizeFormWindow(height: number): Promise<void>;
  finishFormWindow(waitForTurn: boolean): Promise<void>;
  onFormCloseRequested(listener: () => void): () => void;
  openPaneWindow(request: PaneWindowRequest): Promise<void>;
  dockPaneWindow(): Promise<void>;
  onPaneWindowClosed(listener: (info: PaneWindowClosed) => void): () => void;
  socketOpen(id: string, url: string): Promise<void>;
  socketSend(id: string, data: string | Uint8Array): void;
  socketClose(id: string, code?: number, reason?: string): void;
  onSocketEvent(listener: (event: SocketEvent) => void): () => void;
  getStatus(): Promise<DesktopStatus>;
  stopServices(): Promise<DesktopStatus>;
  startServices(): Promise<DesktopStatus>;
  onGuidedTour?(listener: () => void): () => void;
  onNavigate(listener: (path: string) => void): () => void;
  revealAppSupport(): Promise<void>;
  openLogFolder(): Promise<void>;
  getLogs(): Promise<LogEntry[]>;
  getPayload(): Promise<PayloadInfo>;
  installPayloadFromFile(): Promise<boolean>;
  getDevMode(): Promise<boolean>;
  checkForUpdate(channel?: ReleaseChannel): Promise<PayloadUpdate | null>;
  applyUpdate(update: PayloadUpdate): Promise<void>;
  onStatus(listener: (status: DesktopStatus) => void): () => void;
  onDevModeChanged(listener: (devMode: boolean) => void): () => void;
  onLog(listener: (entry: LogEntry) => void): () => void;
  onPayloadProgress(listener: (progress: PayloadProgress) => void): () => void;
  onPayloadInstalled(listener: (info: PayloadInfo) => void): () => void;
  onPayloadFailed(listener: (failure: PayloadFailure) => void): () => void;
}

export const IPC = {
  getMicrophonePermission: 'desktop:getMicrophonePermission',
  requestMicrophonePermission: 'desktop:requestMicrophonePermission',
  openMicrophoneSettings: 'desktop:openMicrophoneSettings',
  getNotifications: 'desktop:getNotifications',
  publishNotification: 'desktop:publishNotification',
  notificationPublished: 'desktop:notificationPublished',
  updateNotification: 'desktop:updateNotification',
  notificationsChanged: 'desktop:notificationsChanged',
  openPreview: 'desktop:openPreview',
  syncForms: 'desktop:syncForms',
  notificationSettingsChanged: 'desktop:notificationSettingsChanged',
  getNotificationSettings: 'desktop:getNotificationSettings',
  setNotificationSettings: 'desktop:setNotificationSettings',
  syncCompletions: 'desktop:syncCompletions',
  completionSound: 'desktop:completionSound',
  finishFormWindow: 'desktop:finishFormWindow',
  resizeFormWindow: 'desktop:resizeFormWindow',
  formCloseRequested: 'desktop:formCloseRequested',
  openPaneWindow: 'desktop:openPaneWindow',
  dockPaneWindow: 'desktop:dockPaneWindow',
  paneWindowClosed: 'desktop:paneWindowClosed',
  socketOpen: 'desktop:socketOpen',
  socketSend: 'desktop:socketSend',
  socketClose: 'desktop:socketClose',
  socketEvent: 'desktop:socketEvent',
  getStatus: 'desktop:getStatus',
  stopServices: 'desktop:stopServices',
  startServices: 'desktop:startServices',
  navigate: 'desktop:navigate',
  guidedTour: 'desktop:guided-tour',
  revealAppSupport: 'desktop:revealAppSupport',
  openLogFolder: 'desktop:openLogFolder',
  getLogs: 'desktop:getLogs',
  getPayload: 'desktop:getPayload',
  installPayloadFromFile: 'desktop:installPayloadFromFile',
  getDevMode: 'desktop:getDevMode',
  checkForUpdate: 'desktop:checkForUpdate',
  applyUpdate: 'desktop:applyUpdate',
  statusChanged: 'desktop:statusChanged',
  devModeChanged: 'desktop:devModeChanged',
  logEntry: 'desktop:logEntry',
  payloadProgress: 'desktop:payloadProgress',
  payloadInstalled: 'desktop:payloadInstalled',
  payloadFailed: 'desktop:payloadFailed',
} as const;
