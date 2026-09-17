import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiUrl } from '../lib/api';
import { DesktopSocket } from '../lib/desktop-socket';
import { openWorkspaceTab } from '../lib/workspace-navigation';
import { useActiveSessionStore } from '../store/active-session-store';
import { executeSessionLayout, sessionLayoutKey } from '../store/workspace-store';

/** Lives with Voice, not a pane. Answers the Voice agent's layout requests for this window. */
export function useVoiceWorkspace(instance: string | undefined, enabled: boolean): boolean {
  const [connected, setConnected] = useState(false);
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  useEffect(() => {
    if (!instance || !enabled || !window.sentinelDesktop?.socketOpen || !window.sentinelDesktop?.onSocketEvent) return;
    let disposed = false;
    let socket: DesktopSocket | undefined;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const completed = new Set<string>();
    const focusedSession = () => useActiveSessionStore.getState().byInstance[instance] ?? null;
    const context = () => ({
      session_id: focusedSession(),
      open_session_ids: useActiveSessionStore.getState().recentByInstance[instance] ?? [],
    });
    const inspect = () => executeSessionLayout(sessionLayoutKey(instance, focusedSession()), { action: 'inspect' });
    const send = (value: unknown) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
    };
    const ready = () => send({ type: 'layout_ready', ready: !document.hidden });
    const connect = () => {
      if (disposed) return;
      const current = new DesktopSocket(apiUrl(`/instances/${encodeURIComponent(instance)}/voice/ui`));
      socket = current;
      current.onopen = () => {
        if (disposed || socket !== current) return;
        ready();
        setConnected(true);
      };
      current.onclose = () => {
        if (!disposed) {
          setConnected(false);
          reconnect = setTimeout(connect, 1000);
        }
      };
      current.onmessage = async message => {
        if (disposed || socket !== current) return;
        let event: Record<string, unknown>;
        try { event = JSON.parse(String(message.data)); } catch { return; }
        if (event.type !== 'layout_request' || typeof event.request_id !== 'string') return;
        const checkActive = () => {
          if (disposed || socket !== current || current.readyState !== WebSocket.OPEN || document.hidden ||
              typeof event.expires_at !== 'number' || Date.now() >= event.expires_at) {
            throw new Error('Voice UI request expired or the window is no longer active. Inspect again.');
          }
        };
        let result: unknown;
        try {
          checkActive();
          if (completed.has(event.request_id)) throw new Error('This UI request was already handled.');
          completed.add(event.request_id);
          if (completed.size > 100) completed.delete(completed.values().next().value!);
          if (!event.command || typeof event.command !== 'object') throw new Error('Invalid workspace command.');
          const command = event.command as Record<string, unknown>;
          if (command.action === 'switch_session') {
            if (typeof command.session_id !== 'string' || !command.session_id) throw new Error('A session ID is required.');
            // Same navigation as the sidebar: restore this chat's layout, then reveal Chat.
            openWorkspaceTab(navigateRef.current, instance, 'sessions', { sessionId: command.session_id });
            // Dockview binds after React commits the selected session/route. Acknowledge
            // only that actual layout, never the previous session's still-mounted API.
            while (true) {
              checkActive();
              if (focusedSession() !== command.session_id) throw new Error('The selected chat changed. Inspect again.');
              let layout: ReturnType<typeof inspect> | undefined;
              try { layout = inspect(); } catch { /* Wait for the destination layout to bind. */ }
              if (layout) {
                result = { ...layout, ...context(), workspace_visible: true };
                break;
              }
              await new Promise<void>(resolve => setTimeout(resolve, 25));
            }
          } else {
            if (command.session_id != null && command.session_id !== focusedSession()) {
              throw new Error('The focused chat changed. Inspect again before arranging panes.');
            }
            if (command.action === 'inspect') {
              try { result = { ...inspect(), ...context(), workspace_visible: true }; }
              catch { result = { ok: true, ...context(), workspace_visible: false, panes: [], hint: 'Switch to a chat to display its workspace first.' }; }
            } else {
              if (!['apply', 'undo'].includes(String(command.action))) throw new Error('Unknown workspace action.');
              // Voice arranges whichever chat is displayed; a stale explicit chat is rejected above.
              const target = typeof command.session_id === 'string' ? command.session_id : focusedSession();
              if (!target) throw new Error('Switch to a chat to display its workspace first.');
              result = { ...executeSessionLayout(sessionLayoutKey(instance, target), { ...command, session_id: target }), ...context(), workspace_visible: true };
            }
          }
        } catch (error) {
          result = { ok: false, error: error instanceof Error ? error.message : 'Unable to change workspace.' };
        }
        if (!disposed && socket === current) send({ type: 'layout_result', request_id: event.request_id, result });
      };
    };
    connect();
    document.addEventListener('visibilitychange', ready);
    return () => {
      disposed = true;
      setConnected(false);
      clearTimeout(reconnect);
      document.removeEventListener('visibilitychange', ready);
      socket?.close();
    };
  }, [instance, enabled]);

  return connected;
}
