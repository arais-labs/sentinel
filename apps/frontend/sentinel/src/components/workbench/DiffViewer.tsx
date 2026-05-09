import React, { useMemo } from 'react';
import { Minus, Plus } from 'lucide-react';

interface DiffViewerProps {
  diff: string;
  className?: string;
}

interface DiffLine {
  type: 'addition' | 'deletion' | 'context' | 'header' | 'hunk';
  content: string;
  leftLineNumber?: number;
  rightLineNumber?: number;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({ diff, className = '' }) => {
  const lines = useMemo(() => {
    const allLines = diff.split('\n');
    const processedLines: DiffLine[] = [];
    let leftLine = 0;
    let rightLine = 0;

    allLines.forEach((line) => {
      if (line.startsWith('---') || line.startsWith('+++')) {
        processedLines.push({ type: 'header', content: line });
      } else if (line.startsWith('@@')) {
        processedLines.push({ type: 'hunk', content: line });
        // Parse hunk header: @@ -1,4 +1,5 @@
        const match = line.match(/@@ -(\d+),?\d* \+(\d+),?\d* @@/);
        if (match) {
          leftLine = parseInt(match[1], 10) - 1;
          rightLine = parseInt(match[2], 10) - 1;
        }
      } else if (line.startsWith('+')) {
        rightLine++;
        processedLines.push({
          type: 'addition',
          content: line.slice(1),
          rightLineNumber: rightLine,
        });
      } else if (line.startsWith('-')) {
        leftLine++;
        processedLines.push({
          type: 'deletion',
          content: line.slice(1),
          leftLineNumber: leftLine,
        });
      } else {
        leftLine++;
        rightLine++;
        processedLines.push({
          type: 'context',
          content: line.startsWith(' ') ? line.slice(1) : line,
          leftLineNumber: leftLine,
          rightLineNumber: rightLine,
        });
      }
    });

    return processedLines;
  }, [diff]);

  return (
    <div className={`font-mono text-[12px] leading-relaxed overflow-auto bg-[color:var(--surface-0)] ${className}`}>
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => {
            let bgColor = '';
            let textColor = 'text-[color:var(--text-secondary)]';
            let marker = null;

            if (line.type === 'addition') {
              bgColor = 'bg-emerald-500/10 dark:bg-emerald-500/15';
              textColor = 'text-emerald-600 dark:text-emerald-400';
              marker = <Plus size={10} className="mt-1" />;
            } else if (line.type === 'deletion') {
              bgColor = 'bg-rose-500/10 dark:bg-rose-500/15';
              textColor = 'text-rose-600 dark:text-rose-400';
              marker = <Minus size={10} className="mt-1" />;
            } else if (line.type === 'hunk') {
              bgColor = 'bg-sky-500/5 dark:bg-sky-500/10';
              textColor = 'text-sky-500/70';
            } else if (line.type === 'header') {
              bgColor = 'bg-[color:var(--surface-2)]';
              textColor = 'text-[color:var(--text-muted)] font-bold';
            }

            return (
              <tr key={i} className={`${bgColor} transition-colors hover:bg-white/5`}>
                <td className="w-10 select-none border-r border-[color:var(--border-subtle)] px-2 text-right text-[10px] text-[color:var(--text-muted)] opacity-50">
                  {line.leftLineNumber || ''}
                </td>
                <td className="w-10 select-none border-r border-[color:var(--border-subtle)] px-2 text-right text-[10px] text-[color:var(--text-muted)] opacity-50">
                  {line.rightLineNumber || ''}
                </td>
                <td className="w-4 px-1 text-center opacity-50">
                  {marker}
                </td>
                <td className={`whitespace-pre px-2 py-0.5 ${textColor}`}>
                  {line.content}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {lines.length === 0 && (
        <div className="p-8 text-center text-[color:var(--text-muted)] italic">
          No diff content to display.
        </div>
      )}
    </div>
  );
};
