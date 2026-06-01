import { wsSessionsBaseUrl } from './env';
import type { WsConnectionState, WsEvent } from '../types/api';

/**
 * Shared session WebSocket stream manager
 * ---------------------------------------
 * A single, ref-counted WebSocket per (instanceName, sessionId). Every consumer
 * — the chat stream in SessionsPage, the runtime hooks, and future standalone
 * runtime tabs — subscribes to the SAME connection through {@link subscribeSessionStream}.
 * The socket opens on the first subscriber and closes after the last one leaves,
 * so opening Desktop + Terminal + Files panes for one session still uses ONE
 * socket rather than three.
 *
 * Behavior mirrors the previous inline SessionsPage WS exactly:
 *   - connection states: connecting → connected, reconnecting on retry;
 *   - reconnect uses exponential backoff (2^attempt seconds, capped at 20s) and
 *     gives up after 8 attempts, invoking the registered `onReconnectFailed`;
 *   - a generation counter drops events from stale sockets after a reconnect.
 *
 * Parsed `WsEvent`s are fanned out to event listeners; connection-state changes
 * to state listeners. Listeners are plain callbacks so React and non-React code
 * can both consume the stream; the `useSessionStream` hook wraps this for React.
 */

export type SessionStreamEventListener = (event: WsEvent) => void;
export type SessionStreamStateListener = (state: WsConnectionState) => void;
export type SessionStreamReconnectFailedListener = () => void;

interface StreamEntry {
  key: string;
  instanceName: string;
  sessionId: string;
  ws: WebSocket | null;
  /** Increment on every (re)connect attempt; events from older sockets are dropped. */
  generation: number;
  connection: WsConnectionState;
  reconnectAttempts: number;
  reconnectTimer: number | null;
  intentionalClose: boolean;
  eventListeners: Set<SessionStreamEventListener>;
  stateListeners: Set<SessionStreamStateListener>;
  reconnectFailedListeners: Set<SessionStreamReconnectFailedListener>;
}

const RECONNECT_MAX_ATTEMPTS = 8;
const RECONNECT_MAX_DELAY_SECONDS = 20;

const entries = new Map<string, StreamEntry>();

function streamKey(instanceName: string, sessionId: string): string {
  return `${instanceName}::${sessionId}`;
}

function setConnection(entry: StreamEntry, state: WsConnectionState): void {
  if (entry.connection === state) return;
  entry.connection = state;
  for (const listener of entry.stateListeners) {
    listener(state);
  }
}

function emitEvent(entry: StreamEntry, event: WsEvent): void {
  for (const listener of entry.eventListeners) {
    listener(event);
  }
}

function clearReconnectTimer(entry: StreamEntry): void {
  if (entry.reconnectTimer !== null) {
    window.clearTimeout(entry.reconnectTimer);
    entry.reconnectTimer = null;
  }
}

function teardownSocket(entry: StreamEntry): void {
  if (entry.ws) {
    entry.ws.onopen = null;
    entry.ws.onmessage = null;
    entry.ws.onclose = null;
    entry.ws.onerror = null;
    try {
      entry.ws.close();
    } catch {
      /* socket may already be closing */
    }
    entry.ws = null;
  }
}

function scheduleReconnect(entry: StreamEntry): void {
  entry.reconnectAttempts += 1;
  if (entry.reconnectAttempts > RECONNECT_MAX_ATTEMPTS) {
    for (const listener of entry.reconnectFailedListeners) {
      listener();
    }
    return;
  }
  const delaySeconds = Math.min(2 ** entry.reconnectAttempts, RECONNECT_MAX_DELAY_SECONDS);
  entry.reconnectTimer = window.setTimeout(() => {
    entry.reconnectTimer = null;
    connect(entry);
  }, delaySeconds * 1000);
}

function connect(entry: StreamEntry): void {
  // Tear down any prior socket without firing reconnect.
  entry.intentionalClose = true;
  clearReconnectTimer(entry);
  teardownSocket(entry);
  entry.intentionalClose = false;

  setConnection(entry, entry.reconnectAttempts > 0 ? 'reconnecting' : 'connecting');

  const generation = ++entry.generation;
  const ws = new WebSocket(
    `${wsSessionsBaseUrl(entry.instanceName)}/${entry.sessionId}/stream`,
  );
  entry.ws = ws;

  ws.onopen = () => {
    if (generation !== entry.generation || ws !== entry.ws) return;
    entry.reconnectAttempts = 0;
    setConnection(entry, 'connected');
  };

  ws.onmessage = (messageEvent) => {
    if (generation !== entry.generation || ws !== entry.ws) return;
    try {
      const payload = JSON.parse(messageEvent.data) as WsEvent;
      emitEvent(entry, payload);
    } catch {
      /* ignore malformed frames */
    }
  };

  ws.onclose = () => {
    if (generation !== entry.generation || ws !== entry.ws) return;
    entry.ws = null;
    if (!entry.intentionalClose) {
      scheduleReconnect(entry);
    }
  };
}

