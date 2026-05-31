import React from 'react';

// `<diffs-container>` is registered transitively via PatchDiff →
// useFileDiffInstance → components/FileDiff.js → components/web-components.js.
// The package's `exports` field doesn't expose web-components.js, so no
// explicit side-effect import is possible (or needed).
import { PatchDiff } from '@pierre/diffs/react';

export type DiffViewMode = 'unified' | 'split';

interface DiffViewerProps {
  diff: string;
  viewMode?: DiffViewMode;
  className?: string;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  diff,
  viewMode = 'unified',
  className = '',
}) => {
  if (!diff || !diff.trim()) {
    return (
      <div className={`p-8 text-center text-[10px] uppercase tracking-widest text-[color:var(--text-muted)] ${className}`}>
        No diff content to display.
      </div>
    );
  }

  return (
    <div
      className={`pierre-diff-host h-full w-full ${className}`}
      style={
        {
          '--diffs-light-bg': '#ffffff',
          '--diffs-dark-bg': '#0b0d10',
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
      <PatchDiff
        patch={diff}
        options={{
          // Header is rendered by the Workbench toolbar.
          disableFileHeader: true,
          diffStyle: viewMode,
        }}
      />
    </div>
  );
};
