import { useEffect, useRef, useState } from 'react';

import {
  subscribeSessionStream,
  type SessionStreamReconnectFailedListener,
} from '../lib/session-stream';
import type { WsConnectionState, WsEvent } from '../types/api';

export interface UseSessionStreamOptions {
  /** Called for every parsed event on the shared socket. */
  onEvent?: (event: WsEvent) => void;
  /** Called once when reconnect attempts are exhausted. */
  onReconnectFailed?: SessionStreamReconnectFailedListener;
}

export interface UseSessionStreamResult {
  /** Live connection state of the shared socket for this session. */
  connection: WsConnectionState;
}

/**
 * React subscription to the shared, ref-counted session WebSocket. Multiple
 * hooks/components subscribing to the same (instanceName, sessionId) share ONE
 * underlying socket — opening on the first subscriber, closing on the last.
 *
 * Handlers are stored in refs and read through stable wrappers, so the
 * subscription only re-runs when the session/instance changes (not on every
 * render or handler identity change). This keeps the connection stable across
 * parent re-renders.
 */
export function useSessionStream(
  instanceName: string | null,
  sessionId: string | null,
  options: UseSessionStreamOptions = {},
): UseSessionStreamResult {
  const [connection, setConnection] = useState<WsConnectionState>('disconnected');

  const onEventRef = useRef(options.onEvent);
  const onReconnectFailedRef = useRef(options.onReconnectFailed);
  useEffect(() => {
    onEventRef.current = options.onEvent;
  }, [options.onEvent]);
  useEffect(() => {
    onReconnectFailedRef.current = options.onReconnectFailed;
  }, [options.onReconnectFailed]);

  useEffect(() => {
    if (!instanceName || !sessionId) {
      setConnection('disconnected');
      return undefined;
    }

    const subscription = subscribeSessionStream(instanceName, sessionId, {
      onEvent: (event) => onEventRef.current?.(event),
      onState: (state) => setConnection(state),
      onReconnectFailed: () => onReconnectFailedRef.current?.(),
    });
    setConnection(subscription.connection);

    return () => {
      subscription.unsubscribe();
      setConnection('disconnected');
    };
  }, [instanceName, sessionId]);

  return { connection };
}
