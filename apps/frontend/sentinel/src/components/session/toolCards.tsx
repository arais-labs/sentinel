import { CheckCircle2 } from 'lucide-react';

import { parsePayloadJson } from '../../lib/toolPayloadPreview';
import { DiffViewer } from '../workbench/DiffViewer';

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export interface CustomToolCardContext {
  toolName: string;
  inputRaw: string;
  outputRaw: string;
  outputError: boolean;
  hasImages: boolean;
  renderImages: () => React.JSX.Element;
  renderGenericCompact: (options?: { hideInput?: boolean }) => React.JSX.Element;
  renderGenericOutput: (options?: { showRawJson?: boolean }) => React.JSX.Element;
  renderPortForwardCompact: () => React.JSX.Element;
  renderPortForwardExpanded: () => React.JSX.Element;
}

export interface CustomToolCard {
  id: string;
  matches: (context: CustomToolCardContext) => boolean;
  autoExpand?: (context: CustomToolCardContext) => boolean;
  hideGenericArguments?: (context: CustomToolCardContext) => boolean;
  renderCompact: (context: CustomToolCardContext) => React.JSX.Element;
  renderExpandedResult: (context: CustomToolCardContext) => React.JSX.Element;
}

function normalizeToolName(value: string): string {
  return value.trim().toLowerCase();
}

function normalizedPatchLines(value: string): string[] {
  if (value === '') return [];
  const normalized = value.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = normalized.split('\n');
  if (normalized.endsWith('\n')) lines.pop();
  return lines;
}

function buildSyntheticReplacePatch(path: string, oldStr: string, newStr: string): string {
  const safePath = path.replace(/\r?\n/g, ' ').trim() || 'file';
  const oldLines = normalizedPatchLines(oldStr);
  const newLines = normalizedPatchLines(newStr);
  const oldCount = oldLines.length;
  const newCount = newLines.length;
  return [
    `diff --git a/${safePath} b/${safePath}`,
    `--- a/${safePath}`,
    `+++ b/${safePath}`,
    `@@ -1,${oldCount} +1,${newCount} @@`,
    ...oldLines.map((line) => `-${line}`),
    ...newLines.map((line) => `+${line}`),
  ].join('\n');
}

const STR_REPLACE_SENSITIVE_PATTERN =
  /(BEGIN (OPENSSH|RSA|EC|DSA)? ?PRIVATE KEY|refresh_token|access_token|api[_-]?key|authorization|password|passphrase|secret|credential|xox[baprs]-|sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._-]{12,})/i;

function strReplaceDiffData(context: CustomToolCardContext): {
  path: string;
  message: string;
  patch: string;
} | null {
  if (context.outputError) return null;
  const input = parsePayloadJson(context.inputRaw);
  const output = parsePayloadJson(context.outputRaw);
  if (!isObjectRecord(input) || !isObjectRecord(output)) return null;
  if (output.ok === false) return null;
  const path = typeof input.path === 'string' ? input.path.trim() : '';
  const oldStr = typeof input.old_str === 'string' ? input.old_str : '';
  const newStr = typeof input.new_str === 'string' ? input.new_str : '';
  if (!path || !oldStr) return null;
  if (STR_REPLACE_SENSITIVE_PATTERN.test(oldStr) || STR_REPLACE_SENSITIVE_PATTERN.test(newStr)) {
    return null;
  }
  const resultPath = typeof output.path === 'string' && output.path.trim() ? output.path.trim() : path;
  const message = typeof output.message === 'string' && output.message.trim()
    ? output.message.trim()
    : 'File patched successfully';
  return {
    path: resultPath,
    message,
    patch: buildSyntheticReplacePatch(resultPath, oldStr, newStr),
  };
}

const CUSTOM_TOOL_CARDS: CustomToolCard[] = [
  {
    id: 'screenshot',
    matches: (context) => context.hasImages || normalizeToolName(context.toolName).includes('screenshot'),
    autoExpand: (context) => context.hasImages,
    hideGenericArguments: () => true,
    renderCompact: (context) => context.renderGenericCompact({ hideInput: true }),
    renderExpandedResult: (context) => context.hasImages ? context.renderImages() : context.renderGenericOutput({ showRawJson: false }),
  },
  {
    id: 'port_forward',
    matches: (context) => normalizeToolName(context.toolName) === 'port_forward',
    renderCompact: (context) => context.renderPortForwardCompact(),
    renderExpandedResult: (context) => context.renderPortForwardExpanded(),
  },
  {
    id: 'str_replace_editor',
    matches: (context) => normalizeToolName(context.toolName) === 'str_replace_editor' && strReplaceDiffData(context) !== null,
    renderCompact: (context) => {
      const data = strReplaceDiffData(context);
      if (!data) return context.renderGenericCompact();
      return (
        <div className="mt-3 space-y-2">
          <div className="flex items-center gap-2 overflow-hidden px-1">
            <CheckCircle2 size={10} className="text-emerald-500/70 shrink-0" />
            <span className="text-[8px] font-black uppercase tracking-widest text-emerald-500/60">patched</span>
            <span className="min-w-0 truncate text-[10px] font-mono text-(--text-primary) max-w-[520px]">
              {data.path}
            </span>
          </div>
          <div className="h-[190px] min-w-[560px] max-w-full overflow-hidden rounded-xl border border-(--border-subtle) bg-(--surface-0)">
            <DiffViewer diff={data.patch} />
          </div>
        </div>
      );
    },
    renderExpandedResult: (context) => {
      const data = strReplaceDiffData(context);
      if (!data) return context.renderGenericOutput();
      return (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-[8px] font-black uppercase tracking-widest text-emerald-400">
              patched
            </span>
            <span className="min-w-0 truncate text-[10px] font-mono text-(--text-primary)">
              {data.path}
            </span>
            <span className="text-[10px] text-(--text-muted)">{data.message}</span>
          </div>
          <div className="h-[360px] overflow-hidden rounded-xl border border-(--border-subtle) bg-(--surface-0)">
            <DiffViewer diff={data.patch} />
          </div>
        </div>
      );
    },
  },
];

export function findCustomToolCard(context: CustomToolCardContext): CustomToolCard | null {
  return CUSTOM_TOOL_CARDS.find((card) => card.matches(context)) ?? null;
}
