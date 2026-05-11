// Helpers for deriving a stable, glanceable identity for each runtime
// terminal in the UI. The whole point: a terminal's `terminal_id` is now the
// source of truth for its visual label and accent color, so the same pill
// looks identical across page reloads and backend restarts (no DB needed).

export interface TerminalAccent {
  /** Solid accent color — used for cursor, focused pill border, panel title. */
  accent: string;
  /** Translucent bg tint matching the accent — pill chip background. */
  bg: string;
  /** Mid-opacity border — pill outline when not focused. */
  border: string;
  /** Faint focus ring — terminal panel highlight. */
  ring: string;
  /** Human-readable color name (for debugging / a11y labels). */
  name: string;
}

// Hand-picked palette of six visually-distinct hues with good contrast on a
// dark UI. Stays inside the existing sentinel palette aesthetic; bright
// enough to be unmistakable at pill-size, calm enough not to scream.
const ACCENT_PALETTE: TerminalAccent[] = [
  { name: 'cyan',    accent: '#38bdf8', bg: 'rgba(56,189,248,0.14)',  border: 'rgba(56,189,248,0.45)',  ring: 'rgba(56,189,248,0.30)' },
  { name: 'emerald', accent: '#34d399', bg: 'rgba(52,211,153,0.14)',  border: 'rgba(52,211,153,0.45)',  ring: 'rgba(52,211,153,0.30)' },
  { name: 'amber',   accent: '#fbbf24', bg: 'rgba(251,191,36,0.14)',  border: 'rgba(251,191,36,0.45)',  ring: 'rgba(251,191,36,0.30)' },
  { name: 'rose',    accent: '#fb7185', bg: 'rgba(251,113,133,0.14)', border: 'rgba(251,113,133,0.45)', ring: 'rgba(251,113,133,0.30)' },
  { name: 'violet',  accent: '#a78bfa', bg: 'rgba(167,139,250,0.14)', border: 'rgba(167,139,250,0.45)', ring: 'rgba(167,139,250,0.30)' },
  { name: 'orange',  accent: '#fb923c', bg: 'rgba(251,146,60,0.14)',  border: 'rgba(251,146,60,0.45)',  ring: 'rgba(251,146,60,0.30)' },
];

// FNV-1a-ish 32-bit string hash. Deterministic, no deps. We only need
// "same input → same color"; cryptographic strength is irrelevant.
function hashString(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = (h * 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/**
 * Pick the accent palette entry for a given terminal id. The default terminal
 * (`"0"`) always gets the first/canonical color (cyan) so it stays anchored
 * across sessions; all other ids hash into the rest of the palette.
 */
export function getTerminalAccent(terminalId: string): TerminalAccent {
  if (terminalId === '0') return ACCENT_PALETTE[0];
  const tail = ACCENT_PALETTE.slice(1);
  return tail[hashString(terminalId) % tail.length];
}

/**
 * Human-facing label for a terminal pill.
 *
 * - `"0"` is the default shell; render it as `main`.
 * - `auto-<hash>` ids come from the parallel-call pre-pass; the agent picked
 *   no meaningful name, so we fall back to a command summary (first non-empty
 *   line of whatever was last run there).
 * - Anything else is an agent-chosen name (`build`, `tests`, `dev-server`...)
 *   and we render it verbatim — that name *is* the label.
 */
export function getTerminalLabel(
  terminalId: string,
  lastCommand: string | null | undefined,
): string {
  if (terminalId === '0') return 'main';
  if (terminalId.startsWith('auto-')) {
    const summary = summarizeCommand(lastCommand);
    return summary || terminalId;
  }
  return terminalId;
}

/**
 * Trim a shell command into a one-line, 40-char-ish summary suitable for a pill.
 * Strips trailing newlines, collapses whitespace, and ellipsizes long inputs.
 */
export function summarizeCommand(command: string | null | undefined): string | null {
  if (!command) return null;
  const firstLine = command.split('\n')[0]?.trim() ?? '';
  if (!firstLine) return null;
  const collapsed = firstLine.replace(/\s+/g, ' ');
  if (collapsed.length <= 40) return collapsed;
  return `${collapsed.slice(0, 39)}…`;
}