function disposeEntry(entry: StreamEntry): void {
  entry.intentionalClose = true;
  entry.generation += 1;
  clearReconnectTimer(entry);
  teardownSocket(entry);
  setConnection(entry, 'disconnected');
  entries.delete(entry.key);
}

export interface SessionStreamSubscription {
  /** Current connection state at subscription time (re-read via `onState`). */
  readonly connection: WsConnectionState;
  /** Detach this subscriber; closes the socket once the last subscriber leaves. */
  unsubscribe: () => void;
}

export interface SessionStreamHandlers {
  onEvent?: SessionStreamEventListener;
  onState?: SessionStreamStateListener;
  /** Fired once when reconnect attempts are exhausted (mirrors the old toast). */
  onReconnectFailed?: SessionStreamReconnectFailedListener;
}

/**
 * Subscribe to the shared stream for a session. Opens the socket on the first
 * subscriber; closes it when the last subscriber unsubscribes. Returns the
 * current connection state plus an `unsubscribe` disposer.
 */
export function subscribeSessionStream(
  instanceName: string,
  sessionId: string,
  handlers: SessionStreamHandlers,
): SessionStreamSubscription {
  const key = streamKey(instanceName, sessionId);
  let entry = entries.get(key);
  const isFirstSubscriber = !entry;
  if (!entry) {
    entry = {
      key,
      instanceName,
      sessionId,
      ws: null,
      generation: 0,
      connection: 'disconnected',
      reconnectAttempts: 0,
      reconnectTimer: null,
      intentionalClose: false,
      eventListeners: new Set(),
      stateListeners: new Set(),
      reconnectFailedListeners: new Set(),
    };
    entries.set(key, entry);
  }

  if (handlers.onEvent) entry.eventListeners.add(handlers.onEvent);
  if (handlers.onState) entry.stateListeners.add(handlers.onState);
  if (handlers.onReconnectFailed) entry.reconnectFailedListeners.add(handlers.onReconnectFailed);

  if (isFirstSubscriber) {
    connect(entry);
  } else if (handlers.onState) {
    // Bring a late subscriber in sync with the live connection state.
    handlers.onState(entry.connection);
  }

  const activeEntry = entry;
  return {
    get connection() {
      return activeEntry.connection;
    },
    unsubscribe: () => {
      if (handlers.onEvent) activeEntry.eventListeners.delete(handlers.onEvent);
      if (handlers.onState) activeEntry.stateListeners.delete(handlers.onState);
      if (handlers.onReconnectFailed) {
        activeEntry.reconnectFailedListeners.delete(handlers.onReconnectFailed);
      }
      const hasSubscribers =
        activeEntry.eventListeners.size > 0 ||
        activeEntry.stateListeners.size > 0 ||
        activeEntry.reconnectFailedListeners.size > 0;
      if (!hasSubscribers) {
        disposeEntry(activeEntry);
      }
    },
  };
}

/** Non-reactive read of a session's current connection state. */
export function getSessionStreamConnection(
  instanceName: string,
  sessionId: string,
): WsConnectionState {
  return entries.get(streamKey(instanceName, sessionId))?.connection ?? 'disconnected';
}

/** Whether the shared socket for a session is currently OPEN and writable. */
export function isSessionStreamOpen(instanceName: string, sessionId: string): boolean {
  const entry = entries.get(streamKey(instanceName, sessionId));
  return entry?.ws?.readyState === WebSocket.OPEN;
}

/**
 * Send a JSON payload over the shared socket for a session. Returns true if the
 * socket was OPEN and the frame was written, false otherwise (caller decides
 * how to surface a closed connection).
 */
export function sendSessionStreamMessage(
  instanceName: string,
  sessionId: string,
  payload: unknown,
): boolean {
  const entry = entries.get(streamKey(instanceName, sessionId));
  if (entry?.ws?.readyState !== WebSocket.OPEN) return false;
  entry.ws.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
  return true;
}
