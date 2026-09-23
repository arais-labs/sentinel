export type WorkspaceDesktop = 'none' | 'xfce' | 'weston' | 'lxqt' | 'gnome' | 'plasma';

export function validateDesktop(value: string): asserts value is WorkspaceDesktop {
  if (!['none', 'xfce', 'weston', 'lxqt', 'gnome', 'plasma'].includes(value)) throw new Error('Unknown workspace desktop');
}
