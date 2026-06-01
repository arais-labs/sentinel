import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { api } from '../lib/api';
import { isSessionStreamOpen, sendSessionStreamMessage } from '../lib/session-stream';
import { useSessionStream } from './useSessionStream';
import type {
  RuntimeActionResponse,
  RuntimeLiveView,
  RuntimeStatusResponse,
  WsConnectionState,
  WsEvent,
} from '../types/api';

/**
 * One live terminal surfaced by the runtime. Mirrors the shape SessionsPage has
 * always used to render the terminal strip and pills.
 */
export interface ActiveTerminal {
  id: string;
  /** Backend-supplied label, used only as a fallback for auto-allocated terminals. */
  label: string | null;
  createdBy: 'agent' | 'user';
  createdAt: number;
  auto: boolean;
  busy: boolean;
  /** Most recent command run in this terminal (for tooltip + rail header). */
  lastCommand: string | null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseTerminal(raw: unknown): ActiveTerminal | null {
  if (!isObjectRecord(raw)) return null;
  const id = typeof raw.terminal_id === 'string' ? raw.terminal_id : null;
  if (!id) return null;
  return {
    id,
    label: typeof raw.label === 'string' ? raw.label : null,
    createdBy: raw.created_by === 'user' ? 'user' : 'agent',
    createdAt: typeof raw.created_at === 'number' ? raw.created_at : Date.now() / 1000,
    auto: Boolean(raw.auto),
    busy: false,
    lastCommand: typeof raw.last_command === 'string' ? raw.last_command : null,
  };
}

export interface UseSessionRuntimeStreamOptions {
  /**
   * Fired when a terminal opens and the pill row was previously empty (i.e. the
   * first terminal of the session). Lets the consumer implement its own
   * auto-focus rule without baking UI concerns into the hook.
   */
  onFirstTerminalOpened?: () => void;
  /**
   * Whether the desktop/live-view surface is currently visible. Gates the
   * live-view poll + runtime-status fetch exactly as SessionsPage did.
   */
  desktopViewActive?: boolean;
  /** Fired when reconnect attempts are exhausted (mirrors the old toast). */
  onReconnectFailed?: () => void;
  /**
   * Extra raw-event tap. The runtime hook handles terminal/runtime_ready events
   * itself; the consumer uses this to drive its own (chat) state from the same
   * shared socket without opening a second connection.
   */
  onEvent?: (event: WsEvent) => void;
}

/**
 * Hooks for the destructive `wipeWorkspace` action. The network call + booting
 * side effects live in the hook, but the confirmation prompt and any
 * file-browser refresh are UI concerns owned by the consumer.
 */
export interface WipeWorkspaceOptions {
  /**
   * Resolve `true` to proceed with the wipe, `false` to cancel. When omitted the
   * wipe runs immediately (no confirmation). Returning `false` short-circuits
   * before `runtimeActionBusy` is set.
   */
  confirm?: () => Promise<boolean> | boolean;
  /** Fired after a successful wipe (e.g. to refresh a file browser). */
  onWiped?: (sessionId: string) => void;
}

export interface UseSessionRuntimeStreamResult {
  // connection
  connection: WsConnectionState;
  /** Whether the shared socket is OPEN and writable. */
  isStreamOpen: () => boolean;
  /** Send a JSON payload over the shared socket; false if not OPEN. */
  sendMessage: (payload: unknown) => boolean;

  // terminals
  activeTerminals: ActiveTerminal[];
  focusedTerminalId: string | null;
  setFocusedTerminalId: (terminalId: string | null) => void;
  /**
   * Optimistically drop a terminal pill (e.g. on a user-initiated close) and
   * clear focus if it was focused. The authoritative `terminal_closed` event is
   * idempotent against this.
   */
  dropTerminal: (terminalId: string) => void;

  // live view
  liveView: RuntimeLiveView | null;
  setLiveView: React.Dispatch<React.SetStateAction<RuntimeLiveView | null>>;
  runtimeBooting: boolean;
  setRuntimeBooting: React.Dispatch<React.SetStateAction<boolean>>;
  /** Desktop is starting when live view is enabled-but-unavailable, or booting. */
  isDesktopRuntimeStarting: boolean;
  fetchLiveView: () => Promise<void>;

