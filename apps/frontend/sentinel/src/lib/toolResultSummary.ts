import { parsePayloadJson, previewPayloadValue } from './toolPayloadPreview';

const sensitive = /token|secret|password|passphrase|api[_-]?key|authorization|cookie|credential|private[_-]?key/i;
const sensitiveText = /sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._-]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}/i;
const label = (key: string) => key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
const structuredText = (value: string) => /^\s*(?:[\[{]|```(?:json)?\s*[\[{])/.test(value);
const text = (value: string) => sensitiveText.test(value) ? '[redacted]'
  : structuredText(value) ? 'Structured result · Inspect for details' : previewPayloadValue(value).text;
const parseStructured = (value: string) => parsePayloadJson(value.trim().replace(/^```(?:json)?\s*([\s\S]*?)\s*```$/i, '$1'));

/** Parse the full field before its display preview can truncate JSON. */
export function summarizeToolField(value: string): string {
  const parsed = parseStructured(value);
  return structuredText(value) || (typeof parsed === 'string' && structuredText(parsed))
    ? summarizeToolResult(value) : text(value);
}

// Prefer human-facing identity fields; unknown schemas fall back to a couple of facts.
function itemPreview(value: unknown, depth = 0): string {
  if (value == null || depth > 3) return '';
  if (typeof value === 'string') {
    const parsed = parseStructured(value);
    return parsed !== null && typeof parsed === 'object'
      ? itemPreview(parsed, depth + 1) : previewPayloadValue(text(value), 48).text;
  }
  if (typeof value !== 'object') return String(value);
  if (Array.isArray(value)) return `${value.length} items`;
  const entries = Object.entries(value).filter(([key]) => !sensitive.test(key));
  for (const key of ['title', 'name', 'label', 'display_name', 'displayName', 'description', 'path', 'url']) {
    const candidate = entries.find(([field]) => field === key)?.[1];
    if (typeof candidate === 'string' && candidate.trim()) return itemPreview(candidate, depth + 1);
  }
  return entries.filter(([key, item]) => !['ok', 'success'].includes(key) && item != null && typeof item !== 'object')
    .slice(0, 2).map(([key, item]) => `${label(key)}: ${itemPreview(item, depth + 1)}`).join(', ');
}

function collectionPreview(items: unknown[], heading: string): string {
  const previews = items.slice(0, 3).map(item => itemPreview(item)).filter(Boolean);
  const remaining = items.length - previews.length;
  return text(`${heading}${previews.length ? ` — ${previews.join('; ')}${remaining > 0 ? `; +${remaining} more` : ''}` : ''}`);
}

/** Bounded, schema-based summaries shared by every tool, live or persisted. */
export function summarizeToolResult(raw: string, failed = false): string {
  if (!raw.trim()) return '';
  function summarize(value: unknown, depth: number): string {
    if (depth > 3 || value == null) return '';
    if (typeof value === 'string') {
      const nested = parseStructured(value);
      return nested !== null && (typeof nested === 'object' || typeof nested === 'string') ? summarize(nested, depth + 1) : text(value);
    }
    if (Array.isArray(value)) return collectionPreview(value, `${value.length} ${value.length === 1 ? 'item' : 'items'}`);
    if (typeof value !== 'object') return String(value);
    const object = value as Record<string, unknown>;
    const entries = Object.entries(object).filter(([key]) => !sensitive.test(key));
    for (const key of ['error', ...(failed ? ['stderr', 'detail', 'reason'] : []), 'summary', 'message', 'note']) {
      if (object[key]) {
        const description = summarize(object[key], depth + 1);
        if (description) return description;
      }
    }
    // Common result envelopes, including MCP text blocks. Never dump JSON into the card.
    if (Array.isArray(object.content)) {
      const blocks = object.content as Array<Record<string, unknown> | null>;
      const descriptions = blocks.filter(block => block?.type === 'text' && typeof block.text === 'string')
        .map(block => summarize(block!.text, depth + 1)).filter(Boolean);
      if (descriptions.length) return text(descriptions.join(' · '));
    }
    for (const key of ['result', 'data', 'output']) {
      if (object[key] != null) {
        const description = summarize(object[key], depth + 1);
        if (description) return description;
      }
    }
    const exitCode = object.exit_code ?? object.returncode;
    if (typeof exitCode === 'number' && exitCode !== 0) return `Process exited with code ${exitCode}`;
    if (typeof object.stdout === 'string' && object.stdout.trim()) return summarize(object.stdout, depth + 1);
    const facts = entries.flatMap(([key, value]) => {
      if (['ok', 'success', 'error', 'stdout', 'stderr', 'returncode', 'exit_code'].includes(key) || value == null) return [];
      if (Array.isArray(value)) return [collectionPreview(value, `${label(key)}: ${value.length}`)];
      if (typeof value === 'boolean') return [`${label(key)}: ${value ? 'yes' : 'no'}`];
      if (typeof value === 'number' || (typeof value === 'string' && value.trim())) return [`${label(key)}: ${summarize(value, depth + 1)}`];
      return [];
    });
    if (facts.length) return text(facts.slice(0, 3).join(' · '));
    if (typeof exitCode === 'number') return `Process exited with code ${exitCode}`;
    return '';
  }
  const parsed = parseStructured(raw);
  return summarize(parsed === null ? raw : parsed, 0);
}
