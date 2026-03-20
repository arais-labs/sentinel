import { ExternalLink, Monitor, Loader2, X } from 'lucide-react';
import { memo, useMemo } from 'react';

interface DesktopPreviewProps {
  url: string | null;
  isFullscreen: boolean;
  onClose: () => void;
  isBooting?: boolean;
}

export const DesktopPreview = memo(({
  url,
  isFullscreen,
  onClose,
  isBooting = false,
}: DesktopPreviewProps) => {
  // Sanitize URL to prevent accidental character injection and FORCE 127.0.0.1
  const cleanUrl = useMemo(() => {
    if (!url) return null;
    const normalized = url
        .replace(/["']/g, '')
        .replace('localhost', '127.0.0.1')
        .trim();
    try {
      const parsed = new URL(normalized);
      // Force fit-to-container behavior in embedded noVNC.
      parsed.searchParams.set('resize', 'scale');
      parsed.searchParams.set('autoconnect', '1');
      parsed.searchParams.set('view_only', '0');
      return parsed.toString();
    } catch {
      return normalized;
    }
  }, [url]);

  const openInNewTab = () => {
    window.open(cleanUrl ?? '', '_blank', 'noopener,noreferrer');
  };

  if (!cleanUrl) {
    return (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-[color:var(--text-muted)] gap-3 bg-zinc-900 rounded-xl border border-[color:var(--border-strong)]">
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

  // Inline (non-fullscreen) view
  if (!isFullscreen) {
    return (
      <div className="absolute inset-0 bg-black overflow-hidden">
        <iframe
          src={cleanUrl}
          className="w-full h-full border-none"
          title="Live Desktop View"
          allow="clipboard-read; clipboard-write"
        />
        <button
          onClick={(event) => {
            event.stopPropagation();
            openInNewTab();
          }}
          className="absolute top-4 right-4 p-2 bg-black/50 hover:bg-black/70 rounded-lg text-white transition-colors"
          title="Open in new tab"
        >
          <ExternalLink size={16} />
        </button>
      </div>
    );
  }

  // Fullscreen overlay
  return (
    <>
      <div
        className="fixed inset-0 z-[90] bg-black/80 backdrop-blur-md"
        onClick={onClose}
      />

      <div
        onMouseDown={(e) => e.stopPropagation()}
        className="fixed inset-4 md:inset-12 z-[100] rounded-2xl border border-white/10 bg-black shadow-2xl overflow-hidden flex flex-col"
      >
        {/* Header — fixed height */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 bg-zinc-900 shrink-0">
          <div className="flex items-center gap-3">
            <Monitor size={16} className="text-sky-400" />
            <span className="text-sm font-mono text-zinc-300 truncate max-w-md">{url}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={openInNewTab}
              className="p-2 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
              title="Open in new tab"
            >
              <ExternalLink size={16} />
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {/* Iframe — fills remaining space below header */}
        <div className="flex-1 min-h-0">
          <iframe
            src={cleanUrl}
            className="w-full h-full border-none"
            title="Live Desktop View"
            allow="clipboard-read; clipboard-write"
          />
        </div>
      </div>
    </>
  );
});
DesktopPreview.displayName = 'DesktopPreview';
