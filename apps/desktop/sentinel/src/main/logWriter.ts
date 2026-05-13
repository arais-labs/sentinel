import { mkdir, appendFile } from 'node:fs/promises';
import path from 'node:path';
import type { LogEntry } from '../shared/ipc.js';

function dateKey(iso: string): string {
  return iso.slice(0, 10);
}

function sanitizeFileSegment(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9._-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '') || 'unknown';
}

export class DailyLogWriter {
  private queue = Promise.resolve();

  constructor(private readonly logRoot: string) {}

  write(entry: LogEntry, instance?: string): void {
    const payload = `${JSON.stringify({ ...entry, instance: instance || undefined })}\n`;
    this.queue = this.queue
      .then(async () => {
        await this.append(path.join(this.logRoot, `desktop-${dateKey(entry.at)}.log`), payload);
        if (instance) {
          await this.append(
            path.join(this.logRoot, 'instances', `${sanitizeFileSegment(instance)}-${dateKey(entry.at)}.log`),
            payload,
          );
        }
      })
      .catch((error) => {
        console.error(`Failed to persist Sentinel desktop log: ${String(error?.message || error)}`);
      });
  }

  async flush(): Promise<void> {
    await this.queue;
  }

  private async append(filePath: string, payload: string): Promise<void> {
    await mkdir(path.dirname(filePath), { recursive: true });
    await appendFile(filePath, payload, 'utf8');
  }
}
