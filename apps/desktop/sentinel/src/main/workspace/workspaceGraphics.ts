import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import type { WorkspaceDistribution } from './workspaceDistributions.js';
import type { WorkspaceRuntime, RuntimeReply } from './workspaceRuntime.js';

const maxChunk = 32768;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Graphics always belong to the native runtime on the VM's owning machine. */
export class WorkspaceGraphics {
  constructor(private runtime: WorkspaceRuntime, private resources: string) {}
  async install(workspace: string, distribution: WorkspaceDistribution = 'alpine') {
    const bundle = distribution === 'alpine' ? 'mesa-linux-arm64' : 'mesa-linux-arm64-glibc';
    const manifest = JSON.parse(await readFile(path.join(this.resources, `${bundle}.json`), 'utf8'));
    if (!/^[a-f0-9]{64}$/.test(manifest.sha256) || typeof manifest.version !== 'string') throw new Error('Invalid bundled desktop graphics manifest');
    const current = await this.runtime.request('exec', { workspace, arguments: ['cat', '/opt/sentinel/graphics/bundle.sha256'], timeout: 5 });
    if (current.exitCode === 0 && current.stdout?.trim() === manifest.sha256) return;
    const archive = await readFile(path.join(this.resources, `${bundle}.tar.xz`));
    if (createHash('sha256').update(archive).digest('hex') !== manifest.sha256) throw new Error('Bundled desktop graphics checksum mismatch');
    const script = await readFile(path.join(this.resources, 'install-guest.py'), 'utf8');
    await this.stop(workspace);
    const process = randomUUID();
    let output = '', exited = false;
    let timer: ReturnType<typeof setTimeout>;
    let receive!: (reply: RuntimeReply) => void;
    const finished = new Promise<void>((resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Desktop graphics installation timed out')), 120000);
      receive = reply => {
        if (reply.data) output = (output + Buffer.from(reply.data, 'base64').toString()).slice(-4096);
        if (reply.event === 'exit') {
          exited = true;
          if (reply.exitCode === 0) resolve();
          else reject(new Error(output || 'Desktop graphics installation failed'));
        }
      };
    });
    void finished.catch(() => {});
    this.runtime.events.on(process, receive);
    try {
      await this.runtime.request('process_start', { workspace, process, terminal: false,
        arguments: ['python3', '-u', '-c', script, String(archive.length), manifest.sha256, manifest.version] });
      for (let offset = 0; offset < archive.length; offset += maxChunk) {
        await this.runtime.request('process_input', { workspace, process, data: archive.subarray(offset, offset + maxChunk).toString('base64') }, 10000);
      }
      await finished;
    } finally {
      clearTimeout(timer!);
      this.runtime.events.off(process, receive);
      if (!exited && this.runtime.isReady) await this.runtime.request('process_stop', { workspace, process }, 5000).catch(() => {});
    }
  }
  async start(workspace: string) {
    if (!uuid.test(workspace)) throw new Error('A workspace UUID is required');
    try { await this.runtime.request('graphics_start', { workspace }); }
    catch (error) {
      if (String(error).includes('Unknown runtime action')) throw new Error('Update this machine’s Sentinel Runtime to enable host-side desktop graphics.');
      throw error;
    }
  }
  async stop(workspace: string) {
    if (!uuid.test(workspace)) throw new Error('A workspace UUID is required');
    if (!this.runtime.isReady) return;
    try { await this.runtime.request('graphics_stop', { workspace }); }
    catch (error) {
      // Old runtimes have no host graphics to stop; installation may upgrade them.
      if (!String(error).includes('Unknown runtime action')) throw error;
    }
  }
  async close() {
    // Closing a viewer/SSH bridge must not destroy another viewer's graphics.
  }
}
