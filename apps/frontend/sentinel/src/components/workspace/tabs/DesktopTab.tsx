import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Clipboard,
  Download,
  Loader2,
  Monitor,
  Play,
  RefreshCw,
  Square,
  Image,
} from 'lucide-react';
import { api } from '../../../lib/api';
import { desktopResolutions, defaultDesktopGeometry } from '../../../lib/desktop-modes';
import { runtimeUpdateRequirement, type RuntimeUpdateRequirement } from '../../../lib/runtime-compatibility';
import { RuntimeUpdateNotice } from '../../runtime/RuntimeUpdateNotice';
import { useSessionWorkspace } from '../../../hooks/useSessionWorkspace';
import { useActiveSessionId } from '../../../store/active-session-store';
import { useInstanceName, usePaneId } from '../../../lib/workspace-context';
import { clearPaneActions, setPaneActions } from '../../../store/pane-actions-store';
import type { RuntimeLiveView, Workspace } from '../../../types/api';
import { DesktopView } from '../DesktopView';
import { WallpaperChooser } from '../WallpaperChooser';
import { WorkspaceAttachment } from '../../session/WorkspaceAttachment';
import { PaneActions } from '../PaneActions';
import './desktop.css';

const actionClass = 'chat-header-pill inline-flex h-7 items-center gap-2 rounded-md px-2 text-xs text-(--text-secondary) hover:bg-(--surface-2) hover:text-(--text-primary) disabled:opacity-40';

export function DesktopTab() {
  const instance = useInstanceName() ?? null;
  const session = useActiveSessionId();
  const { workspace, loading } = useSessionWorkspace(session, instance);
  if (loading || !workspace || !session || !instance)
    return (
      <div className="workspace-desktop-empty">
        <Monitor size={24} />
        <p>
          {loading
            ? 'Loading workspace…'
            : 'Attach a workspace to this session to use Desktop.'}
        </p>
      </div>
    );
  return (
    <WorkspaceDesktop
      key={`${instance}:${session}:${workspace.id}`}
      workspace={workspace}
      session={session}
      instance={instance}
    />
  );
}

