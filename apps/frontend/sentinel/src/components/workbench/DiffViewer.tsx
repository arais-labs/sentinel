import { useThemeStore } from '../../store/theme-store';
import { exceedsPreviewBudget } from './previewBudget';
import React, { useEffect, useMemo, useState } from 'react';
import { DEFAULT_THEMES, getFiletypeFromFileName, getSingularPatch, preloadHighlighter } from '@pierre/diffs';

// `<diffs-container>` is registered transitively via PatchDiff →
// useFileDiffInstance → components/FileDiff.js → components/web-components.js.
// The package's `exports` field doesn't expose web-components.js, so no
// explicit side-effect import is possible (or needed).
import { PatchDiff } from '@pierre/diffs/react';
import type { DiffLineAnnotation, OnDiffLineClickProps } from '@pierre/diffs';

export type DiffViewMode = 'unified' | 'split';

interface DiffViewerProps {
  diff: string;
  viewMode?: DiffViewMode;
  className?: string;
  onLineClick?: (line: OnDiffLineClickProps) => void;
  annotations?: DiffLineAnnotation<React.ReactNode>[];
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  diff,
  viewMode = 'unified',
  className = '',
  onLineClick,
  annotations,
}) => {
  const theme = useThemeStore(state => state.theme);
  const plainPreview = useMemo(() => exceedsPreviewBudget(diff), [diff]);
  const language = useMemo(() => {
    if (plainPreview) return null;
    if (!diff.trim()) return null;
    try {
      const file = getSingularPatch(diff);
      return file.lang ?? getFiletypeFromFileName(file.name);
    } catch { return null; }
  }, [diff, plainPreview]);
  const [readyLanguage, setReadyLanguage] = useState<string | null>(null);
  useEffect(() => {
    if (language === null) return;
    let cancelled = false;
    // A cold PatchDiff can leave an empty container while loading its themes
    // and grammar. Mount it only when it can render synchronously; until then
    // the patch remains readable below. Shared assets are cached by the library.
    void preloadHighlighter({ themes: Object.values(DEFAULT_THEMES), langs: [language] })
      .then(() => { if (!cancelled) setReadyLanguage(language); })
      .catch(() => { /* Keep the readable patch if highlighting cannot load. */ });
    return () => { cancelled = true; };
  }, [language]);

  if (!diff || !diff.trim()) {
    return (
      <div className={`p-8 text-center text-[10px] uppercase tracking-widest text-(--text-muted) ${className}`}>
        No diff content to display.
      </div>
    );
  }

  return (
    <div
      className={`pierre-diff-host h-full w-full ${className}`}
      style={
        {
          '--diffs-light-bg': 'var(--app-bg)',
          '--diffs-dark-bg': 'var(--app-bg)',
          '--diffs-light': '#1f2937',
          '--diffs-dark': '#e5e7eb',
          '--diffs-added-light': '#10b981',
          '--diffs-added-dark': '#34d399',
          '--diffs-deleted-light': '#f43f5e',
          '--diffs-deleted-dark': '#fb7185',
          '--diffs-modified-light': '#0ea5e9',
          '--diffs-modified-dark': '#38bdf8',
          '--diffs-font-family':
            'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
          '--diffs-font-size': '12px',
          '--diffs-line-height': '18px',
        } as React.CSSProperties
      }
    >
      {plainPreview && <p className="px-3 py-2 text-xs text-(--text-muted)">Large diff · showing line changes without syntax highlighting.</p>}
      {language !== null && readyLanguage === language ? <PatchDiff
        patch={diff}
        lineAnnotations={annotations}
        renderAnnotation={annotation => annotation.metadata}
        options={{
          themeType: theme,
          // Syntax themes render inside a shadow root with their own surfaces.
          // Keep those surfaces aligned with the surrounding Sentinel pane.
          unsafeCSS: `
            :host, [data-diff] {
              --diffs-bg: var(--app-bg);
              --diffs-bg-buffer-override: var(--app-bg);
              --diffs-bg-context-override: var(--surface-1);
              --diffs-bg-context-gutter-override: var(--app-bg);
              --diffs-bg-separator-override: var(--surface-1);
              --diffs-fg-number-override: var(--text-muted);
            }
          `,
          onLineNumberClick: onLineClick,
          lineHoverHighlight: onLineClick ? 'number' : 'disabled',
          // Header is rendered by the Workbench toolbar.
          disableFileHeader: true,
          diffStyle: viewMode,
          maxLineDiffLength: 2_000,
          tokenizeMaxLineLength: 2_000,
          tokenizeMaxLength: 100_000,
        }}
      /> : <pre className="diff-readable-preview" aria-label="Diff">
        {diff.split('\n').map((line, index) => <span key={index} data-change={line.startsWith('+') && !line.startsWith('+++') ? 'added' : line.startsWith('-') && !line.startsWith('---') ? 'removed' : undefined}>{line || ' '}</span>)}
      </pre>}
    </div>
  );
};
