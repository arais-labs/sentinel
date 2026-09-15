import type { AppNotification } from '../shared/notifications.js';
import { app, BrowserWindow, Notification, Menu, dialog, ipcMain, screen, shell } from 'electron';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { installRendererTransport } from './transport/rendererTransport.js';
import { DesktopManager } from './app/desktopManager.js';
import { validateBackupFolder } from './app/backupReset.js';
import { BACKUP_RESET_ARGUMENT } from './app/backupResetWindow.js';
import { openPreviewWindow } from './app/previewWindow.js';
import { IPC, type CompletionSound, type NotificationSettings, type SessionCompletion, type PendingFormWindow, type DesktopStatus, type PayloadUpdate, type ReleaseChannel } from '../shared/ipc.js';

export function startDesktopApplication(): void {
  const backupRoots = [
    { name: 'app-data', path: app.getPath('userData') },
    { name: 'logs', path: app.getPath('logs') },
  ];
  let mainWindow: BrowserWindow | undefined;
  type FormWindowEntry = { window: BrowserWindow; instanceName: string; formId: string; resolved: boolean };
  let formWindow: FormWindowEntry | undefined;
  const formWindows = new Map<number, FormWindowEntry>();
  const shownForms = new Set<string>();

  const manager = new DesktopManager();
  let activeSentinelOrigin: string | undefined;
  let isQuitting = false;
  let resetInProgress = false;
  let startupInProgress = true;
  let payloadInProgress = false;
  manager.onPayloadProgress(progress => { payloadInProgress = progress.phase !== 'done'; });
  manager.onPayloadFailed(() => { payloadInProgress = false; });
  // The development watcher launches the replacement while the old process shuts down.
  // DesktopManager transfers ownership after that process releases its services.
  const singleInstanceLock = !app.isPackaged || app.requestSingleInstanceLock();

  // Developer Mode exposes explicit bundle installation and backup/reset actions.
  // Toggling the preference itself never changes workspace or app data.
  const desktopSettings = loadDesktopSettings();
  let devMode = desktopSettings.devMode === true;
  let formAlertsEnabled = desktopSettings.formAlertsEnabled !== false;
  let bannersEnabled = desktopSettings.bannersEnabled !== false;
  let inAppBannersEnabled = desktopSettings.inAppBannersEnabled !== false;
  let alertSoundsEnabled = desktopSettings.alertSoundsEnabled !== false;
  let completionSoundsEnabled = desktopSettings.completionSoundsEnabled !== false;
  let completionSound: CompletionSound = desktopSettings.completionSound === 'chime' || desktopSettings.completionSound === 'glass' ? desktopSettings.completionSound : 'soft';
  const completionSnapshots = new Map<string, Map<string, string | null>>();

  function devSettingsPath(): string {
    return path.join(app.getPath('userData'), 'desktop-settings.json');
  }

  function loadDesktopSettings(): { devMode?: boolean; formAlertsEnabled?: boolean; completionSoundsEnabled?: boolean; completionSound?: CompletionSound; bannersEnabled?: boolean; inAppBannersEnabled?: boolean; alertSoundsEnabled?: boolean } {
    try {
      return JSON.parse(readFileSync(devSettingsPath(), 'utf8'));
    } catch {
      return {};
    }
  }

  function setDevMode(value: boolean): void {
    devMode = value;
    try {
      writeFileSync(devSettingsPath(), JSON.stringify({ devMode: value, formAlertsEnabled, completionSoundsEnabled, completionSound, bannersEnabled, inAppBannersEnabled, alertSoundsEnabled }));
    } catch {
      // A failed write only means the preference won't persist; keep the session value.
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(IPC.devModeChanged, value);
    }
    installMenu();
  }

  function preloadPath(): string {
    return path.resolve(import.meta.dirname, '../preload/preload.mjs');
  }

  async function createWindow(): Promise<void> {
    const window = new BrowserWindow({
      width: 1280,
      height: 860,
      minWidth: 640,
      minHeight: 440,
      titleBarStyle: 'hiddenInset',
      trafficLightPosition: { x: 14, y: 14 },
      title: 'Sentinel',
      backgroundColor: '#09090b',
      webPreferences: {
        preload: preloadPath(),
        contextIsolation: true,
        backgroundThrottling: false,
        autoplayPolicy: 'no-user-gesture-required',
        nodeIntegration: false,
        sandbox: false,
      },
    });
    mainWindow = window;

    const sendToWindow = (channel: string, payload: unknown) => {
      if (isQuitting || window.isDestroyed() || window.webContents.isDestroyed()) return;
      window.webContents.send(channel, payload);
    };
    const unsubscribeStatus = manager.onStatus((status) => sendToWindow(IPC.statusChanged, status));
    const unsubscribeLog = manager.onLog((entry) => sendToWindow(IPC.logEntry, entry));
    const unsubscribePayloadProgress = manager.onPayloadProgress((progress) =>
      sendToWindow(IPC.payloadProgress, progress),
    );
    const unsubscribePayloadInstalled = manager.onPayloadInstalled((info) =>
      sendToWindow(IPC.payloadInstalled, info),
    );
    const unsubscribePayloadFailed = manager.onPayloadFailed((failure) =>
      sendToWindow(IPC.payloadFailed, failure),
    );
    window.once('closed', () => {
      unsubscribeStatus();
      unsubscribeLog();
      unsubscribePayloadProgress();
      unsubscribePayloadInstalled();
      unsubscribePayloadFailed();
      if (mainWindow === window) mainWindow = undefined;
    });

    window.webContents.setWindowOpenHandler(({ url }) => {
      if (isSentinelUrl(url)) return { action: 'allow' };
      void shell.openExternal(url);
      return { action: 'deny' };
    });
    window.webContents.on('will-navigate', (event, url) => {
      if (isInternalAppUrl(url)) return;
      event.preventDefault();
      void shell.openExternal(url);
    });
    const url = await manager.prepareUI();
    activeSentinelOrigin = new URL(url).protocol === 'sentinel:' ? 'sentinel://app' : new URL(url).origin;
    await window.loadURL(url);
  }

  async function openFormWindow(instanceName: string, form: PendingFormWindow): Promise<void> {
    const window = new BrowserWindow({
      width: 600, height: 180, minWidth: 420, minHeight: 120,
      frame: false, transparent: true, resizable: false, hasShadow: true,
      title: 'Sentinel — Your input', backgroundColor: '#00000000',
      alwaysOnTop: true, show: false, minimizable: false,
      webPreferences: { preload: preloadPath(), contextIsolation: true, nodeIntegration: false, sandbox: false, backgroundThrottling: false },
    });
    const entry = { window, instanceName, formId: form.formId, resolved: false };
    formWindow = entry;
    formWindows.set(window.webContents.id, entry);
    const contentsId = window.webContents.id;
    if (process.platform === 'darwin') {
      // Keep Sentinel a foreground app: Electron otherwise hides the entire
      // app's Dock icon when making this auxiliary window visible on fullscreen.
      window.setVisibleOnAllWorkspaces(true, {
        visibleOnFullScreen: true,
        skipTransformProcessType: true,
      });
    }
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', event => event.preventDefault());
    window.on('close', event => {
      if (isQuitting || entry.resolved) return;
      event.preventDefault();
      window.webContents.send(IPC.formCloseRequested);
    });
    window.once('closed', () => { formWindows.delete(contentsId); if (formWindow === entry) formWindow = undefined; });
    window.once('ready-to-show', () => { if (!entry.resolved) window.showInactive(); });
    const url = new URL('/form', mainWindow!.webContents.getURL());
    url.search = new URLSearchParams({ instance: instanceName, session: form.sessionId, form: form.formId }).toString();
    try { await window.loadURL(url.toString()); }
    catch (error) { window.destroy(); throw error; }
  }

  async function navigateTo(route: string): Promise<void> {
    await ensureWindow();
    mainWindow!.webContents.send(IPC.navigate, route);
  }

  // Opens a native picker for a locally-built payload tarball and installs it.
  // Returns false when the user cancels.
  async function installPayloadFromFile(): Promise<boolean> {
    if (resetInProgress) throw new Error('A backup and reset is in progress.');
    if (!devMode) throw new Error('Enable Developer Mode to install a PR app bundle.');
    const result = await dialog.showOpenDialog({
      title: 'Install PR app bundle',
      message: 'Select the sentinel-payload tarball from the downloaded PR build artifacts.',
      properties: ['openFile'],
      filters: [{ name: 'Payload archive', extensions: ['gz', 'tgz', 'tar.gz'] }],
    });
    if (result.canceled || result.filePaths.length === 0) return false;
    if (!devMode) throw new Error('Developer Mode was disabled. The bundle was not installed.');
    await manager.installPayloadFromFile(result.filePaths[0]);
    return true;
  }

  async function backupAndResetFromMenu(): Promise<void> {
    if (!devMode || resetInProgress) return;
    resetInProgress = true;
    try {
      const selection = await dialog.showOpenDialog({ title: 'Choose where to back up Sentinel',
        properties: ['openDirectory', 'createDirectory'], buttonLabel: 'Choose backup folder' });
      if (selection.canceled || !selection.filePaths[0]) return;
      const folder = await validateBackupFolder(selection.filePaths[0], backupRoots);
      const confirmation = await dialog.showMessageBox({ type: 'warning',
        message: 'Back up all local Sentinel data and start fresh?',
        detail: `Backup location: ${folder}\n\nSentinel will stop running tasks and local workspaces, then back up and verify conversations, credentials, settings, installed bundles, local workspace storage, and logs before removing them. External project folders and remote machines are untouched.\n\nThe backup contains sensitive data. Keep it private. Sentinel will restart into clean setup and may need to download an app bundle.`,
        buttons: ['Cancel', 'Back up and reset'], defaultId: 0, cancelId: 0, noLink: true });
      if (confirmation.response !== 1 || !devMode) return;
      if (startupInProgress || payloadInProgress) throw new Error('Wait for Sentinel startup or bundle installation to finish, then retry.');
      // Restart into the dedicated backup launch after shutdown: no running
      // Chromium profile, database, or VM can change the files during copying.
      Menu.setApplicationMenu(null);
      isQuitting = true;
      for (const window of BrowserWindow.getAllWindows()) window.destroy();
      await manager.shutdown();
      app.relaunch({ args: [...process.argv.slice(1), `${BACKUP_RESET_ARGUMENT}${folder}`] });
      app.exit(0);
    } finally {
      resetInProgress = false;
    }
  }

  async function ensureWindow(): Promise<void> {
    if (!mainWindow || mainWindow.isDestroyed()) {
      await createWindow();
    }
  }

  function isInternalAppUrl(url: string): boolean {
    return url.startsWith('file://') || isSentinelUrl(url);
  }

  function isSentinelUrl(url: string): boolean {
    if (!activeSentinelOrigin) return false;
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'sentinel:' && parsed.host === 'app') return true;
      return (parsed.protocol === 'sentinel:' ? `${parsed.protocol}//${parsed.host}` : parsed.origin) === activeSentinelOrigin;
    } catch {
      return false;
    }
  }

  function installMenu(): void {
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {
        label: 'Sentinel',
        submenu: [
          { label: 'Instances', click: () => void navigateTo('/desktop/instances') },
          { label: 'Open Sentinel', click: () => void navigateTo('/') },
          { type: 'separator' },
          ...(process.platform === 'darwin' ? [
            { role: 'hide' as const },
            { role: 'hideOthers' as const },
            { role: 'unhide' as const },
            { type: 'separator' as const },
          ] : []),
          { role: 'quit' },
        ],
      },
      {
        label: 'View',
        submenu: [
          { role: 'reload' },
          { role: 'resetZoom' },
          { role: 'zoomIn' },
          { role: 'zoomOut' },
        ],
      },
      {
        label: 'Developer',
        submenu: [
          {
            label: 'Developer Mode',
            type: 'checkbox',
            checked: devMode,
            click: (item) => setDevMode(item.checked),
          },
          ...(devMode ? [{
            label: 'Install PR app bundle…',
            click: () => { void installPayloadFromFile().catch((error) => {
              dialog.showErrorBox('Could not install PR app bundle', error instanceof Error ? error.message : String(error));
            }); },
          }, {
            label: 'Back up and reset Sentinel…',
            click: () => { void backupAndResetFromMenu().catch((error) => {
              dialog.showErrorBox('Could not back up and reset Sentinel', error instanceof Error ? error.message : String(error));
              if (isQuitting) { app.relaunch(); app.exit(1); }
            }); },
          }] : []),
        ],
      },
      {
        label: 'Edit',
        submenu: [
          { role: 'undo' },
          { role: 'redo' },
          { type: 'separator' },
          { role: 'cut' },
          { role: 'copy' },
          { role: 'paste' },
          { role: 'selectAll' },
        ],
      },
      {
        role: 'help',
        submenu: [
          { label: 'Guided Tour…', click: () => { void ensureWindow().then(() => {
            mainWindow!.show();
            mainWindow!.focus();
            mainWindow!.webContents.send(IPC.guidedTour);
          }); } },
        ],
      },
    ]));
  }

  manager.notifications.events.on('changed', items => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(IPC.notificationsChanged, items);
  });
  const lastNotificationAlert = new Map<string, number>();
  manager.notifications.events.on('published', (item: AppNotification, meaningful: boolean) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(IPC.notificationPublished, { item, meaningful });
    if (!meaningful || item.progress || item.source === 'sessions') return;
    const now = Date.now();
    if (now - (lastNotificationAlert.get(item.id) ?? 0) < 60_000) return;
    lastNotificationAlert.set(item.id, now);
    if (lastNotificationAlert.size > 200) lastNotificationAlert.delete(lastNotificationAlert.keys().next().value!);
    const audible = alertSoundsEnabled && ['urgent', 'error', 'warning'].includes(item.severity);
    if (audible && mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(IPC.completionSound, completionSound);
    if (bannersEnabled && !mainWindow?.isFocused() && Notification.isSupported()) {
      const banner = new Notification({ title: item.title, body: item.message, silent: true });
      banner.on('click', () => {
        if (!mainWindow || mainWindow.isDestroyed()) return;
        mainWindow.show(); mainWindow.focus();
        const target = item.target;
        if (target?.instanceName) mainWindow.webContents.send(IPC.navigate,
          `/instances/${encodeURIComponent(target.instanceName)}/${target.sessionId ? `sessions/${target.sessionId}` : 'workspaces'}`);
        manager.notifications.update(item.id, 'read');
      });
      banner.show();
    }
  });

  function registerIpc(): void {
    const handle: typeof ipcMain.handle = (channel, listener) => ipcMain.handle(channel, (event, ...args) => {
      if (resetInProgress) throw new Error('A backup and reset is in progress.');
      if (!event.senderFrame || event.senderFrame !== mainWindow?.webContents.mainFrame || !isSentinelUrl(event.senderFrame.url)) {
        throw new Error('Untrusted desktop request');
      }
      return listener(event, ...args);
    });
    handle(IPC.openPreview, async (_event, href: string) => {
      if (typeof href !== 'string' || !manager.transport) throw new Error('Workspace services are unavailable');
      await openPreviewWindow(href, manager.transport);
    });
    handle(IPC.getNotifications, () => manager.notifications.list());
    handle(IPC.publishNotification, (_event, input) => manager.notifications.publish(input));
    handle(IPC.updateNotification, (_event, id, action) => manager.notifications.update(id, action));
    handle(IPC.getNotificationSettings, () => ({ forms: formAlertsEnabled, completionSounds: completionSoundsEnabled, sound: completionSound, banners: bannersEnabled, inAppBanners: inAppBannersEnabled, alertSounds: alertSoundsEnabled }));
    handle(IPC.setNotificationSettings, (_event, settings: NotificationSettings) => {
      if (!settings || typeof settings.banners !== 'boolean' || typeof settings.inAppBanners !== 'boolean' || typeof settings.alertSounds !== 'boolean' || typeof settings.forms !== 'boolean' || typeof settings.completionSounds !== 'boolean' || !['soft', 'chime', 'glass'].includes(settings.sound)) throw new Error('Invalid notification settings');
      writeFileSync(devSettingsPath(), JSON.stringify({ devMode, formAlertsEnabled: settings.forms, completionSoundsEnabled: settings.completionSounds, completionSound: settings.sound, bannersEnabled: settings.banners, inAppBannersEnabled: settings.inAppBanners, alertSoundsEnabled: settings.alertSounds }));
      bannersEnabled = settings.banners;
      inAppBannersEnabled = settings.inAppBanners;
      alertSoundsEnabled = settings.alertSounds;
      formAlertsEnabled = settings.forms;
      completionSoundsEnabled = settings.completionSounds;
      completionSound = settings.sound;
      if (!formAlertsEnabled) {
        if (formWindow && !formWindow.resolved) formWindow.window.destroy();
        shownForms.clear();
      }
      mainWindow?.webContents.send(IPC.notificationSettingsChanged, settings);
      return settings;
    });
    handle(IPC.syncCompletions, (_event, instanceName: string, sessions: SessionCompletion[]) => {
      if (typeof instanceName !== 'string' || !Array.isArray(sessions) || sessions.some(s => !s || typeof s.sessionId !== 'string' ||
          (s.completionId !== null && typeof s.completionId !== 'string') || typeof s.running !== 'boolean' || typeof s.awaitingInput !== 'boolean')) throw new Error('Invalid completion state');
      const previous = completionSnapshots.get(instanceName);
      const next = new Map<string, string | null>();
      let completed = false;
      for (const session of sessions) {
        const ready = !session.running && !session.awaitingInput;
        const id = ready || session.awaitingInput || !previous?.has(session.sessionId)
          ? session.completionId : previous.get(session.sessionId) ?? null;
        next.set(session.sessionId, id);
        if (previous?.has(session.sessionId) && ready && id && previous.get(session.sessionId) !== id) {
          completed = true;
          manager.notifications.publish({ source: 'sessions', key: `${instanceName}:${session.sessionId}:${id}`, title: 'Agent finished', message: 'The response is ready to read.', severity: 'success', target: { instanceName, sessionId: session.sessionId } });
        }
      }
      completionSnapshots.set(instanceName, next);
      if (completed && completionSoundsEnabled && mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(IPC.completionSound, completionSound);
    });
    handle(IPC.syncForms, async (_event, instanceName: string, forms: PendingFormWindow[], viewedSessionId: string | null) => {
      if (typeof instanceName !== 'string' || !instanceName || !Array.isArray(forms) ||
          forms.some(form => !form || typeof form.sessionId !== 'string' || typeof form.formId !== 'string' || typeof form.title !== 'string')) {
        throw new Error('Invalid pending form state');
      }
      if (formWindow && !formWindow.resolved && formWindow.instanceName === instanceName &&
          !forms.some(form => form.formId === formWindow!.formId)) {
        formWindow.window.destroy();
      }
      if (!formAlertsEnabled) return;
      for (const form of forms) {
        const key = JSON.stringify([instanceName, form.sessionId, form.formId]);
        if (form.sessionId === viewedSessionId && mainWindow?.isFocused()) {
          shownForms.add(key);
          if (formWindow?.instanceName === instanceName && formWindow.formId === form.formId && !formWindow.resolved) {
            formWindow.window.destroy();
          }
          continue;
        }
        if (formWindow || shownForms.has(key)) continue;
        shownForms.add(key);
        try { await openFormWindow(instanceName, form); }
        catch (error) { shownForms.delete(key); throw error; }
      }
    });
    ipcMain.handle(IPC.resizeFormWindow, (event, height: number) => {
      const entry = formWindows.get(event.sender.id);
      if (!entry || event.senderFrame !== event.sender.mainFrame || !event.senderFrame ||
          !isSentinelUrl(event.senderFrame.url) || !Number.isFinite(height)) throw new Error('Invalid form resize');
      const window = entry.window;
      const area = screen.getDisplayMatching(window.getBounds()).workArea;
      // Convert renderer CSS pixels to the screen DIPs used by BrowserWindow.
      const nextHeight = Math.min(Math.max(120, Math.ceil(height * window.webContents.getZoomFactor())), area.height - 40);
      const width = Math.min(600, area.width - 40);
      const bounds = window.getBounds();
      if (bounds.width === width && bounds.height === nextHeight) return;
      window.setBounds({
        width, height: nextHeight,
        x: Math.max(area.x + 20, Math.min(bounds.x, area.x + area.width - width - 20)),
        y: Math.max(area.y + 20, Math.min(bounds.y, area.y + area.height - nextHeight - 20)),
      });
    });
    ipcMain.handle(IPC.finishFormWindow, (event, waitForTurn: boolean) => {
      const entry = formWindows.get(event.sender.id);
      if (!entry || event.sender !== entry.window.webContents || event.senderFrame !== event.sender.mainFrame ||
          !event.senderFrame || !isSentinelUrl(event.senderFrame.url) || typeof waitForTurn !== 'boolean') {
        throw new Error('Untrusted form window request');
      }
      entry.resolved = true;
      if (formWindow === entry) formWindow = undefined;
      if (waitForTurn) entry.window.hide();
      else entry.window.destroy();
    });
    handle(IPC.getStatus, () => manager.getStatus());
    handle(IPC.stopServices, () => manager.stopServices());
    handle(IPC.startServices, () => manager.initialize());
    handle(IPC.revealAppSupport, () => manager.revealAppSupport());
    handle(IPC.openLogFolder, () => manager.openLogFolder());
    handle(IPC.getLogs, () => manager.logs());
    handle(IPC.getPayload, () => manager.getPayload());
    handle(IPC.installPayloadFromFile, () => installPayloadFromFile());
    handle(IPC.getDevMode, () => devMode);
    handle(IPC.checkForUpdate, async (_event, channel?: ReleaseChannel) =>
      manager.checkForUpdate(channel),
    );
    handle(IPC.applyUpdate, async (_event, update: PayloadUpdate) =>
      manager.applyUpdate(update),
    );
  }

  if (!singleInstanceLock) {
    app.quit();
  } else {
    app.on('second-instance', () => {
      if (!mainWindow || mainWindow.isDestroyed()) {
        void createWindow();
        return;
      }
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    });

    app.on('window-all-closed', () => {
      if (process.platform !== 'darwin') {
        app.quit();
      }
    });

    app.on('before-quit', (event) => {
      if (isQuitting) return;
      event.preventDefault();
      isQuitting = true;
      void manager.shutdown().finally(() => app.exit(0));
    });

    if (!app.isPackaged) {
      process.on('SIGTERM', () => app.quit());
      process.on('SIGINT', () => app.quit());
    }

    app.whenReady()
      .then(async () => {
        installRendererTransport(() => manager.transport);
        registerIpc();
        installMenu();
        await createWindow();
        void manager.initialize()
          .catch((error) => {
            const message = String(error?.stack || error);
            console.error(message);

          }).finally(() => { startupInProgress = false; });
        app.on('activate', () => {
          if (BrowserWindow.getAllWindows().length === 0) {
            void createWindow();
          }
        });
      })
      .catch((error) => {
        void shell.openExternal(`data:text/plain,${encodeURIComponent(String(error?.stack || error))}`);
        app.quit();
      });
  }
}
