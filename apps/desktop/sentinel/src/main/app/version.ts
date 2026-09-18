import type { ReleaseChannel, ShellUpdate } from '../../shared/ipc.js';

const GITHUB_REPO = 'arais-labs/sentinel';

export function parseVersion(value: string): number[] {
  const match = /^v?(\d+)\.(\d+)\.(\d+)/.exec(value.trim());
  if (!match) throw new Error(`Not a version: ${value}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

export function compareVersions(a: string, b: string): -1 | 0 | 1 {
  const left = parseVersion(a), right = parseVersion(b);
  for (let index = 0; index < 3; index++) {
    if (left[index] !== right[index]) return left[index] < right[index] ? -1 : 1;
  }
  return 0;
}

// Versioned release page, matching the tag scheme in scripts/publishing/publish-release.mjs.
export function releasePageUrl(channel: ReleaseChannel, version: string, commit: string): string {
  return `https://github.com/${GITHUB_REPO}/releases/tag/${channel}-${version}-${commit.slice(0, 7)}`;
}

// Null when the running shell can run the payload; otherwise where to get the newer shell.
export function shellUpdateFor(
  index: { channel: ReleaseChannel; version: string; commit: string; minShellVersion?: string },
  shellVersion: string,
): ShellUpdate | null {
  if (!index.minShellVersion || compareVersions(shellVersion, index.minShellVersion) >= 0) return null;
  return { version: index.version, minShellVersion: index.minShellVersion, url: releasePageUrl(index.channel, index.version, index.commit) };
}

export function assertShellCompatible(update: { version: string; shellUpdate: ShellUpdate | null }): void {
  if (update.shellUpdate) {
    throw new Error(`Sentinel ${update.version} needs app version ${update.shellUpdate.minShellVersion} or newer. Download the installer to update.`);
  }
}
