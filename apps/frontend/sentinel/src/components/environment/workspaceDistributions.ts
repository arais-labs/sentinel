import alpine from '../../assets/distro-logos/alpine.svg';
import ubuntu from '../../assets/distro-logos/ubuntu.svg';
import debian from '../../assets/distro-logos/debian.svg';
import windows from '../../assets/distro-logos/windows.svg';

/** Logos for known distributions; callers show a generic icon for other ids. */
export const distributionLogos: Partial<Record<string, string>> = { alpine, ubuntu, debian, 'windows-11': windows };
