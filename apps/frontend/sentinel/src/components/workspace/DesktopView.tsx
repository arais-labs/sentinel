import { RetainedSessionContext } from '../../lib/workspace-context-values';
import { Loader2, RefreshCw } from 'lucide-react';
import { useContext, useEffect, useRef, useState } from 'react';
import { DesktopSocket } from '../../lib/desktop-socket';
import { DesktopVideo } from '../../lib/desktop-video';
import { attachDesktopInput } from '../../lib/desktop-video-input';
import { shareDesktopClipboard } from '../../lib/desktop-clipboard';

export function DesktopView({ wsUrl, clipboardUrl, clipboardEnabled = false, onClipboardError, onDisconnect }: {
  wsUrl: string;
  clipboardUrl: string;
  clipboardEnabled?: boolean;
  onClipboardError?: (message: string) => void;
  onDisconnect: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const [retry, setRetry] = useState(0);
  const visible = useContext(RetainedSessionContext)?.visible ?? true;
  const player = useRef<DesktopVideo | null>(null);
  const clipboard = useRef<ReturnType<typeof shareDesktopClipboard> | null>(null);
  const options = useRef({ visible, clipboardEnabled, onClipboardError, onDisconnect });
  options.current = { visible, clipboardEnabled, onClipboardError, onDisconnect };
  useEffect(() => {
    player.current?.setVisible(visible);
    clipboard.current?.setEnabled(clipboardEnabled && visible);
    if (!visible) canvas.current?.blur();
  }, [visible, clipboardEnabled]);
  useEffect(() => {
    if (!canvas.current) return;
    setState('connecting');
    let detach: (() => void) | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    let socket: DesktopSocket | WebSocket | undefined;
    const lost = () => {
      if (disposed) return;
      clearTimeout(timer);
      detach?.();
      clipboard.current?.dispose();
      clipboard.current = null;
      setState('disconnected');
      options.current.onDisconnect();
    };
    try {
      socket = window.sentinelDesktop ? new DesktopSocket(wsUrl) : new WebSocket(wsUrl);
      const video = new DesktopVideo(canvas.current, socket, () => {
        clearTimeout(timer);
        if (!disposed) setState('connected');
      }, lost);
      player.current = video;
      const input = attachDesktopInput(canvas.current, video);
      detach = input;
      clipboard.current = shareDesktopClipboard(canvas.current, input, clipboardUrl, message => options.current.onClipboardError?.(message));
      clipboard.current.setEnabled(options.current.clipboardEnabled && options.current.visible);
      video.setVisible(options.current.visible);
      timer = setTimeout(() => { video.close(); lost(); }, 15000);
    } catch { socket?.close(); lost(); }
    return () => {
      disposed = true;
      clearTimeout(timer);
      clipboard.current?.dispose(); clipboard.current = null;
      detach?.();
      player.current?.close(); player.current = null;
    };
  }, [wsUrl, clipboardUrl, retry]);
  return <div className="relative h-full min-h-0 w-full bg-(--app-bg) dark:bg-[#0c0c0e]">
    <canvas ref={canvas} tabIndex={0} aria-label="Workspace desktop"
      className={`block h-full w-full object-contain outline-none${state === 'connected' && visible ? ' data-[guest-pointer]:cursor-none' : ''}`} />
    {state !== 'connected' && <div className="absolute inset-0 flex items-center justify-center bg-(--app-bg)/90">
      <div className="flex items-center gap-3 text-sm text-(--text-secondary)" role="status">
        {state === 'connecting' ? <><Loader2 size={16} className="animate-spin" />Connecting to desktop…</> :
          <>Connection lost<button className="inline-flex items-center gap-2" onClick={() => setRetry(value => value + 1)}>
            <RefreshCw size={14} />Reconnect
          </button></>}
      </div>
    </div>}
  </div>;
}
