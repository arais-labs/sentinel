import { useCallback, useEffect, useRef, useState } from 'react';
import {
  isSessionStreamOpen,
  sendSessionStreamMessage,
} from '../lib/session-stream';
import {
  getFocusedPane,
  setFocusedPane,
  useFocusedPane,
} from '../store/focused-pane-store';
import { useSessionStream } from './useSessionStream';
import type { WsEvent } from '../types/api';

/**
 * One live terminal surfaced by the runtime. Mirrors the shape SessionsPage has
 * always used to render the terminal strip and pills.
 */
export interface ActivePane {
  id: string;
  windowId: string;
  windowName: string;
  title: string;
  busy: boolean;
  dead: boolean;
  lastCommand: string | null;
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parsePane(raw: unknown): ActivePane | null {
  if (
    !isObjectRecord(raw) ||
    typeof raw.pane_id !== 'string' ||
    typeof raw.window_id !== 'string'
  )
    return null;
  return {
    id: raw.pane_id,
    windowId: raw.window_id,
    windowName:
      typeof raw.window_name === 'string' ? raw.window_name : 'Window',
    title: typeof raw.title === 'string' ? raw.title : '',
    busy: Boolean(raw.busy),
    dead: Boolean(raw.dead),
    lastCommand: typeof raw.command === 'string' ? raw.command : null,
  };
}

export interface UseSessionRuntimeStreamOptions {
  onReconnectFailed?: () => void;
  onEvent?: (event: WsEvent) => void;
}

// Session streams own terminal state. Desktop services belong to the workspace
// and are controlled explicitly by the Desktop tab.
export function useSessionRuntimeStream(
  instanceName: string | null,
  sessionId: string | null,
  options: UseSessionRuntimeStreamOptions = {},
) {
  const [activePanes, setActivePanes] = useState<ActivePane[]>([]);
  const [runtimeBooting, setRuntimeBooting] = useState(false);
  const focusedPaneId = useFocusedPane(sessionId);
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;
  useEffect(() => {
    setActivePanes([]);
    setRuntimeBooting(false);
  }, [instanceName, sessionId]);
  const setFocusedPaneId = useCallback(
    (pane: string | null) => {
      if (sessionId) setFocusedPane(sessionId, pane);
    },
    [sessionId],
  );
  const dropPane = useCallback(
    (paneId: string) => {
      setActivePanes((current) => current.filter((pane) => pane.id !== paneId));
      if (sessionId && getFocusedPane(sessionId) === paneId)
        setFocusedPane(sessionId, null);
    },
    [sessionId],
  );
  const isStreamOpen = useCallback(
    () =>
      Boolean(
        instanceName &&
          sessionId &&
          isSessionStreamOpen(instanceName, sessionId),
      ),
    [instanceName, sessionId],
  );
  const sendMessage = useCallback(
    (payload: unknown) =>
      Boolean(
        instanceName &&
          sessionId &&
          sendSessionStreamMessage(instanceName, sessionId, payload),
      ),
    [instanceName, sessionId],
  );
  const handleEvent = useCallback(
    (event: WsEvent) => {
      if (event.type === 'workspace_changed') {
        setActivePanes([]);
        setRuntimeBooting(false);
        if (sessionId) setFocusedPane(sessionId, null);
        window.dispatchEvent(
          new CustomEvent('sentinel:workspace-changed', {
            detail: { sessionId },
          }),
        );
      }
      if (event.type === 'runtime_ready' || event.type === 'connected')
        setRuntimeBooting(false);
      if (
        (event.type === 'panes_changed' || event.type === 'connected') &&
        Array.isArray(event.panes)
      ) {
        const incoming = (event.panes as unknown[])
          .map(parsePane)
          .filter((pane): pane is ActivePane => pane !== null);
        setActivePanes(incoming);
        const focused = sessionId ? getFocusedPane(sessionId) : null;
        if (
          sessionId &&
          focused &&
          !incoming.some((pane) => pane.id === focused)
        )
          setFocusedPane(sessionId, null);
      }
      onEventRef.current?.(event);
    },
    [sessionId],
  );
  const { connection, runActive } = useSessionStream(instanceName, sessionId, {
    onEvent: handleEvent,
    onReconnectFailed: options.onReconnectFailed,
  });
  return {
    connection,
    runActive,
    isStreamOpen,
    sendMessage,
    activePanes,
    focusedPaneId,
    setFocusedPaneId,
    dropPane,
    runtimeBooting,
    setRuntimeBooting,
  };
}
