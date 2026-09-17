export const instanceColors = ['#8ebaff', '#82d9bb', '#c1a5f5', '#efbd80', '#ef9daa', '#a7b4ca'];

export function instanceColor(id: string) {
  const hash = Array.from(id).reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 0);
  return instanceColors[hash % instanceColors.length];
}
