import { mkdir, appendFile } from 'node:fs/promises';
import path from 'node:path';
import type { LogEntry } from '../shared/ipc.js';

function dateKey(iso: string): string {
  return iso.slice(0, 10);
}

export class DailyLogWriter {
  private queue = Promise.resolve();

  constructor(private readonly logRoot: string) {}

  write(entry: LogEntry): void {
    const payload = `${JSON.stringify(entry)}\n`;
    this.queue = this.queue
      .then(async () => {
        await this.append(path.join(this.logRoot, `desktop-${dateKey(entry.at)}.log`), payload);
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
