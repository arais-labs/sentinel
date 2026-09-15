import { RetainedSessionContext } from '../../lib/workspace-context-values';
import { controlVncVisibility } from '../../lib/vnc-visibility';
import RFB from '@novnc/novnc';
import { Loader2, RefreshCw } from 'lucide-react';
import { useContext, useEffect, useRef, useState } from 'react';
import { DesktopSocket } from '../../lib/desktop-socket';
import { paceVncUpdates } from '../../lib/vnc-pacing';
import { forwardVncPointer } from '../../lib/vnc-input';
import { shareVncClipboard } from '../../lib/vnc-clipboard';

export function DesktopView({
  wsUrl,
  updateMode = 'paced',
  clipboardEnabled = false,
  onClipboardError,
  onDisconnect,
}: {
  wsUrl: string;
  updateMode?: 'native' | 'paced';
  clipboardEnabled?: boolean;
  onClipboardError?: (message: string) => void;
  onDisconnect: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<
    'connecting' | 'connected' | 'disconnected'
  >('connecting');
  const [retry, setRetry] = useState(0);
  const visible = useContext(RetainedSessionContext)?.visible ?? true;
  const visibility = useRef<ReturnType<typeof controlVncVisibility> | null>(null);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  useEffect(() => { visibility.current?.setVisible(visible); }, [visible]);
  const disconnected = useRef(onDisconnect);
  disconnected.current = onDisconnect;
  const clipboard = useRef<ReturnType<typeof shareVncClipboard> | null>(null);
  const clipboardOptions = useRef({ clipboardEnabled, onClipboardError });
  clipboardOptions.current = { clipboardEnabled, onClipboardError };
  useEffect(() => {
    clipboard.current?.setEnabled(clipboardEnabled && visible);
  }, [clipboardEnabled, visible]);
  useEffect(() => {
    if (!host.current) return;
    setState('connecting');
    let rfb: RFB;
    let stopPacing: (() => void) | undefined;
    let stopPointer: (() => void) | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const lost = () => {
      clearTimeout(timer);
      stopPacing?.();
      clipboard.current?.dispose();
      clipboard.current = null;
      setState('disconnected');
      disconnected.current();
    };
    try {
      rfb = new RFB(host.current, new DesktopSocket(wsUrl));
      if (updateMode === 'paced') stopPacing = paceVncUpdates(rfb, () => visibleRef.current);
      stopPointer = forwardVncPointer(rfb);
      visibility.current = controlVncVisibility(rfb);
      rfb.background = 'transparent';
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.viewOnly = false;
      rfb.focusOnClick = true;
    } catch {
      lost();
      return;
    }
    const connected = () => {
      clearTimeout(timer);
      clipboard.current = shareVncClipboard(rfb, host.current!, (message) => {
        clipboardOptions.current.onClipboardError?.(message);
      });
      clipboard.current.setEnabled(clipboardOptions.current.clipboardEnabled && visibleRef.current);
      visibility.current?.setVisible(visibleRef.current);
      setState('connected');
    };
    rfb.addEventListener('connect', connected);
    rfb.addEventListener('disconnect', lost);
    rfb.addEventListener('securityfailure', lost);
    timer = setTimeout(() => {
      rfb.disconnect();
      lost();
    }, 15000);
    return () => {
      clipboard.current?.dispose();
      clipboard.current = null;
      stopPointer?.();
      clearTimeout(timer);
      visibility.current?.dispose();
      visibility.current = null;
      stopPacing?.();
      rfb.removeEventListener('connect', connected);
      rfb.removeEventListener('disconnect', lost);
      rfb.removeEventListener('securityfailure', lost);
      rfb.disconnect();
    };
  }, [wsUrl, retry, updateMode]);
  return (
    <div className="relative h-full min-h-0 w-full bg-(--app-bg) dark:bg-[#0c0c0e]">
      <div
        ref={host}
        className="h-full w-full overflow-hidden [&>div]:h-full! [&>div]:w-full!"
      />
      {state !== 'connected' && (
        <div className="absolute inset-0 flex items-center justify-center bg-(--app-bg)/90">
          <div
            className="flex items-center gap-3 text-sm text-(--text-secondary)"
            role="status"
          >
            {state === 'connecting' ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Connecting to desktop…
              </>
            ) : (
              <>
                Connection lost
                <button
                  className="inline-flex items-center gap-2"
                  onClick={() => setRetry((value) => value + 1)}
                >
                  <RefreshCw size={14} />
                  Reconnect
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
