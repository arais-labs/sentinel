import { execFile } from 'node:child_process';
import { access } from 'node:fs/promises';
import { constants } from 'node:fs';

const DEFAULT_COMMAND_PATHS = [
  '/usr/bin',
  '/bin',
  '/usr/sbin',
  '/sbin',
];

export function execFileText(
  file: string,
  args: string[] = [],
  options: { cwd?: string; env?: NodeJS.ProcessEnv; input?: string } = {},
): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd: options.cwd, env: options.env }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`${file} ${args.join(' ')} failed: ${stderr || error.message}`));
        return;
      }
      resolve(stdout.toString());
    }).stdin?.end(options.input);
  });
}

export function commandSearchPath(pathValue = process.env.PATH || ''): string {
  return [...new Set([...pathValue.split(':').filter(Boolean), ...DEFAULT_COMMAND_PATHS])].join(':');
}

export async function commandExists(command: string): Promise<string | undefined> {
  for (const dir of commandSearchPath().split(':')) {
    const candidate = `${dir}/${command}`;
    try {
      await access(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Continue searching PATH.
    }
  }
  return undefined;
}
