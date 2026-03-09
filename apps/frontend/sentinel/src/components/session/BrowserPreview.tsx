import { ExternalLink, Globe, X } from 'lucide-react';
import { memo, useMemo } from 'react';

interface BrowserPreviewProps {
  url: string | null;
  isFullscreen: boolean;
  onClose: () => void;
}

export const BrowserPreview = memo(({
  url,
  isFullscreen,
  onClose
}: BrowserPreviewProps) => {
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
          <Globe size={32} strokeWidth={1} />
          <p className="text-[10px] font-bold uppercase tracking-widest">No Active Browser</p>
        </div>
    );
  }

  return (
      <>
        <div
            className={`fixed inset-0 z-[90] bg-black/80 backdrop-blur-md transition-opacity duration-500 ${isFullscreen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
            onClick={onClose}
        />

        <div
            onMouseDown={(e) => e.stopPropagation()}
            className={`transition-all duration-500 ease-in-out bg-black shadow-2xl overflow-hidden ${
                isFullscreen
                    ? 'fixed inset-4 md:inset-12 z-[100] rounded-2xl border border-white/10'
                    : 'absolute inset-0 rounded-none'
            }`}
        >
          {isFullscreen && (
              <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 bg-zinc-900">
                <div className="flex items-center gap-3">
                  <Globe size={16} className="text-sky-400" />
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
          )}

          <div className="relative w-full h-full">
            <iframe
                src={cleanUrl}
                className="w-full h-full border-none"
                title="Live Browser View"
                allow="clipboard-read; clipboard-write"
            />
            {!isFullscreen ? (
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
            ) : null}
          </div>
        </div>
      </>
  );
});
BrowserPreview.displayName = 'BrowserPreview';
