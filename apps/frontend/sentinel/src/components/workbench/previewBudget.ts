/** Keep generated/minified files out of synchronous rich-rendering paths. */
export function exceedsPreviewBudget(text: string): boolean {
  if (text.length > 100_000) return true;
  let lineStart = 0;
  let lines = 1;
  for (let i = 0; i < text.length; i++) {
    if (i - lineStart > 2_000) return true;
    if (text.charCodeAt(i) === 10) {
      lineStart = i + 1;
      if (++lines > 2_000) return true;
    }
  }
  return text.length - lineStart > 2_000;
}
