import RFB from '@novnc/novnc';
import { Maximize2, MonitorOff, MousePointer2, RefreshCw, X } from 'lucide-react';
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

interface DesktopPreviewProps {
  url: string | null;
  wsUrl?: string | null;
  isFullscreen: boolean;
  isBooting?: boolean;
  layoutKey?: string;
  onClose: () => void;
  onFrameLoad?: () => void;
  onInteract?: () => void;
}

type ConnectionState = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error';

interface PanelRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const TRANSITION_MS = 320;
const TRANSITION_SETTLE_MS = TRANSITION_MS + 80;
const DESKTOP_ASPECT_RATIO = 16 / 10;

function getFullscreenRect(): PanelRect {
  let width = window.innerWidth;
  let height = width / DESKTOP_ASPECT_RATIO;
  if (height > window.innerHeight) {
    height = window.innerHeight;
    width = height * DESKTOP_ASPECT_RATIO;
  }
  return {
    top: (window.innerHeight - height) / 2,
    left: (window.innerWidth - width) / 2,
    width,
    height,
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

function normalizeWsUrl(value: string | null | undefined): string | null {
  const raw = (value || '').trim();
  if (!raw) return null;
  if (raw.startsWith('ws://') || raw.startsWith('wss://')) return raw;
  if (raw.startsWith('/')) {
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${scheme}//${window.location.host}${raw}`;
  }
  return raw;
}

export const DesktopPreview = memo(function DesktopPreview({
  url,
  wsUrl,
  isFullscreen,
  isBooting = false,
  layoutKey,
  onClose,
  onFrameLoad,
  onInteract,
}: DesktopPreviewProps) {
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rfbRef = useRef<RFB | null>(null);
  const frameRef = useRef<number | null>(null);
  const transitionTimeoutRef = useRef<number | null>(null);
  const dockedRectRef = useRef<PanelRect | null>(null);
  const [panelRect, setPanelRect] = useState<PanelRect | null>(null);
  const [isFullscreenPanelVisible, setIsFullscreenPanelVisible] = useState(false);
  const [isPanelTransitionEnabled, setIsPanelTransitionEnabled] = useState(false);
  const [isControlActive, setIsControlActive] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [retryNonce, setRetryNonce] = useState(0);
  const rfbUrl = useMemo(() => normalizeWsUrl(wsUrl ?? url), [wsUrl, url]);

  useEffect(() => {
    setIsControlActive(false);
    setRetryNonce(0);
  }, [rfbUrl, layoutKey]);

  useEffect(() => {
    if (isFullscreen) {
      setIsControlActive(true);
    }
  }, [isFullscreen]);

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
      const dockedRect = getAnchorRect(anchorRef.current) ?? getAnchorRect(panelRef.current);
      if (dockedRect) {
        dockedRectRef.current = dockedRect;
      }
      const startRect = dockedRect ?? panelRect ?? getFullscreenRect();
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
      const targetRect = dockedRectRef.current ?? getAnchorRect(anchorRef.current) ?? getFullscreenRect();
      setIsPanelTransitionEnabled(true);
      setPanelRect(getFullscreenRect());
      frameRef.current = window.requestAnimationFrame(() => {
        setPanelRect(targetRect);
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

  useEffect(() => {
    if (isFullscreen || isFullscreenPanelVisible) return undefined;
    const updateDockedRect = () => {
      const rect = getAnchorRect(anchorRef.current);
      if (rect) {
        dockedRectRef.current = rect;
      }
    };
    updateDockedRect();
    window.addEventListener('resize', updateDockedRect);
    window.addEventListener('scroll', updateDockedRect, true);
    return () => {
      window.removeEventListener('resize', updateDockedRect);
      window.removeEventListener('scroll', updateDockedRect, true);
    };
  }, [isFullscreen, isFullscreenPanelVisible]);

  useEffect(() => {
    if (!isFullscreen) return undefined;
    const handleResize = () => setPanelRect(getFullscreenRect());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [isFullscreen]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => {
      window.dispatchEvent(new Event('resize'));
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [rfbUrl, layoutKey]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !rfbUrl) {
      setConnectionState('idle');
      return;
    }

    setConnectionState('connecting');
    const rfb = new RFB(container, rfbUrl);
    rfbRef.current = rfb;
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.viewOnly = false;
    rfb.focusOnClick = true;

    const handleConnect = () => {
      setConnectionState('connected');
      onFrameLoad?.();
    };
    const handleDisconnect = (event: Event) => {
      const detail = (event as CustomEvent<{ clean?: boolean }>).detail;
      setConnectionState(detail?.clean ? 'disconnected' : 'error');
    };
    const handleSecurityFailure = () => {
      setConnectionState('error');
    };

    rfb.addEventListener('connect', handleConnect);
    rfb.addEventListener('disconnect', handleDisconnect);
    rfb.addEventListener('securityfailure', handleSecurityFailure);

    return () => {
      rfb.removeEventListener('connect', handleConnect);
      rfb.removeEventListener('disconnect', handleDisconnect);
      rfb.removeEventListener('securityfailure', handleSecurityFailure);
      try {
        rfb.disconnect();
      } catch {
        // noVNC throws if React tears down after the socket already failed.
      }
      if (rfbRef.current === rfb) {
        rfbRef.current = null;
      }
    };
  }, [rfbUrl, layoutKey, retryNonce, onFrameLoad]);

  const activateControls = () => {
    setIsControlActive(true);
    rfbRef.current?.focus();
    onInteract?.();
  };

  const content = !rfbUrl ? (
    <div key="unavailable" className="flex h-full w-full flex-col items-center justify-center gap-3 bg-black px-6 text-center text-white/60">
      <div className="rounded-xl border border-white/10 bg-white/5 p-3">
        {isBooting ? <Maximize2 size={22} className="animate-pulse" /> : <MonitorOff size={22} />}
      </div>
      <div className="text-[10px] font-bold uppercase tracking-widest">
        {isBooting ? 'Desktop starting' : 'Desktop unavailable'}
      </div>
      <p className="max-w-[260px] text-[11px] leading-relaxed text-white/40">
        This SSH runtime does not currently expose a session desktop view.
      </p>
    </div>
  ) : (
    <div
      key="rfb"
      className="relative h-full w-full bg-black"
      onPointerDown={() => {
        rfbRef.current?.focus();
        onInteract?.();
      }}
    >
      <div ref={containerRef} className="h-full w-full overflow-hidden" />
      {connectionState !== 'connected' ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-center p-2">
          <div className="rounded-md border border-white/10 bg-black/70 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-white/70">
            {connectionState === 'connecting' ? 'Connecting desktop' : 'Desktop disconnected'}
          </div>
        </div>
      ) : null}
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
      {connectionState === 'error' || connectionState === 'disconnected' ? (
        <button
          type="button"
          onClick={() => setRetryNonce((value) => value + 1)}
          className="absolute bottom-3 right-3 inline-flex items-center gap-1.5 rounded-md border border-white/15 bg-black/70 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-widest text-white/80 transition-colors hover:text-white"
        >
          <RefreshCw size={12} />
          Reconnect
        </button>
      ) : null}
    </div>
  );

  const isFloatingPanel = isFullscreenPanelVisible && Boolean(panelRect);

  return (
    <>
      <div ref={anchorRef} className="absolute inset-0 pointer-events-none" aria-hidden />
      <div
        ref={panelRef}
        className={`${isFloatingPanel ? 'fixed z-[20000]' : 'absolute inset-0'} overflow-hidden bg-black flex flex-col`}
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
        {content}
        <div
          className="absolute right-4 top-4 z-30 flex items-center gap-2"
          style={{
            opacity: isFullscreen ? 1 : 0,
            transform: isFullscreen ? 'translateY(0)' : 'translateY(-8px)',
            pointerEvents: isFullscreen ? 'auto' : 'none',
            transition: 'opacity 220ms ease-out, transform 320ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        >
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
        className={`fixed inset-0 ${isFullscreen ? 'z-[19990]' : 'z-[40]'} bg-black/80 backdrop-blur-md transition-opacity duration-300 ease-out ${
          isFullscreen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
        onClick={isFullscreen ? onClose : undefined}
      />
    </>
  );
});

DesktopPreview.displayName = 'DesktopPreview';