  // desktop resolution
  desktopResolution: string;
  /** Local-only set + persist (no POST). Used by the validation guard. */
  setDesktopResolution: (geometry: string) => void;
  /** Whether a resolution-change POST is in flight. */
  isDesktopResolutionChanging: boolean;
  /** Persist + POST a new geometry to the runtime, updating live view. */
  applyDesktopResolution: (geometry: string) => Promise<void>;
  /** Layout nonce, bumped on resolution change so DesktopPreview's RFB remounts. */
  desktopLayoutNonce: number;

  // runtime status
  runtimeStatus: RuntimeStatusResponse | null;
  runtimeStatusLoading: boolean;
  fetchRuntimeStatus: () => Promise<void>;

  // per-session runtime maintenance
  /** True while any of the maintenance actions below is in flight. */
  runtimeActionBusy: boolean;
  /** Wipe the runtime's Chrome profile and re-fetch the live view. */
  resetBrowser: () => Promise<void>;
  /** Restart the VNC desktop at the current resolution and re-fetch live view. */
  restartDesktop: () => Promise<void>;
  /** Delete the session's runtime workspace files (optionally confirmed first). */
  wipeWorkspace: (options?: WipeWorkspaceOptions) => Promise<void>;
}

const DEFAULT_DESKTOP_RESOLUTION = '1920x1200';
const DESKTOP_RESOLUTION_STORAGE_KEY = 'sentinel-desktop-resolution';

/**
 * Single source of truth for selectable desktop geometries. Consumers derive
 * their own option labels from these so the list can't drift between the hook
 * (validation guard) and the tab (resolution `<select>`).
 */
export const DESKTOP_RESOLUTION_PRESETS = [
  '1280x800',
  '1440x900',
  '1680x1050',
  '1920x1200',
  '2560x1600',
  '2880x1800',
  '3840x2400',
];

/**
 * Owns the per-session runtime state derived from the shared WS stream:
 *   - the live terminal list + focus (terminal_opened/closed/busy + connected);
 *   - the noVNC live-view payload + booting state (poll on visibility,
 *     re-fetch on runtime_ready);
 *   - runtime status checks.
 *
 * The WebSocket itself is shared and ref-counted via {@link useSessionStream},
 * so SessionsPage and the future standalone runtime tabs that consume this hook
 * share ONE socket per session. `onEvent` lets a consumer (SessionsPage) feed
 * its chat state from the same stream.
 */
export function useSessionRuntimeStream(
  instanceName: string | null,
  sessionId: string | null,
  options: UseSessionRuntimeStreamOptions = {},
): UseSessionRuntimeStreamResult {
  const desktopViewActive = options.desktopViewActive ?? false;

  const [activeTerminals, setActiveTerminals] = useState<ActiveTerminal[]>([]);
  const [focusedTerminalId, setFocusedTerminalId] = useState<string | null>(null);
  const [liveView, setLiveView] = useState<RuntimeLiveView | null>(null);
  const [runtimeBooting, setRuntimeBooting] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatusResponse | null>(null);
  const [runtimeStatusLoading, setRuntimeStatusLoading] = useState(false);
  const [desktopResolution, setDesktopResolutionState] = useState(
    () => localStorage.getItem(DESKTOP_RESOLUTION_STORAGE_KEY) || DEFAULT_DESKTOP_RESOLUTION,
  );
  const [isDesktopResolutionChanging, setIsDesktopResolutionChanging] = useState(false);
  const [desktopLayoutNonce, setDesktopLayoutNonce] = useState(0);
  const [runtimeActionBusy, setRuntimeActionBusy] = useState(false);

  // Refs so stream callbacks read current values without re-subscribing.
  const sessionIdRef = useRef<string | null>(sessionId);
  const instanceNameRef = useRef<string | null>(instanceName);
  const desktopResolutionRef = useRef(desktopResolution);
  const onFirstTerminalOpenedRef = useRef(options.onFirstTerminalOpened);
  const onEventRef = useRef(options.onEvent);
  // Guards the maintenance actions against re-entry without re-creating the
  // ref-stable callbacks each time `runtimeActionBusy` flips.
  const runtimeActionBusyRef = useRef(false);
  // Flipped false on unmount so the long-lived `runtime_ready` retry loop can't
  // setState after the component is gone.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);
  useEffect(() => {
    instanceNameRef.current = instanceName;
  }, [instanceName]);
  useEffect(() => {
    desktopResolutionRef.current = desktopResolution;
  }, [desktopResolution]);
  useEffect(() => {
    onFirstTerminalOpenedRef.current = options.onFirstTerminalOpened;
  }, [options.onFirstTerminalOpened]);
  useEffect(() => {
    onEventRef.current = options.onEvent;
  }, [options.onEvent]);

  const fetchLiveView = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) {
      setLiveView(null);
      setRuntimeBooting(false);
      return;
    }
    try {
      const query = new URLSearchParams({ session_id: sid, geometry: desktopResolutionRef.current });
      const payload = await api.get<RuntimeLiveView>(`/runtime/live-view?${query.toString()}`);
      if (sid !== sessionIdRef.current) return;
      setLiveView(payload);
      if (payload.enabled && payload.available) {
        setRuntimeBooting(false);
      } else if (payload.enabled) {
        setRuntimeBooting(true);
      } else {
        setRuntimeBooting(false);
      }
    } catch {
      if (sid !== sessionIdRef.current) return;
      setLiveView(null);
    }
  }, []);

  const fetchRuntimeStatus = useCallback(async () => {
    setRuntimeStatusLoading(true);
    try {
      const payload = await api.get<RuntimeStatusResponse>('/runtime/status');
      setRuntimeStatus(payload);
    } catch {
      setRuntimeStatus(null);
    } finally {
      setRuntimeStatusLoading(false);
    }
  }, []);

  const setDesktopResolution = useCallback((geometry: string) => {
    setDesktopResolutionState(geometry);
    localStorage.setItem(DESKTOP_RESOLUTION_STORAGE_KEY, geometry);
  }, []);

  const dropTerminal = useCallback((terminalId: string) => {
    setActiveTerminals((current) => current.filter((t) => t.id !== terminalId));
    setFocusedTerminalId((current) => (current === terminalId ? null : current));
  }, []);

  const isStreamOpen = useCallback(() => {
    const inst = instanceNameRef.current;
    const sid = sessionIdRef.current;
    if (!inst || !sid) return false;
    return isSessionStreamOpen(inst, sid);
  }, []);

  const sendMessage = useCallback((payload: unknown) => {
    const inst = instanceNameRef.current;
    const sid = sessionIdRef.current;
    if (!inst || !sid) return false;
    return sendSessionStreamMessage(inst, sid, payload);
  }, []);

  // Fall back to the default if a persisted resolution is no longer a preset.
  useEffect(() => {
    if (!DESKTOP_RESOLUTION_PRESETS.includes(desktopResolution)) {
      setDesktopResolutionState(DEFAULT_DESKTOP_RESOLUTION);
      localStorage.setItem(DESKTOP_RESOLUTION_STORAGE_KEY, DEFAULT_DESKTOP_RESOLUTION);
    }
  }, [desktopResolution]);

  const applyDesktopResolution = useCallback(
    async (geometry: string) => {
      if (isDesktopResolutionChanging || geometry === desktopResolutionRef.current) return;
      const sid = sessionIdRef.current;
      if (!sid) return;
      setDesktopResolutionState(geometry);
      localStorage.setItem(DESKTOP_RESOLUTION_STORAGE_KEY, geometry);
      setIsDesktopResolutionChanging(true);
      setRuntimeBooting(true);
      setDesktopLayoutNonce((value) => value + 1);
      try {
        const query = new URLSearchParams({ session_id: sid });
        const payload = await api.post<RuntimeLiveView>(
          `/runtime/live-view/resolution?${query.toString()}`,
          { geometry },
          { timeoutMs: 60_000 },
        );
        setLiveView(payload);
        if (payload.enabled && payload.available) {
          setRuntimeBooting(false);
          setDesktopLayoutNonce((value) => value + 1);
        } else {
          toast.error(payload.reason || 'Desktop resolution change failed');
        }
      } catch {
        toast.error('Failed to change desktop resolution');
        setRuntimeBooting(false);
      } finally {
        setIsDesktopResolutionChanging(false);
      }
    },
    [isDesktopResolutionChanging],
  );

  const beginRuntimeAction = useCallback(() => {
    runtimeActionBusyRef.current = true;
    setRuntimeActionBusy(true);
  }, []);

  const endRuntimeAction = useCallback(() => {
    runtimeActionBusyRef.current = false;
    setRuntimeActionBusy(false);
  }, []);

  const resetBrowser = useCallback(async () => {
    if (runtimeActionBusyRef.current) return;
    const sid = sessionIdRef.current;
    if (!sid) return;
    beginRuntimeAction();
    try {
      await api.post<RuntimeActionResponse>(`/runtime/browser/reset?session_id=${sid}`, {});
      toast.success('Browser reset');
      await fetchLiveView();
    } catch {
      toast.error('Failed to reset browser');
    } finally {
      endRuntimeAction();
    }
  }, [beginRuntimeAction, endRuntimeAction, fetchLiveView]);

  const restartDesktop = useCallback(async () => {
    if (runtimeActionBusyRef.current) return;
    const sid = sessionIdRef.current;
    if (!sid) return;
    beginRuntimeAction();
    setRuntimeBooting(true);
    setLiveView(null);
    try {
      await api.post<RuntimeActionResponse>(
        `/runtime/desktop/restart?session_id=${sid}`,
        { geometry: desktopResolutionRef.current },
        { timeoutMs: 60_000 },
      );
      toast.success('Desktop restarted');
      await fetchLiveView();
    } catch {
      toast.error('Failed to restart desktop');
      setRuntimeBooting(false);
    } finally {
      endRuntimeAction();
    }
  }, [beginRuntimeAction, endRuntimeAction, fetchLiveView]);

  const wipeWorkspace = useCallback(
    async (wipeOptions: WipeWorkspaceOptions = {}) => {
      if (runtimeActionBusyRef.current) return;
      const sid = sessionIdRef.current;
      if (!sid) return;
      // Confirmation is a UI concern — let the consumer gate the destructive
      // action before we flip the busy flag or touch any state.
      if (wipeOptions.confirm) {
        const confirmed = await wipeOptions.confirm();
        if (!confirmed) return;
      }
      beginRuntimeAction();
      setRuntimeBooting(true);
      setLiveView(null);
      try {
        await api.post<RuntimeActionResponse>(
          `/runtime/workspace/wipe?session_id=${sid}`,
          {},
          { timeoutMs: 90_000 },
        );
        toast.success('Workspace wiped');
        await fetchLiveView();
        wipeOptions.onWiped?.(sid);
      } catch {
        toast.error('Failed to wipe workspace');
        setRuntimeBooting(false);
      } finally {
        endRuntimeAction();
      }
    },
    [beginRuntimeAction, endRuntimeAction, fetchLiveView],
  );

  // Reset terminal + live-view state and (re)fetch when the session changes.
  useEffect(() => {
    setActiveTerminals([]);
    setFocusedTerminalId(null);
    if (!sessionId) {
      setLiveView(null);
      setRuntimeBooting(false);
      return;
    }
    setRuntimeBooting(true);
    void fetchLiveView();
  }, [sessionId, fetchLiveView]);

  // Live-view poll while the desktop surface is visible and still booting.
  useEffect(() => {
    if (!sessionId || !desktopViewActive) return undefined;
    if (!runtimeBooting && liveView?.enabled && liveView.available) return undefined;

    let cancelled = false;
    const refresh = async () => {
      if (cancelled) return;
      await fetchLiveView();
    };
    const interval = window.setInterval(() => {
      void refresh();
    }, 2000);
    void refresh();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [sessionId, desktopViewActive, runtimeBooting, liveView?.enabled, liveView?.available, fetchLiveView]);

  // Runtime status fetch on desktop visibility / instance change.
  useEffect(() => {
    if (!desktopViewActive) return;
    void fetchRuntimeStatus();
  }, [desktopViewActive, instanceName, fetchRuntimeStatus]);

  const handleEvent = useCallback((event: WsEvent) => {
    switch (event.type) {
      case 'connected': {
        if (Array.isArray(event.terminals)) {
          // Replace (not merge) so stale terminals from a previous session
          // never linger after a reload or backend restart.
          const incoming = (event.terminals as unknown[])
            .map(parseTerminal)
            .filter((item): item is ActiveTerminal => item !== null);
          setActiveTerminals(incoming);
        }
        break;
      }
      case 'terminal_opened': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        const label = typeof event.label === 'string' ? event.label : null;
        const createdBy = event.created_by === 'user' ? 'user' : 'agent';
        const auto = Boolean(event.auto);
        let wasFirstTerminal = false;
        setActiveTerminals((current) => {
          if (current.some((t) => t.id === id)) {
            return current.map((t) => (t.id === id ? { ...t, label: t.label || label } : t));
          }
          wasFirstTerminal = current.length === 0;
          return [
            ...current,
            { id, label, createdBy, createdAt: Date.now() / 1000, auto, busy: false, lastCommand: null },
          ];
        });
        if (wasFirstTerminal) {
          onFirstTerminalOpenedRef.current?.();
        }
        break;
      }
      case 'terminal_closed': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        setActiveTerminals((current) => current.filter((t) => t.id !== id));
        setFocusedTerminalId((current) => (current === id ? null : current));
        break;
      }
      case 'terminal_busy': {
        const id = typeof event.terminal_id === 'string' ? event.terminal_id : null;
        if (!id) break;
        const busy = Boolean(event.busy);
        const lastCommand = typeof event.last_command === 'string' ? event.last_command : undefined;
        setActiveTerminals((current) =>
          current.map((t) =>
            t.id === id
              ? { ...t, busy, lastCommand: lastCommand !== undefined ? lastCommand : t.lastCommand }
              : t,
          ),
        );
        break;
      }
      case 'runtime_ready': {
        // noVNC may need a moment after the container reports ready — retry a
        // few times before giving up on the booting flag.
        void (async () => {
          for (let attempt = 0; attempt < 6; attempt++) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            if (!mountedRef.current) return;
            const sid = sessionIdRef.current;
            if (!sid) break;
            try {
              const query = new URLSearchParams({ session_id: sid, geometry: desktopResolutionRef.current });
              const payload = await api.get<RuntimeLiveView>(`/runtime/live-view?${query.toString()}`);
              if (!mountedRef.current || sid !== sessionIdRef.current) return;
              setLiveView(payload);
              if (payload.enabled && payload.available) {
                setRuntimeBooting(false);
                return;
              }
            } catch {
              /* retry */
            }
          }
          if (!mountedRef.current) return;
          const sid = sessionIdRef.current;
          if (!sid) {
            setRuntimeBooting(false);
          }
        })();
        break;
      }
      default:
        break;
    }
    // Forward every event to the consumer (chat state lives there).
    onEventRef.current?.(event);
  }, []);

  const { connection } = useSessionStream(instanceName, sessionId, {
    onEvent: handleEvent,
    onReconnectFailed: options.onReconnectFailed,
  });

  const isDesktopRuntimeStarting = useMemo(
    () => Boolean(liveView?.enabled && !liveView.available) || runtimeBooting,
    [liveView?.enabled, liveView?.available, runtimeBooting],
  );

  return {
    connection,
    isStreamOpen,
    sendMessage,
    activeTerminals,
    focusedTerminalId,
    setFocusedTerminalId,
    dropTerminal,
    liveView,
    setLiveView,
    runtimeBooting,
    setRuntimeBooting,
    isDesktopRuntimeStarting,
    fetchLiveView,
    desktopResolution,
    setDesktopResolution,
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
  };
}
