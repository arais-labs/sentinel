import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, rename, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { pipeline } from 'node:stream/promises';
import { createZstdDecompress } from 'node:zlib';
import path from 'node:path';

export interface KernelManifest {
  kernelUrl: string;
  kernelSha256: string;
  kernelFileSha256: string;
  kernelArchivePath: string;
}

async function digest(file: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

/** One verified kernel shared by all workspaces; interrupted downloads are disposable. */
export class WorkspaceKernel {
  readonly file: string;
  private job?: Promise<void>;
  private controller?: AbortController;
  private verified = false;
  private readonly directory: string;

  constructor(root: string, private readonly manifest: KernelManifest, private readonly log: (message: string) => void) {
    if (!/^[a-f0-9]{64}$/.test(manifest.kernelSha256) || !/^[a-f0-9]{64}$/.test(manifest.kernelFileSha256)
      || new URL(manifest.kernelUrl).protocol !== 'https:'
      || !manifest.kernelArchivePath || manifest.kernelArchivePath.startsWith('/')
      || manifest.kernelArchivePath.split('/').includes('..')) throw new Error('Invalid workspace kernel manifest');
    this.directory = path.join(root, 'kernels', manifest.kernelFileSha256);
    this.file = path.join(this.directory, 'kernel');
  }

  ensure(): Promise<void> {
    if (this.verified) return Promise.resolve();
    if (this.job) return this.job;
    const controller = new AbortController();
    this.controller = controller;
    const job = this.prepare(controller.signal);
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

  private async prepare(cancel: AbortSignal): Promise<void> {
    const signal = AbortSignal.any([cancel, AbortSignal.timeout(30 * 60_000)]);
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    try {
      if (await digest(this.file) === this.manifest.kernelFileSha256) {
        this.verified = true;
        return;
      }
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    const archive = path.join(this.directory, 'download.partial');
    const extracted = path.join(this.directory, 'kernel.partial');
    try {
      this.log('Downloading workspace kernel in the background…');
      const response = await fetch(this.manifest.kernelUrl, { signal });
      if (!response.ok || !response.body) throw new Error(`Kernel download failed (${response.status})`);
      await pipeline(response.body, createWriteStream(archive, { mode: 0o600 }), { signal });
      if (await digest(archive) !== this.manifest.kernelSha256) throw new Error('Workspace kernel archive checksum does not match');
      signal.throwIfAborted();
      // Electron supplies Zstandard decompression. macOS tar otherwise tries to
      // launch an external zstd executable, which is absent on clean machines.
      // Extract just the pinned member to stdout; archive paths never select a destination.
      const tar = spawn('/usr/bin/tar', ['-xOf', '-', this.manifest.kernelArchivePath], { signal, stdio: ['pipe', 'pipe', 'pipe'] });
      let errorText = '';
      tar.stderr.on('data', chunk => { errorText = (errorText + chunk).slice(-4096); });
      const finished = new Promise<void>((resolve, reject) => {
        tar.once('error', reject);
        tar.once('close', code => code === 0 ? resolve() : reject(new Error(`Kernel extraction failed: ${errorText || code}`)));
      });
      const results = await Promise.allSettled([
        pipeline(createReadStream(archive), createZstdDecompress(), tar.stdin, { signal }).catch(error => { tar.kill(); throw error; }),
        finished,
        pipeline(tar.stdout, createWriteStream(extracted, { mode: 0o600 }), { signal }).catch(error => { tar.kill(); throw error; }),
      ]);
      for (const result of results) if (result.status === 'rejected') throw result.reason;
      if (await digest(extracted) !== this.manifest.kernelFileSha256) throw new Error('Workspace kernel checksum does not match');
      signal.throwIfAborted();
      await rename(extracted, this.file);
      this.verified = true;
      this.log('Workspace kernel ready');
    } finally {
      await Promise.all([rm(archive, { force: true }), rm(extracted, { force: true })]);
    }
  }
}
