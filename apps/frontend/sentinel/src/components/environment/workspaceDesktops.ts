import xfce from '../../assets/desktop-previews/xfce.png';
import lxqt from '../../assets/desktop-previews/lxqt.png';
import gnome from '../../assets/desktop-previews/gnome.png';
import plasma from '../../assets/desktop-previews/plasma.png';
import xfceLogo from '../../assets/desktop-logos/xfce.svg';
import lxqtLogo from '../../assets/desktop-logos/lxqt.svg';
import gnomeLogo from '../../assets/desktop-logos/gnome.svg';
import plasmaLogo from '../../assets/desktop-logos/kde.svg';

type DesktopDescription = {
  logo?: string;
  summary: string;
  detail: string;
  capture?: { src: string; distribution: string };
};

const descriptions: Partial<Record<string, DesktopDescription>> = {
  none: {
    summary: 'Terminal, files, and background services.',
    detail: 'Use Sentinel’s terminal and files without a graphical desktop session.',
  },
  xfce: {
    logo: xfceLogo, summary: 'A familiar, full Linux desktop.',
    detail: 'Application menu, top taskbar, file manager and window controls.',
    capture: { src: xfce, distribution: 'Alpine' },
  },
  lxqt: {
    logo: lxqtLogo, summary: 'A full Linux desktop with the labwc compositor.',
    detail: 'Application menu, running-app taskbar, file manager and desktop settings.',
    capture: { src: lxqt, distribution: 'Alpine' },
  },
  gnome: {
    logo: gnomeLogo, summary: 'An integrated desktop organized around activities and workspaces.',
    detail: 'Activities overview, application grid, workspaces and integrated desktop settings.',
    capture: { src: gnome, distribution: 'Ubuntu 26.04' },
  },
  plasma: {
    logo: plasmaLogo, summary: 'A full desktop with a customizable panel and application launcher.',
    detail: 'Application launcher, taskbar, file manager and customizable desktop panels.',
    capture: { src: plasma, distribution: 'Ubuntu 26.04' },
  },
};

const generic: DesktopDescription = {
  summary: 'A graphical desktop session.',
  detail: 'Graphical apps in the workspace, streamed to Sentinel’s Desktop tab.',
};

export const desktopDescription = (id: string) => descriptions[id] ?? generic;
