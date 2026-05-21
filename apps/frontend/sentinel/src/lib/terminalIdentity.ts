export function summarizeCommand(command: string | null | undefined): string {
  const trimmed = (command ?? '').replace(/\s+/g, ' ').trim();
  if (!trimmed) return 'idle shell';
  return trimmed.length > 64 ? `${trimmed.slice(0, 61)}...` : trimmed;
}

export function getTerminalLabel(
  terminalId: string | null | undefined,
  fallback: string | null | undefined,
): string {
  const id = (terminalId ?? '').trim();
  if (!id || id === '0') return 'main';
  const label = (fallback ?? '').trim();
  if (label && label !== terminalId) {
    return summarizeCommand(label);
  }
  return `Terminal ${id}`;
}
