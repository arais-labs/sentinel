import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import path from 'node:path';

export interface KernelManifest { kernelFileSha256: string; kernelRelease: string }

/** Every workspace boots the same verified, bundled kernel as its GPU modules. */
export class WorkspaceKernel {
  readonly file: string;
  private job?: Promise<void>;
  private controller?: AbortController;
  private verified = false;

  constructor(resources: string, private readonly manifest: KernelManifest, private readonly log: (message: string) => void) {
    if (!/^[a-f0-9]{64}$/.test(manifest.kernelFileSha256) || !manifest.kernelRelease) {
      throw new Error('Invalid workspace kernel manifest');
    }
    this.file = path.join(resources, 'kernel');
  }

  ensure(): Promise<void> {
    if (this.verified) return Promise.resolve();
    if (this.job) return this.job;
    const controller = this.controller = new AbortController();
    const job = this.verify(controller.signal);
    this.job = job;
    void job.finally(() => {
      if (this.job === job) { this.job = undefined; this.controller = undefined; }
    }).catch(() => {});
    return job;
  }

  async cancel(): Promise<void> {
    this.controller?.abort();
    await this.job?.catch(() => {});
  }

  private async verify(signal: AbortSignal): Promise<void> {
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(this.file, { signal })) hash.update(chunk);
    signal.throwIfAborted();
    if (hash.digest('hex') !== this.manifest.kernelFileSha256) {
      throw new Error('Bundled workspace kernel checksum mismatch. Reinstall Sentinel.');
    }
    this.verified = true;
    this.log('Workspace kernel verified');
  }
}
