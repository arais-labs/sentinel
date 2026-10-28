import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Clipboard,
  Download,
  Loader2,
  Monitor,
  Play,
  RefreshCw,
  Image,
} from 'lucide-react';
import { api } from '../../../lib/api';
import { runtimeUpdateRequirement, type RuntimeUpdateRequirement } from '../../../lib/runtime-compatibility';
import { RuntimeUpdateNotice } from '../../environment/RuntimeUpdateNotice';
import { useSessionWorkspace } from '../../../hooks/useSessionWorkspace';
import { useActiveSessionId } from '../../../store/active-session-store';
import { useInstanceName, usePaneId } from '../../../lib/workspace-context';
import { clearPaneActions, setPaneActions } from '../../../store/pane-actions-store';
import type { WorkspaceCatalog, WorkspaceDesktopStatus, Workspace } from '../../../types/api';
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
  const desktopPath = `${prefix}/sessions/${encodeURIComponent(session)}/workspace/desktop`;
  const [desktop, setDesktop] = useState<WorkspaceDesktopStatus | null>(null);
  const [error, setError] = useState('');
  const [runtimeUpdate, setRuntimeUpdate] = useState<RuntimeUpdateRequirement>();
  const [busy, setBusy] = useState('');
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
      const result = await api.get<WorkspaceDesktopStatus>(
        desktopPath,
      );
      if (mounted.current) {
        setDesktop(result);
        setRuntimeUpdate(undefined);
        setError('');
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
  }, [desktopPath]);
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
          state === 'preparing' || state === 'stopping' || state === 'starting' ? 3000 : 15000,
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
        !(response as WorkspaceDesktopStatus).available
      )
        throw new Error(
          (response as WorkspaceDesktopStatus).reason || 'Desktop could not start.',
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
      const catalog = await api.get<WorkspaceCatalog>(
        `/machines/${current.machine_id}/development-tools`,
      );
      const choice = catalog.desktops.find((item) =>
        item.distributions.includes(current.distribution),
      );
      if (!choice)
        throw new Error('No desktop is available for this operating system.');
      const updated = await api.patch<Workspace>(
        `${prefix}/workspaces/${workspace.id}`,
        {
          desktop: choice.id,
          revision: current.revision,
        },
      );
      if (updated.state === 'failed')
        throw new Error(updated.error || 'Workspace setup failed.');
    });
  const startWorkspace = () =>
    action('Starting workspace…', async () => {
      const result = await api.post<Workspace>(
        `${prefix}/workspaces/${workspace.id}/start`,
        {},
      );
      if (result.state === 'failed')
        throw new Error(result.error || 'Workspace could not start.');
    });
  const running = !runtimeUpdate && desktop?.state === 'running' && desktop.available;
  // The machine's screen while it starts: from power on, through setup, until the desktop runs.
  const watching = !runtimeUpdate && !running && desktop?.viewable === true;
  const state = desktop?.state;
  const preparing = state === 'preparing' || state === 'stopping' || state === 'starting';
  const status =
    busy ||
    (preparing
      ? desktop?.reason || 'Preparing workspace…'
      : running
        ? 'Running'
        : state === 'not_installed'
          ? 'Not installed'
          : state === 'workspace_stopped'
            ? 'Workspace stopped'
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
          : status);
  const emptyDescription = busy
    ? null
    : state === 'not_installed'
      ? 'Install the default desktop, or choose another desktop in workspace settings. It runs with the workspace, shared by every session attached to it.'
      : desktop?.reason || (error ? 'Use Refresh to check the workspace again.' : null);
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
        endpoint={`${desktopPath}/wallpaper`}
        onClose={() => setShowWallpapers(false)} />}
      {error && running && (
        <div className="workspace-desktop-notice" role="alert">
          {error}
        </div>
      )}
      {clipboardEnabled && clipboardError && (
        <div className="workspace-desktop-notice" role="alert">{clipboardError}</div>
      )}
      <div className="workspace-desktop-content">
        {running || watching ? (
          <DesktopView
            viewerPath={`${desktopPath}/viewer`}
            clipboardUrl={`${desktopPath}/clipboard`}
            clipboardEnabled={clipboardEnabled && running}
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
              ) : null)}
          </div>
        )}
      </div>
      <footer className="workspace-desktop-footer">
        {running
          ? 'Shared by sessions attached to this workspace. Closing this tab keeps applications running.'
          : 'The desktop starts and stops with its workspace.'}
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
