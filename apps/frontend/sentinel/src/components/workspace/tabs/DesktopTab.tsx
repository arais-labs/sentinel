import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Copy,
  Expand,
  ExternalLink,
  Globe,
  HelpCircle,
  Loader2,
  MonitorOff,
  RefreshCw,
  RotateCcw,
  Settings2,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { DesktopPreview } from '../../session/DesktopPreview';
import { useSessionDeleteConfirmation } from '../../session/SessionDeleteConfirmDialog';
import {
  DESKTOP_RESOLUTION_PRESETS,
  useSessionRuntimeStream,
} from '../../../hooks/useSessionRuntimeStream';
import { getSessionDeleteWorkspaceSummary } from '../../../lib/sessionDeletion';
import { instanceRoute } from '../../../lib/routes';
import { useActiveSessionId } from '../../../store/active-session-store';
import { useInstanceName } from '../../../lib/workspace-context';
import type { RuntimeStatusCheck } from '../../../types/api';

/**
 * Standalone Desktop workspace tab.
 *
 * Renders the noVNC interactive view for the workspace-wide active session,
 * mirroring the Runtime → Desktop sub-view in SessionsPage. It consumes the
 * SHARED, ref-counted session stream (`useSessionRuntimeStream`) so opening this
 * tab alongside the chat / other runtime tabs for the same session yields a
 * single WebSocket — zero duplicated stream/live-view logic.
 *
 * Parity with the Runtime Desktop view:
 *   - noVNC connect/disconnect/reconnect (handled inside DesktopPreview);
 *   - fullscreen toggle;
 *   - resolution/geometry select (drives `applyDesktopResolution` + layoutKey);
 *   - maintenance menu (reset browser / restart desktop / wipe workspace);
 *   - runtime health-check diagnostics panel;
 *   - booting state while the runtime spins up;
 *   - per-session live-view fetch (enabled/available/url/ws_url), gated by
 *     `desktopViewActive` — always true here since this tab *is* the surface.
 *   - onFrameLoad / onInteract hooks (the composer-focus behavior is specific to
 *     SessionsPage's chat; here they are harmless no-ops).
 */

/** Selectable resolutions, labelled here from the hook's single preset source. */
const DESKTOP_RESOLUTION_OPTIONS = DESKTOP_RESOLUTION_PRESETS.map((value) => {
  const [w, h] = value.split('x');
  return { value, label: `${w} x ${h}` };
});

const COMMAND_HINT_RE =
  /^(sudo |mkdir |chown |chmod |systemctl |service |brew |apt |apt-get |yum |dnf |pacman |scp |ssh |docker |npm |pip |uv |cargo |go )/;

