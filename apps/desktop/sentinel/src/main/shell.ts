import { execFile } from 'node:child_process';
import { access } from 'node:fs/promises';
import { constants } from 'node:fs';

export function execFileText(file: string, args: string[] = [], options: { cwd?: string; env?: NodeJS.ProcessEnv } = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(file, args, { cwd: options.cwd, env: options.env }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`${file} ${args.join(' ')} failed: ${stderr || error.message}`));
        return;
      }
      resolve(stdout.toString());
    });
  });
}

export async function commandExists(command: string): Promise<string | undefined> {
  const pathValue = process.env.PATH || '';
  for (const dir of pathValue.split(':')) {
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