function WorkspaceDesktop({
  workspace,
  session,
  instance,
}: {
  workspace: Workspace;
  session: string;
  instance: string;
}) {
  const prefix = `/instances/${encodeURIComponent(instance)}`;
  const query = `session_id=${encodeURIComponent(session)}`;
  const [desktop, setDesktop] = useState<RuntimeLiveView | null>(null);
  const [error, setError] = useState('');
  const [runtimeUpdate, setRuntimeUpdate] = useState<RuntimeUpdateRequirement>();
  const [busy, setBusy] = useState('');
  const [geometry, setGeometry] = useState<string>(defaultDesktopGeometry);
  const [confirmStop, setConfirmStop] = useState(false);
  const [showWallpapers, setShowWallpapers] = useState(false);
  const clipboardKey = `sentinel:desktop-clipboard:${instance}:${workspace.id}`;
  const [clipboardEnabled, setClipboardEnabled] = useState(() => {
    try { return localStorage.getItem(clipboardKey) !== 'off'; }
    catch { return true; }
  });
  const [clipboardError, setClipboardError] = useState('');
  const toggleClipboard = () => {
    const next = !clipboardEnabled;
    setClipboardEnabled(next);
    setClipboardError('');
    try { localStorage.setItem(clipboardKey, next ? 'on' : 'off'); } catch { /* Session-only when storage is unavailable. */ }
  };
  const busyRef = useRef(false);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    try {
      const result = await api.get<RuntimeLiveView>(
        `${prefix}/runtime/live-view?${query}`,
      );
      if (mounted.current) {
        setDesktop(result);
        setRuntimeUpdate(undefined);
        setError('');
        if (result.geometry) setGeometry(result.geometry);
      }
      return result.state;
    } catch (reason) {
      if (mounted.current) setRuntimeUpdate(runtimeUpdateRequirement(reason));
      if (mounted.current)
        setError(
          reason instanceof Error
            ? reason.message
            : 'Could not reach this workspace.',
        );
      return 'unavailable';
    }
  }, [prefix, query]);
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      clearTimeout(timer);
      if (cancelled || running || document.visibilityState !== 'visible')
        return;
      running = true;
      const state = busyRef.current ? 'preparing' : await refresh();
      running = false;
      if (!cancelled)
        timer = setTimeout(
          poll,
          state === 'preparing' || state === 'stopping' ? 3000 : 15000,
        );
    };
    const visible = () => {
      void poll();
    };
    document.addEventListener('visibilitychange', visible);
    void poll();
    return () => {
      cancelled = true;
      mounted.current = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [refresh]);
  async function action(label: string, operation: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(label);
    setError('');
    try {
      const response = await operation();
      if (
        response &&
        typeof response === 'object' &&
        'available' in response &&
        !(response as RuntimeLiveView).available
      )
        throw new Error(
          (response as RuntimeLiveView).reason || 'Desktop could not start.',
        );
      await refresh();
    } catch (reason) {
      if (mounted.current) setRuntimeUpdate(runtimeUpdateRequirement(reason));
      if (mounted.current)
        setError(
          reason instanceof Error
            ? reason.message
            : 'Desktop operation failed.',
        );
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy('');
    }
  }
  const install = () =>
    action('Installing Desktop…', async () => {
      const current = await api.get<Workspace>(
        `${prefix}/sessions/${session}/workspace`,
      );
      if (current.id !== workspace.id)
        throw new Error('The attached workspace changed. Open Desktop again.');
      const updated = await api.patch<Workspace>(
        `${prefix}/workspaces/${workspace.id}`,
        {
          desktop: 'xfce',
          revision: current.revision,
        },
      );
      if (updated.container_state === 'failed')
        throw new Error(updated.container_error || 'Workspace setup failed.');
    });
  const startWorkspace = () =>
    action('Starting workspace…', async () => {
      const result = await api.post<Workspace>(
        `${prefix}/workspaces/${workspace.id}/start`,
        {},
      );
      if (result.container_state === 'failed')
        throw new Error(result.container_error || 'Workspace could not start.');
    });
  const start = () =>
    action('Starting desktop…', () =>
      api.post(
        `${prefix}/runtime/desktop/start?${query}`,
        { geometry },
        { timeoutMs: 35000 },
      ),
    );
  const stop = () => {
    setConfirmStop(false);
    void action('Stopping desktop…', () =>
      api.post(`${prefix}/runtime/desktop/stop?${query}`, {}),
    );
  };
  const running = !runtimeUpdate && desktop?.state === 'running' && desktop.available;
  const state = desktop?.state;
  const preparing = state === 'preparing' || state === 'stopping';
  const status =
    busy ||
    (preparing
      ? desktop?.reason || 'Preparing workspace…'
      : running
        ? 'Running · Metal'
        : state === 'not_installed'
          ? 'Not installed'
          : state === 'workspace_stopped'
            ? 'Workspace stopped'
            : state === 'stopped'
              ? 'Stopped'
              : desktop || error
                ? 'Unavailable'
                : 'Checking desktop…');
  const emptyTitle =
    busy ||
    (state === 'preparing'
      ? 'Preparing desktop'
      : state === 'stopping'
        ? 'Stopping workspace'
        : state === 'not_installed'
          ? 'Add a desktop to this workspace'
          : state === 'stopped'
            ? 'Desktop is stopped'
            : status);
  const emptyDescription = busy
    ? null
    : state === 'not_installed'
      ? 'Install an XFCE desktop and terminal, or choose Weston in workspace settings. The desktop starts only when needed.'
      : state === 'stopped'
        ? 'Open graphical applications alongside your terminal and files. Sessions attached to this workspace share the same desktop.'
        : desktop?.reason ||
          (error ? 'Use Refresh to check the workspace again.' : null);
  return (
    <section className="workspace-desktop">
      <DesktopPaneControls>
        <div className="workspace-desktop-header-status chat-header-pill" title={status}>
          {busy || preparing || (!desktop && !error)
            ? <Loader2 size={13} className="animate-spin" />
            : <span className="workspace-desktop-status-dot" data-running={running} data-error={!!error} />}
          <span>{status}</span>
        </div>
        <WorkspaceAttachment sessionId={session} instanceName={instance} busy={!!busy} />
        {running && (
          <>
            <button className={actionClass} onClick={() => setShowWallpapers(value => !value)}
              aria-expanded={showWallpapers} title="Choose desktop wallpaper">
              <Image size={13} /><span>Wallpaper</span>
            </button>
            <button
              className={actionClass}
              onClick={toggleClipboard}
              aria-label="Share desktop clipboard"
              aria-pressed={clipboardEnabled}
              title="Share text clipboard with this workspace in both directions while its desktop is focused"
            >
              <Clipboard size={13} />
              <span>Clipboard {clipboardEnabled ? 'on' : 'off'}</span>
            </button>
            <button
              className={actionClass}
              disabled={!!busy}
              onClick={() => setConfirmStop(true)}
              title="Stop desktop applications"
            >
              <Square size={13} className="workspace-desktop-stop-icon" />
              <span>Stop desktop</span>
            </button>
          </>
        )}
        <button
          className={actionClass}
          disabled={!!busy}
          onClick={() => void refresh()}
          aria-label="Refresh desktop status"
          title="Refresh desktop status"
        >
          <RefreshCw size={14} />
          <span>Refresh</span>
        </button>
      </DesktopPaneControls>
      {running && showWallpapers && <WallpaperChooser
        endpoint={`${prefix}/runtime/desktop/wallpaper?${query}`}
        onClose={() => setShowWallpapers(false)} />}
      {error && running && (
        <div className="workspace-desktop-notice" role="alert">
          {error}
        </div>
      )}
      {clipboardEnabled && clipboardError && (
        <div className="workspace-desktop-notice" role="alert">{clipboardError}</div>
      )}
      {confirmStop && (
        <div className="workspace-desktop-notice">
          Stop desktop? Graphical apps will close for all sessions using{' '}
          {workspace.name}.<button onClick={stop}>Stop desktop</button>
          <button onClick={() => setConfirmStop(false)}>Cancel</button>
        </div>
      )}
      <div className="workspace-desktop-content">
        {running && desktop.ws_url ? (
          <DesktopView
            key={desktop.geometry}
            wsUrl={desktop.ws_url}
            clipboardUrl={`${prefix}/runtime/desktop/clipboard?${query}`}
            clipboardEnabled={clipboardEnabled}
            onClipboardError={setClipboardError}
            onDisconnect={() => void refresh()}
          />
        ) : (
          <div className="workspace-desktop-empty">
            {busy || preparing || (!desktop && !error) ? (
              <Loader2 size={25} className="animate-spin" />
            ) : (
              <Monitor size={28} />
            )}
            <h3>{runtimeUpdate ? 'Workspace unavailable' : emptyTitle}</h3>
            {runtimeUpdate ? (
              <RuntimeUpdateNotice requirement={runtimeUpdate} machineId={workspace.machine_id} onUpdated={refresh} />
            ) : error ? (
              <p role="alert">{error}</p>
            ) : emptyDescription && emptyDescription !== emptyTitle && <p>{emptyDescription}</p>}
            {!busy &&
              !runtimeUpdate &&
              !preparing &&
              (state === 'not_installed' ? (
                <button
                  className="workspace-desktop-primary"
                  onClick={() => void install()}
                >
                  <Download size={15} />
                  Install Desktop
                </button>
              ) : state === 'workspace_stopped' || state === 'failed' ? (
                <button
                  className="workspace-desktop-primary"
                  onClick={() => void startWorkspace()}
                >
                  <Play size={15} />
                  {state === 'failed'
                    ? 'Retry workspace setup'
                    : 'Start workspace'}
                </button>
              ) : state === 'stopped' ? (
                <>
                  <label className="workspace-desktop-resolution">
                    Resolution
                    <select
                      aria-label="Desktop resolution"
                      value={geometry}
                      onChange={(event) => setGeometry(event.target.value)}
                    >
                      {desktopResolutions.map((value) => (
                        <option key={value} value={value}>
                          {value.replace('x', ' × ')}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="workspace-desktop-primary"
                    onClick={() => void start()}
                  >
                    <Play size={15} />
                    Start desktop
                  </button>
                </>
              ) : null)}
          </div>
        )}
      </div>
      <footer className="workspace-desktop-footer">
        {running
          ? 'Shared by sessions attached to this workspace. Closing this tab keeps applications running.'
          : 'Stopping the desktop leaves your workspace, terminal, and files available.'}
      </footer>
    </section>
  );
}

/** Register with the shared pane bar, including its overflow and focus controls. */
function DesktopPaneControls({ children }: { children: ReactNode }) {
  const paneId = usePaneId();
  useEffect(() => {
    if (paneId) setPaneActions(paneId, children);
  }, [paneId, children]);
  useEffect(() => {
    if (paneId) return () => clearPaneActions(paneId);
  }, [paneId]);
  return !paneId
    ? <header className="workspace-desktop-toolbar chat-header-actions"><PaneActions>{children}</PaneActions></header>
    : null;
}
