import type { WorkspaceDesktop } from '../../types/api';
import xfce from '../../assets/desktop-previews/xfce.png';
import lxqt from '../../assets/desktop-previews/lxqt.png';
import gnome from '../../assets/desktop-previews/gnome.png';
import plasma from '../../assets/desktop-previews/plasma.png';

type DesktopDescription = {
  name: string;
  summary: string;
  detail: string;
  capture?: { src: string; distribution: string };
};

export const workspaceDesktops: Record<WorkspaceDesktop, DesktopDescription> = {
  none: {
    name: 'None', summary: 'Terminal, files, and background services.',
    detail: 'Use Sentinel’s terminal and files without a graphical Linux session.',
  },
  xfce: {
    name: 'XFCE · X11', summary: 'A familiar, full Linux desktop.',
    detail: 'Application menu, top taskbar, file manager and window controls.',
    capture: { src: xfce, distribution: 'Alpine' },
  },
  lxqt: {
    name: 'LXQt · Wayland', summary: 'A full Linux desktop with the labwc compositor.',
    detail: 'Application menu, running-app taskbar, file manager and desktop settings.',
    capture: { src: lxqt, distribution: 'Alpine' },
  },
  gnome: {
    name: 'GNOME · Wayland', summary: 'An integrated desktop organized around activities and workspaces.',
    detail: 'Activities overview, application grid, workspaces and integrated desktop settings.',
    capture: { src: gnome, distribution: 'Ubuntu 26.04' },
  },
  plasma: {
    name: 'KDE Plasma · Wayland', summary: 'A full desktop with a customizable panel and application launcher.',
    detail: 'Application launcher, taskbar, file manager and customizable desktop panels.',
    capture: { src: plasma, distribution: 'Ubuntu 26.04' },
  },
  weston: {
    name: 'Weston · Wayland', summary: 'A minimal compositor for desktop diagnostics.',
    detail: 'Minimal launcher panel and windows; no full running-app taskbar.',
  },
};

// Choices have real native-session captures; known upstream limitations are
// documented with the runtime rather than hidden behind alternate launch paths.
// Weston is an internal diagnostic profile, not a workspace desktop choice.
export const workspaceDesktopChoices = ['none', 'xfce', 'lxqt', 'gnome', 'plasma'] as const satisfies readonly WorkspaceDesktop[];