export function DesktopTab() {
  const activeSessionId = useActiveSessionId();
  const instanceName = useInstanceName() ?? null;
  const navigate = useNavigate();

  const [isDesktopFullscreen, setIsDesktopFullscreen] = useState(false);
  const [resetMenuOpen, setResetMenuOpen] = useState(false);
  const [showPassingRuntimeChecks, setShowPassingRuntimeChecks] = useState(false);
  const [showOptionalRuntimeWarnings, setShowOptionalRuntimeWarnings] = useState(false);
  const resetMenuRef = useRef<HTMLDivElement>(null);

  const { confirmSessionDelete, sessionDeleteConfirmDialog } = useSessionDeleteConfirmation();

  // `desktopViewActive: true` — this tab IS the desktop surface, so the
  // live-view poll + runtime-status fetch should run while it is mounted.
  const {
    liveView,
    isDesktopRuntimeStarting,
    desktopResolution,
    isDesktopResolutionChanging,
    applyDesktopResolution,
    desktopLayoutNonce,
    runtimeStatus,
    runtimeStatusLoading,
    fetchRuntimeStatus,
    runtimeActionBusy,
    resetBrowser,
    restartDesktop,
    wipeWorkspace,
  } = useSessionRuntimeStream(instanceName, activeSessionId, {
    desktopViewActive: true,
  });

  // Close the maintenance menu on any click outside it (the original lived on a
  // `resetMenuRef` guard).
  useEffect(() => {
    if (!resetMenuOpen) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (resetMenuRef.current && !resetMenuRef.current.contains(event.target as Node)) {
        setResetMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [resetMenuOpen]);

  const handleWipeWorkspace = useCallback(() => {
    void wipeWorkspace({
      confirm: async () => {
        const sid = activeSessionId;
        if (!sid) return false;
        const workspaceSummary = await getSessionDeleteWorkspaceSummary(sid);
        return confirmSessionDelete({
          kind: 'workspace_wipe',
          label: 'Session',
          topLevelEntries: workspaceSummary.topLevelEntries,
        });
      },
    });
  }, [wipeWorkspace, activeSessionId, confirmSessionDelete]);

  // onFrameLoad/onInteract drive composer-focus restoration inside
  // SessionsPage's chat. There is no composer in this standalone tab, so they
  // are intentionally no-ops here — kept wired for prop parity.
  const handleDesktopFrameLoad = useCallback(() => {}, []);
  const handleDesktopInteract = useCallback(() => {}, []);

  const isLiveViewReady = Boolean(liveView?.enabled && liveView?.available);

  if (!activeSessionId) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3 text-[color:var(--text-muted)]">
          <MonitorOff size={22} />
        </div>
        <div className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
          No active session
        </div>
        <p className="max-w-[280px] text-[11px] leading-relaxed text-[color:var(--text-muted)]">
          Open a session in the Sessions tab to view its interactive desktop here.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {sessionDeleteConfirmDialog}
      <div
        className={`relative flex items-center justify-between border-b border-[color:var(--border-subtle)] p-3 ${
          isDesktopFullscreen ? 'z-10' : 'z-[110]'
        }`}
      >
        <div className="flex items-center gap-2">
          <Globe size={15} className="text-sky-500" />
          <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
            Interactive View
          </span>
        </div>
        <div className="flex items-center gap-1">
          <select
            value={desktopResolution}
            onChange={(event) => void applyDesktopResolution(event.target.value)}
            disabled={isDesktopResolutionChanging || isDesktopRuntimeStarting}
            className="h-7 max-w-[126px] rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-2 font-mono text-[10px] font-bold text-[color:var(--text-secondary)] outline-none transition-colors hover:bg-[color:var(--surface-2)] disabled:opacity-50"
            title="Desktop resolution"
          >
            {DESKTOP_RESOLUTION_OPTIONS.map((preset) => (
              <option key={preset.value} value={preset.value}>
                {preset.label}
              </option>
            ))}
          </select>
          {/* Maintenance menu */}
          <div className={`relative ${isDesktopFullscreen ? 'z-10' : 'z-[120]'}`} ref={resetMenuRef}>
            <button
              onClick={() => setResetMenuOpen((o) => !o)}
              disabled={runtimeActionBusy}
              className="rounded-md p-1.5 text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--surface-2)] disabled:opacity-50"
              title="Runtime actions"
            >
              {runtimeActionBusy ? (
                <RotateCcw size={14} className="animate-spin" />
              ) : (
                <Settings2 size={14} />
              )}
            </button>
            {resetMenuOpen && (
              <div
                className={`absolute right-0 top-full mt-1 w-48 overflow-hidden rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] shadow-xl ${
                  isDesktopFullscreen ? 'z-10' : 'z-[130]'
                }`}
              >
                <button
                  className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[11px] font-medium text-[color:var(--text-primary)] transition-colors hover:bg-[color:var(--surface-2)]"
                  onClick={() => {
                    setResetMenuOpen(false);
                    void resetBrowser();
                  }}
                >
                  <RotateCcw size={13} className="shrink-0 text-rose-400" />
                  <div>
                    <div className="font-semibold">Reset Browser</div>
                    <div className="text-[9px] text-[color:var(--text-muted)]">Wipe Chrome profile</div>
                  </div>
                </button>
                <div className="h-px bg-[color:var(--border-subtle)]" />
                <button
                  className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[11px] font-medium text-[color:var(--text-primary)] transition-colors hover:bg-[color:var(--surface-2)]"
                  onClick={() => {
                    setResetMenuOpen(false);
                    void restartDesktop();
                  }}
                >
                  <RefreshCw size={13} className="shrink-0 text-amber-400" />
                  <div>
                    <div className="font-semibold">Restart Desktop</div>
                    <div className="text-[9px] text-[color:var(--text-muted)]">Restart VNC session</div>
                  </div>
                </button>
                <div className="h-px bg-[color:var(--border-subtle)]" />
                <button
                  className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[11px] font-medium text-rose-300 transition-colors hover:bg-rose-500/10"
                  onClick={() => {
                    setResetMenuOpen(false);
                    handleWipeWorkspace();
                  }}
                >
                  <Trash2 size={13} className="shrink-0 text-rose-400" />
                  <div>
                    <div className="font-semibold">Wipe Workspace</div>
                    <div className="text-[9px] text-[color:var(--text-muted)]">Delete session files</div>
                  </div>
                </button>
              </div>
            )}
          </div>
          <button
            onClick={() => setIsDesktopFullscreen(true)}
            className="rounded-md p-1.5 text-sky-500 transition-colors hover:bg-[color:var(--surface-2)]"
            title="Open fullscreen"
          >
            <Expand size={14} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="relative aspect-[16/10] w-full overflow-hidden border-b border-[color:var(--border-subtle)] bg-black">
          <DesktopPreview
            url={isLiveViewReady ? liveView!.url : null}
            wsUrl={isLiveViewReady ? liveView!.ws_url : null}
            isFullscreen={isDesktopFullscreen}
            onClose={() => setIsDesktopFullscreen(false)}
            isBooting={isDesktopRuntimeStarting && !isLiveViewReady}
            layoutKey={`desktop-tab:${desktopLayoutNonce}:${liveView?.geometry ?? desktopResolution}`}
            onFrameLoad={handleDesktopFrameLoad}
            onInteract={handleDesktopInteract}
          />
        </div>

        <div className="space-y-4 p-3">
          <section>
            <div className="mb-2.5 px-1 text-[10px] font-bold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">
              Desktop Status
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 p-3 transition-all hover:bg-[color:var(--surface-1)]">
                <span className="text-[10px] font-bold uppercase tracking-wider text-[color:var(--text-secondary)]">
                  Connection
                </span>
                <div className="flex items-center gap-2">
                  {isLiveViewReady ? (
                    <>
                      <div className="h-1.5 w-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]" />
                      <span className="font-mono text-[10px] font-bold text-emerald-500">CONNECTED</span>
                    </>
                  ) : isDesktopRuntimeStarting ? (
                    <>
                      <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
                      <span className="font-mono text-[10px] font-bold text-sky-400">STARTING</span>
                    </>
                  ) : (
                    <>
                      <div className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                      <span className="font-mono text-[10px] font-bold uppercase text-rose-500">
                        {liveView?.enabled ? 'UNREACHABLE' : 'DISABLED'}
                      </span>
                    </>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50 p-3 transition-all hover:bg-[color:var(--surface-1)]">
                <div className="flex items-center justify-between gap-3">
                  <span className="shrink-0 text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                    Stream URL
                  </span>
                  <div className="min-w-0 flex-1 truncate text-right font-mono text-[10px] text-[color:var(--text-secondary)]">
                    {liveView?.ws_url || liveView?.url || '—'}
                  </div>
                </div>
                {liveView?.reason && (
                  <div className="mt-1 truncate text-right text-[9px] font-medium italic leading-relaxed text-amber-500/80">
                    {liveView.reason}
                  </div>
                )}
              </div>
            </div>
          </section>

          {(() => {
            const overall = runtimeStatus?.status;
            const allChecks = runtimeStatus?.checks ?? [];
            const failed = allChecks.filter((c) => c.status === 'fail' || c.status === 'warn');
            const requiredFailures = failed.filter((c) => c.required);
            const optionalWarnings = failed.filter((c) => !c.required);
            const passed = allChecks.filter((c) => c.status === 'pass');
            const skipped = allChecks.filter((c) => c.status === 'skip');
            const totalPassMs = passed.reduce((sum, c) => sum + (c.duration_ms ?? 0), 0);
            const primaryFailure = requiredFailures[0] ?? null;
            const remainingRequiredFailures = requiredFailures.slice(1);
            const suppressPassedRollup = overall === 'unreachable' || overall === 'failed';

            const heroIcon =
              overall === 'ready' ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-400" /> :
              overall === 'degraded' ? <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-400" /> :
              overall === 'unreachable' || overall === 'failed' ? <XCircle size={16} className="mt-0.5 shrink-0 text-rose-500" /> :
              overall === 'not_configured' ? <HelpCircle size={16} className="mt-0.5 shrink-0 text-[color:var(--text-muted)]" /> :
              <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-[color:var(--text-muted)]" />;

            const heroLabel =
              overall === 'ready' ? 'Ready' :
              overall === 'degraded' ? 'Degraded' :
              overall === 'unreachable' ? 'Unreachable' :
              overall === 'failed' ? 'Failed' :
              overall === 'not_configured' ? 'Not configured' :
              runtimeStatusLoading ? 'Checking…' : 'Unknown';

            const heroBorder =
              overall === 'ready' ? 'border-emerald-500/30 bg-emerald-500/5' :
              overall === 'degraded' ? 'border-amber-500/30 bg-amber-500/5' :
              overall === 'unreachable' || overall === 'failed' ? 'border-rose-500/30 bg-rose-500/5' :
              'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/50';

            const targetLine = runtimeStatus?.runtime
              ? [
                  runtimeStatus.runtime.name,
                  runtimeStatus.runtime.host && `${runtimeStatus.runtime.host}:${runtimeStatus.runtime.port ?? 22}`,
                ].filter(Boolean).join(' · ')
              : null;

            const settingsHref = instanceName ? instanceRoute(instanceName, 'settings') : '/';
            const looksLikeCommand = (text: string) => COMMAND_HINT_RE.test(text.trim());

            const renderFixCallout = (check: RuntimeStatusCheck) => {
              const isConfigFailure = check.id.startsWith('config_');
              const hintIsCommand = check.hint ? looksLikeCommand(check.hint) : false;
              if (!check.hint && !isConfigFailure) return null;
              return (
                <div className="space-y-1.5 rounded-md border-l-2 border-amber-500/50 bg-[color:var(--surface-1)]/40 py-1.5 pl-2 pr-1.5">
                  {check.hint && hintIsCommand ? (
                    <div className="flex items-start gap-1.5">
                      <code className="flex-1 break-all font-mono text-[10px] leading-snug text-[color:var(--text-primary)]">
                        {check.hint}
                      </code>
                      <button
                        type="button"
                        onClick={() => {
                          void navigator.clipboard.writeText(check.hint ?? '');
                          toast.success('Copied');
                        }}
                        className="shrink-0 rounded p-0.5 text-[color:var(--text-muted)] transition-colors hover:text-[color:var(--accent-solid)]"
                        title="Copy to clipboard"
                      >
                        <Copy size={10} />
                      </button>
                    </div>
                  ) : check.hint ? (
                    <div className="text-[10px] leading-snug text-[color:var(--text-secondary)]">{check.hint}</div>
                  ) : null}
                  {isConfigFailure && (
                    <button
                      type="button"
                      onClick={() => navigate(settingsHref)}
                      className="inline-flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] transition-opacity hover:opacity-70"
                    >
                      Open Settings <ExternalLink size={9} />
                    </button>
                  )}
                </div>
              );
            };

            return (
              <section className="space-y-2">
                {/* Hero card — absorbs the primary required failure when one exists */}
                <div className={`rounded-lg border px-2.5 py-2 ${heroBorder}`}>
                  <div className="flex items-start gap-2">
                    {heroIcon}
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[12px] font-bold text-[color:var(--text-primary)]">{heroLabel}</span>
                        {targetLine && (
                          <span className="truncate font-mono text-[10px] text-[color:var(--text-muted)]">
                            {targetLine}
                          </span>
                        )}
                      </div>
                      {primaryFailure ? (
                        <>
                          <div className="text-[10px] leading-snug text-[color:var(--text-secondary)]">
                            <span className="font-semibold text-[color:var(--text-primary)]">
                              {primaryFailure.label}
                            </span>{' '}
                            failed
                            {typeof primaryFailure.duration_ms === 'number' &&
                              primaryFailure.duration_ms >= 500 && (
                                <span className="font-mono text-[color:var(--text-muted)]">
                                  {' '}
                                  · {primaryFailure.duration_ms}ms
                                </span>
                              )}
                          </div>
                          {primaryFailure.detail && (
                            <div className="break-words font-mono text-[10px] leading-snug text-[color:var(--text-secondary)]">
                              {primaryFailure.detail}
                            </div>
                          )}
                          {renderFixCallout(primaryFailure)}
                        </>
                      ) : (
                        (runtimeStatus?.summary || runtimeStatusLoading) && (
                          <div className="text-[10px] leading-snug text-[color:var(--text-secondary)]">
                            {runtimeStatus?.summary ?? 'Checking runtime status…'}
                          </div>
                        )
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => void fetchRuntimeStatus()}
                      disabled={runtimeStatusLoading}
                      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text-primary)] disabled:opacity-50"
                      title="Refresh runtime diagnostics"
                    >
                      <RefreshCw size={11} className={runtimeStatusLoading ? 'animate-spin' : ''} />
                    </button>
                  </div>
                </div>

                {/* Additional required failures, if any (rare — usually there's just one). */}
                {remainingRequiredFailures.length > 0 && (
                  <div className="space-y-1.5">
                    {remainingRequiredFailures.map((check) => (
                      <div
                        key={check.id}
                        className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-2.5 py-2"
                      >
                        <div className="flex items-start gap-2">
                          <XCircle size={13} className="mt-0.5 shrink-0 text-rose-500" />
                          <div className="min-w-0 flex-1 space-y-1">
                            <div className="text-[11px] font-bold text-[color:var(--text-primary)]">
                              {check.label}
                            </div>
                            {check.detail && (
                              <div className="break-words font-mono text-[10px] leading-snug text-[color:var(--text-secondary)]">
                                {check.detail}
                              </div>
                            )}
                            {renderFixCallout(check)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Optional warnings — collapsed roll-up */}
                {optionalWarnings.length > 0 && (
                  <div className="space-y-1">
                    <button
                      type="button"
                      onClick={() => setShowOptionalRuntimeWarnings((v) => !v)}
                      className="flex w-full items-center justify-between gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-left transition-all hover:bg-amber-500/10"
                    >
                      <div className="flex min-w-0 items-center gap-1.5">
                        <AlertCircle size={11} className="shrink-0 text-amber-400" />
                        <span className="text-[10px] font-medium text-[color:var(--text-secondary)]">
                          {optionalWarnings.length} optional capabilit
                          {optionalWarnings.length === 1 ? 'y' : 'ies'} missing
                        </span>
                      </div>
                      <ChevronDown
                        size={11}
                        className={`shrink-0 text-[color:var(--text-muted)] transition-transform ${
                          showOptionalRuntimeWarnings ? 'rotate-180' : ''
                        }`}
                      />
                    </button>
                    {showOptionalRuntimeWarnings && (
                      <div className="space-y-1 rounded-lg bg-[color:var(--surface-1)]/30 px-2 py-1.5">
                        {optionalWarnings.map((check) => (
                          <div key={check.id} className="flex items-start gap-1.5 py-0.5">
                            <div className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-amber-400" />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-baseline gap-1.5">
                                <span className="text-[10px] font-semibold text-[color:var(--text-secondary)]">
                                  {check.label}
                                </span>
                                {check.detail && (
                                  <span className="truncate font-mono text-[9px] text-[color:var(--text-muted)]">
                                    {check.detail}
                                  </span>
                                )}
                              </div>
                              {check.hint && (
                                <div className="text-[9px] leading-snug text-[color:var(--text-muted)]">
                                  {check.hint}
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Passing checks collapsed — hidden when the runtime is unreachable/failed */}
                {passed.length > 0 && !suppressPassedRollup && (
                  <div className="space-y-1">
                    <button
                      type="button"
                      onClick={() => setShowPassingRuntimeChecks((v) => !v)}
                      className="flex w-full items-center justify-between gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]/40 px-2.5 py-1.5 text-left transition-all hover:bg-[color:var(--surface-1)]"
                    >
                      <div className="flex min-w-0 items-center gap-1.5">
                        <CheckCircle2 size={11} className="shrink-0 text-emerald-400" />
                        <span className="text-[10px] font-medium text-[color:var(--text-secondary)]">
                          {passed.length} check{passed.length === 1 ? '' : 's'} passed
                          {totalPassMs > 0 ? ` · ${totalPassMs}ms` : ''}
                        </span>
                      </div>
                      <ChevronDown
                        size={11}
                        className={`shrink-0 text-[color:var(--text-muted)] transition-transform ${
                          showPassingRuntimeChecks ? 'rotate-180' : ''
                        }`}
                      />
                    </button>
                    {showPassingRuntimeChecks && (
                      <div className="space-y-0.5 rounded-lg bg-[color:var(--surface-1)]/30 px-2 py-1.5">
                        {passed.map((check) => (
                          <div key={check.id} className="flex items-center gap-1.5 py-0.5">
                            <div className="h-1 w-1 shrink-0 rounded-full bg-emerald-500" />
                            <span className="min-w-0 flex-1 truncate text-[10px] text-[color:var(--text-secondary)]">
                              {check.label}
                            </span>
                            {typeof check.duration_ms === 'number' && check.duration_ms >= 100 && (
                              <span className="shrink-0 font-mono text-[9px] text-[color:var(--text-muted)]">
                                {check.duration_ms}ms
                              </span>
                            )}
                          </div>
                        ))}
                        {skipped.map((check) => (
                          <div key={check.id} className="flex items-center gap-1.5 py-0.5 opacity-50">
                            <div className="h-1 w-1 shrink-0 rounded-full bg-[color:var(--text-muted)]" />
                            <span className="min-w-0 flex-1 truncate text-[10px] text-[color:var(--text-muted)]">
                              {check.label} (skipped)
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Empty / loading states */}
                {!runtimeStatus && runtimeStatusLoading && allChecks.length === 0 && (
                  <div className="py-3 text-center text-[10px] text-[color:var(--text-muted)] opacity-60">
                    Checking runtime…
                  </div>
                )}
                {!runtimeStatus && !runtimeStatusLoading && (
                  <div className="py-3 text-center text-[10px] text-[color:var(--text-muted)] opacity-40">
                    No diagnostics yet.
                  </div>
                )}

                {/* Footer metadata */}
                {runtimeStatus?.runtime &&
                  (runtimeStatus.runtime.username || runtimeStatus.runtime.workspaces_dir) && (
                    <div className="space-y-0 px-1 pt-0.5 font-mono text-[9px] text-[color:var(--text-muted)]">
                      <div className="truncate">
                        {[
                          runtimeStatus.os !== 'unknown' ? runtimeStatus.os : null,
                          runtimeStatus.sandbox !== 'unknown' && runtimeStatus.sandbox !== 'unavailable'
                            ? `${runtimeStatus.sandbox} sandbox`
                            : null,
                          runtimeStatus.runtime.username && runtimeStatus.runtime.host
                            ? `${runtimeStatus.runtime.username}@${runtimeStatus.runtime.host}:${
                                runtimeStatus.runtime.port ?? 22
                              }`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                      {runtimeStatus.runtime.workspaces_dir && (
                        <div className="truncate">{runtimeStatus.runtime.workspaces_dir}</div>
                      )}
                    </div>
                  )}
              </section>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
