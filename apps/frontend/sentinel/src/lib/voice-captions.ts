/** Fit complete phrases to the actual rendered font; split oversized words safely. */
export function voiceCaptionLines(text: string, width: number, measure: (text: string) => number): string[] {
  const glyphs = Array.from(new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text.replace(/\s+/gu, ' ').trim()), item => item.segment);
  const lines: string[] = [];
  while (glyphs.length) {
    let low = 1, high = glyphs.length, count = 1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (measure(glyphs.slice(0, middle).join('')) <= width) { count = middle; low = middle + 1; }
      else high = middle - 1;
    }
    if (count < glyphs.length && glyphs[count] !== ' ') {
      const boundary = glyphs.slice(0, count).lastIndexOf(' ');
      if (boundary > 0) count = boundary;
    }
    lines.push(glyphs.splice(0, count).join('').trim());
    while (glyphs[0] === ' ') glyphs.shift();
  }
  return lines;
}

export const voiceCaptionDuration = (text: string) => Math.max(1100, Math.min(3600, text.split(/\s+/u).length * 320));

/** Prefer whole sentences; only split a sentence if it exceeds three visible lines. */
export function voiceCaptionSentences(text: string, width: number, measure: (text: string) => number): string[] {
  const sentences = new Intl.Segmenter('en', { granularity: 'sentence' }).segment(text.replace(/\s+/gu, ' ').trim());
  const captions: string[] = [];
  for (const { segment } of sentences) {
    const lines = voiceCaptionLines(segment, width, measure);
    for (let index = 0; index < lines.length; index += 3) captions.push(lines.slice(index, index + 3).join(' '));
  }
  return captions;
}
