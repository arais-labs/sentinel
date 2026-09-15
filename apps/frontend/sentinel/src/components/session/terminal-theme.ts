export interface TerminalTheme {
  foreground: string;
  background: string;
  cursor: string;
  selection: string;
  palette: string[];
}

export function terminalTheme(): TerminalTheme {
  const styles = getComputedStyle(document.documentElement);
  const color = (name: string) => styles.getPropertyValue(name).trim();
  const dark = document.documentElement.classList.contains('dark');
  return {
    foreground: color('--text-primary'), background: color('--surface-0'),
    cursor: color('--sentinel-blue'), selection: color('--surface-3'),
    palette: dark ? [
      '#71717a', '#f87171', '#4ade80', '#fbbf24', '#38bdf8', '#c084fc', '#22d3ee', '#e4e4e7',
      '#a1a1aa', '#fca5a5', '#86efac', '#fde047', '#7dd3fc', '#d8b4fe', '#67e8f9', '#ffffff',
    ] : [
      '#3f3f46', '#b91c1c', '#15803d', '#92400e', '#0369a1', '#7e22ce', '#0e7490', '#334155',
      '#64748b', '#991b1b', '#166534', '#854d0e', '#075985', '#6b21a8', '#155e75', '#0f172a',
    ],
  };
}
