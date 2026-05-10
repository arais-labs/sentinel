import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

export function parseEnv(content: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }
    const idx = line.indexOf('=');
    if (idx < 0) {
      continue;
    }
    result[line.slice(0, idx)] = line.slice(idx + 1);
  }
  return result;
}

export function serializeEnv(values: Record<string, string>): string {
  return `${Object.entries(values)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`)
    .join('\n')}\n`;
}

export async function readEnvFile(file: string): Promise<Record<string, string>> {
  try {
    return parseEnv(await readFile(file, 'utf8'));
  } catch {
    return {};
  }
}

export async function writeEnvFile(file: string, values: Record<string, string>): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, serializeEnv(values), { mode: 0o600 });
}

export function randomSecret(length = 32): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < length; i += 1) {
    out += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return out;
}
