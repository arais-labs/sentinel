import { ExternalLink, Loader2, Monitor, MousePointer2, X } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

interface DesktopPreviewProps {
  url: string | null;
  isFullscreen: boolean;
  onClose: () => void;
  isBooting?: boolean;
  layoutKey?: string;
  onFrameLoad?: () => void;
  onInteract?: () => void;
}

interface PanelRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const TRANSITION_MS = 320;
const TRANSITION_SETTLE_MS = TRANSITION_MS + 80;

function getFullscreenRect(): PanelRect {
  return {
    top: 0,
    left: 0,
    width: window.innerWidth,
    height: window.innerHeight,
  };
}

function getAnchorRect(element: HTMLElement | null): PanelRect | null {
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  return {
    top: rect.top,
    left: rect.left,
    width: rect.width,
    height: rect.height,
  };
}

export const DesktopPreview = memo(({
  url,
  isFullscreen,
  onClose,
  isBooting = false,
  layoutKey,
  onFrameLoad,
  onInteract,
}: DesktopPreviewProps) => {
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [panelRect, setPanelRect] = useState<PanelRect | null>(null);
  const [isFullscreenPanelVisible, setIsFullscreenPanelVisible] = useState(false);
  const frameRef = useRef<number | null>(null);
  const transitionTimeoutRef = useRef<number | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [isPanelTransitionEnabled, setIsPanelTransitionEnabled] = useState(false);
  const [isControlActive, setIsControlActive] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const retryCountRef = useRef(0);

  const cleanUrl = useMemo(() => {
    if (!url) return null;
    const normalized = url
      .replace(/["']/g, '')
      .replace('localhost', '127.0.0.1')
      .trim();
    try {
      const parsed = new URL(normalized);
      parsed.searchParams.set('resize', 'scale');
      parsed.searchParams.set('autoconnect', '1');
      parsed.searchParams.set('view_only', '0');
      parsed.searchParams.set('reconnect', '1');
      parsed.searchParams.set('reconnect_delay', '1000');
      if (retryNonce > 0) {
        parsed.searchParams.set('sentinel_retry', String(retryNonce));
      }
      return parsed.toString();
    } catch {
      return normalized;
    }
  }, [url, retryNonce]);

  useEffect(() => {
    setIsControlActive(false);
    retryCountRef.current = 0;
    setRetryNonce(0);
  }, [url, layoutKey]);

  useEffect(() => {
    if (isFullscreen) {
      setIsControlActive(true);
    }
  }, [isFullscreen]);

  const activateControls = () => {
    setIsControlActive(true);
    onInteract?.();
  };

  const handleFrameLoad = () => {
    iframeRef.current?.blur();
    onFrameLoad?.();

    window.setTimeout(() => {
      const frame = iframeRef.current;
      if (!frame || retryCountRef.current >= 5) return;

      try {
        const doc = frame.contentDocument;
        const root = doc?.documentElement;
        const status = doc?.getElementById('noVNC_status');
        const hasConnectionFailure =
          root?.classList.contains('noVNC_disconnected') &&
          status?.classList.contains('noVNC_status_error') &&
          status?.classList.contains('noVNC_open');

        if (hasConnectionFailure) {
          retryCountRef.current += 1;
          setRetryNonce((value) => value + 1);
        }
      } catch {
        // If the iframe is not readable, leave noVNC's own retry behavior in charge.
      }
    }, 1500);
  };

  useLayoutEffect(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    if (transitionTimeoutRef.current !== null) {
      window.clearTimeout(transitionTimeoutRef.current);
      transitionTimeoutRef.current = null;
    }

    if (isFullscreen) {
      const startRect = getAnchorRect(panelRef.current) ?? getAnchorRect(anchorRef.current) ?? panelRect ?? getFullscreenRect();
      setIsFullscreenPanelVisible(true);
      setIsPanelTransitionEnabled(false);
      setPanelRect(startRect);
      frameRef.current = window.requestAnimationFrame(() => {
        setIsPanelTransitionEnabled(true);
        frameRef.current = window.requestAnimationFrame(() => {
          setPanelRect(getFullscreenRect());
        });
      });
    } else if (isFullscreenPanelVisible) {
      setIsPanelTransitionEnabled(true);
      setPanelRect(getFullscreenRect());
      frameRef.current = window.requestAnimationFrame(() => {
        setPanelRect(getAnchorRect(anchorRef.current) ?? getFullscreenRect());
        transitionTimeoutRef.current = window.setTimeout(() => {
          setIsFullscreenPanelVisible(false);
          setIsPanelTransitionEnabled(false);
          setPanelRect(null);
          transitionTimeoutRef.current = null;
        }, TRANSITION_SETTLE_MS);
      });
    }

    return () => {
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      if (transitionTimeoutRef.current !== null) {
        window.clearTimeout(transitionTimeoutRef.current);
        transitionTimeoutRef.current = null;
      }
    };
  }, [isFullscreen]);

  const openInNewTab = () => {
    window.open(cleanUrl ?? '', '_blank', 'noopener,noreferrer');
  };

  if (!cleanUrl) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-xl border border-[color:var(--border-strong)] bg-zinc-900 text-[color:var(--text-muted)]">
        {isBooting ? (
          <>
            <Loader2 size={32} strokeWidth={1.5} className="animate-spin text-sky-400" />
            <p className="text-[10px] font-bold uppercase tracking-widest text-sky-400">Starting Runtime</p>
            <p className="text-[9px] text-zinc-500">Provisioning desktop environment...</p>
          </>
        ) : (
          <>
            <Monitor size={32} strokeWidth={1} />
            <p className="text-[10px] font-bold uppercase tracking-widest">No Active Desktop</p>
          </>
        )}
      </div>
    );
  }

  const isFloatingPanel = isFullscreenPanelVisible && Boolean(panelRect);

  return (
    <>
      <div
        ref={anchorRef}
        className="absolute inset-0 pointer-events-none"
        aria-hidden
      />
      <div
        ref={panelRef}
        className={`${isFloatingPanel ? 'fixed z-[1000]' : 'absolute inset-0'} overflow-hidden bg-black flex flex-col`}
        onPointerDownCapture={onInteract}
        onMouseDown={isFloatingPanel ? (event) => event.stopPropagation() : undefined}
        style={
          isFloatingPanel && panelRect
            ? {
                top: `${panelRect.top}px`,
                left: `${panelRect.left}px`,
                width: `${panelRect.width}px`,
                height: `${panelRect.height}px`,
                borderRadius: '0px',
                borderWidth: '0px',
                borderStyle: 'solid',
                borderColor: 'transparent',
                boxShadow: 'none',
                transitionProperty: isPanelTransitionEnabled
                  ? 'top, left, width, height, border-radius, border-color, box-shadow'
                  : 'none',
                transitionDuration: isPanelTransitionEnabled ? `${TRANSITION_MS}ms` : '0ms',
                transitionTimingFunction: 'cubic-bezier(0.22, 1, 0.36, 1)',
                willChange: 'top, left, width, height, border-radius, box-shadow',
              }
            : undefined
        }
      >
        <iframe
          ref={iframeRef}
          src={cleanUrl}
          className={`h-full w-full border-none ${isControlActive ? '' : 'pointer-events-none'}`}
          title="Live Desktop View"
          allow="clipboard-read; clipboard-write"
          tabIndex={isControlActive ? 0 : -1}
          onLoad={handleFrameLoad}
        />
        {!isControlActive && !isFullscreen ? (
          <button
            type="button"
            onClick={activateControls}
            className="absolute inset-0 z-20 flex items-center justify-center bg-black/0 opacity-0 transition-opacity duration-200 hover:bg-black/20 hover:opacity-100 focus:opacity-100 focus:outline-none"
            title="Click to control desktop"
          >
            <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-black/70 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-white/80 shadow-xl backdrop-blur">
              <MousePointer2 size={13} />
              Click to control
            </span>
          </button>
        ) : null}
        {!isFloatingPanel ? (
          <button
            onClick={(event) => {
              event.stopPropagation();
              openInNewTab();
            }}
            className="absolute top-4 right-4 z-10 rounded-lg bg-black/50 p-2 text-white transition-colors hover:bg-black/70"
            title="Open in new tab"
            style={{
              opacity: 1,
              transform: 'translateY(0)',
              pointerEvents: 'auto',
              transition: 'opacity 180ms ease-out, transform 280ms cubic-bezier(0.22, 1, 0.36, 1)',
            }}
          >
            <ExternalLink size={16} />
          </button>
        ) : null}
        <div
          className="absolute right-4 top-4 z-10 flex items-center gap-2"
          style={{
            opacity: isFullscreen ? 1 : 0,
            transform: isFullscreen ? 'translateY(0)' : 'translateY(-8px)',
            pointerEvents: isFullscreen ? 'auto' : 'none',
            transition: 'opacity 220ms ease-out, transform 320ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        >
          <button
            onClick={openInNewTab}
            className="rounded-lg bg-black/50 p-2 text-white transition-colors hover:bg-black/70"
            title="Open in new tab"
          >
            <ExternalLink size={16} />
          </button>
          <button
            onClick={onClose}
            className="rounded-lg bg-black/50 p-2 text-white transition-colors hover:bg-black/70"
            title="Close fullscreen"
          >
            <X size={18} />
          </button>
        </div>
      </div>
      <div
        className={`fixed inset-0 ${isFullscreen ? 'z-[990]' : 'z-[40]'} bg-black/80 backdrop-blur-md transition-opacity duration-300 ease-out ${
          isFullscreen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
        onClick={isFullscreen ? onClose : undefined}
      />
    </>
  );
});

DesktopPreview.displayName = 'DesktopPreview';
