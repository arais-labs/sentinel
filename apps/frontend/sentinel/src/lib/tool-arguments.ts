/** Streamed tool arguments arrive as JSON fragments; these helpers rebuild them safely. */

export function serializeToolArguments(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function hasMeaningfulToolArguments(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  if (trimmed === '{}' || trimmed === 'null') return false;
  return true;
}

export function mergeStreamingToolArguments(current: string, delta: string): string {
  if (!delta) return current;
  const trimmedCurrent = current.trim();
  const trimmedDelta = delta.trim();

  if (!hasMeaningfulToolArguments(current) || trimmedCurrent === '{}') {
    return delta;
  }

  const currentLooksCompleteJson =
    (trimmedCurrent.startsWith('{') && trimmedCurrent.endsWith('}')) ||
    (trimmedCurrent.startsWith('[') && trimmedCurrent.endsWith(']'));
  const deltaLooksLikeFreshJson =
    trimmedDelta.startsWith('{') || trimmedDelta.startsWith('[');

  if (currentLooksCompleteJson && deltaLooksLikeFreshJson) {
    return delta;
  }

  return `${current}${delta}`;
}
