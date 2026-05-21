import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type {
  BootstrapProgress,
  ReleaseChannel,
  UpdateAvailable,
  UpdateProgress,
} from '../shared/ipc.js';
import { execFileText } from './shell.js';
import {
  backendSourceDir,
  bundledGitBinary,
  bundledNpmBinary,
  bundledUvBinary,
  frontendSourceDir,
  nodeHome,
  pythonHome,
  runtimeChannelMarkerPath,
  runtimeCommitMarkerPath,
  sourceRoot,
} from './desktopConfig.js';

export function channelToBranch(channel: ReleaseChannel): string {
  return CHANNEL_TO_BRANCH[channel];
}

const CHANNEL_TO_BRANCH: Record<ReleaseChannel, string> = {
  stable: 'main',
  beta: 'beta',
};

export type BootstrapListener = (progress: BootstrapProgress) => void;
export type UpdateListener = (progress: UpdateProgress) => void;

function gitEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    GIT_TERMINAL_PROMPT: '0',
    GIT_ASKPASS: 'echo',
  };
}

function buildToolchainEnv(): NodeJS.ProcessEnv {
  const node = nodeHome();
  const python = pythonHome();
  return {
    ...process.env,
    PATH: [
      path.join(node, 'bin'),
      path.join(python, 'bin'),
      process.env.PATH || '',
    ]
      .filter(Boolean)
      .join(':'),
    UV_PYTHON: path.join(python, 'bin/python3'),
    UV_NO_PROGRESS: '1',
  };
}

export async function readMarker(file: string): Promise<string | null> {
  try {
    const value = await readFile(file, 'utf8');
    return value.trim() || null;
  } catch {
    return null;
  }
}

export async function writeMarker(file: string, value: string): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${value.trim()}\n`);
}

export async function currentCommit(): Promise<string | null> {
  return readMarker(runtimeCommitMarkerPath());
}

export async function currentChannel(): Promise<ReleaseChannel | null> {
  const raw = await readMarker(runtimeChannelMarkerPath());
  if (raw === 'stable' || raw === 'beta') return raw;
  return null;
}

export function isBootstrapped(): boolean {
  return existsSync(path.join(sourceRoot(), '.git'));
}

export async function fetchChannel(channel: ReleaseChannel): Promise<void> {
  const branch = CHANNEL_TO_BRANCH[channel];
  // Plain refspec only: explicit refspec + --prune trips an Apple-git bug.
  await execFileText(
    bundledGitBinary(),
    ['fetch', 'origin', branch],
    { cwd: sourceRoot(), env: gitEnv() },
  );
}

export async function resolveRef(ref: string): Promise<string> {
  const sha = await execFileText(bundledGitBinary(), ['rev-parse', ref], {
    cwd: sourceRoot(),
    env: gitEnv(),
  });
  return sha.trim();
}

export async function commitSubject(sha: string): Promise<string> {
  const subject = await execFileText(
    bundledGitBinary(),
    ['log', '-1', '--format=%s', sha],
    { cwd: sourceRoot(), env: gitEnv() },
  );
  return subject.trim();
}

// Warns user before applying: forward-only migrations can't be rolled back.
export async function hasNewMigrations(prevSha: string, targetSha: string): Promise<boolean> {
  try {
    const out = await execFileText(
      bundledGitBinary(),
      ['diff', '--name-only', `${prevSha}..${targetSha}`, '--', 'apps/backend/sentinel/db/alembic/'],
      { cwd: sourceRoot(), env: gitEnv() },
    );
    return out.trim().length > 0;
  } catch {
    return true; // fail-safe: warn rather than silently skip
  }
}

export async function checkForUpdates(
  channel: ReleaseChannel,
): Promise<UpdateAvailable | null> {
  if (!isBootstrapped()) return null;
  await fetchChannel(channel);
  const branch = CHANNEL_TO_BRANCH[channel];
  const remoteTip = await resolveRef(`origin/${branch}`);
  const current = (await currentCommit()) || (await resolveRef('HEAD'));
  if (current === remoteTip) return null;
  return {
    channel,
    currentCommit: current,
    targetCommit: remoteTip,
    subject: await commitSubject(remoteTip),
    hasNewMigrations: await hasNewMigrations(current, remoteTip),
  };
}

export async function checkoutCommit(sha: string): Promise<void> {
  await execFileText(bundledGitBinary(), ['checkout', '--detach', sha], {
    cwd: sourceRoot(),
    env: gitEnv(),
  });
}

export async function syncPythonDeps(opts: { offline: boolean } = { offline: false }): Promise<void> {
  const args = ['sync', '--no-dev'];
  if (opts.offline) args.push('--offline');
  await execFileText(bundledUvBinary(), args, {
    cwd: backendSourceDir(),
    env: buildToolchainEnv(),
  });
}

export async function installNodeDeps(opts: { offline: boolean } = { offline: false }): Promise<void> {
  const args = ['ci'];
  if (opts.offline) args.push('--offline');
  await execFileText(bundledNpmBinary(), args, {
    cwd: frontendSourceDir(),
    env: buildToolchainEnv(),
  });
}

export async function buildFrontend(): Promise<void> {
  await execFileText(bundledNpmBinary(), ['run', 'build'], {
    cwd: frontendSourceDir(),
    env: buildToolchainEnv(),
  });
}

export async function stampVersion(channel: ReleaseChannel, commit: string): Promise<void> {
  await writeMarker(runtimeCommitMarkerPath(), commit);
  await writeMarker(runtimeChannelMarkerPath(), channel);
}
