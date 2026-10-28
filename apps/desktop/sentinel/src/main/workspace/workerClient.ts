import type { WorkspaceRuntime } from './workspaceRuntime.js';
import { requireRuntimeCapability } from './runtimeCompatibility.js';

/** Stateless protocol adapter. A client never owns a remote lifecycle or catalog. */
export class WorkerClient {
  constructor(private readonly runtime: WorkspaceRuntime) {}

  async request(action: string, values: Record<string, any> = {}): Promise<unknown> {
    if (action === 'status') {
      const [health, inventory] = await Promise.all([
        this.runtime.request('status'), this.runtime.request('workspaces'),
      ]);
      const records = inventory.workspaces ?? {};
      return { worker_id: inventory.worker_id, workspaces: records,
        states: Object.fromEntries(Object.entries(records).map(([id, record]) => [id, {
          state: record.operation ?? (record.error ? 'failed' : health.states?.[id] ?? 'stopped'),
          error: record.error ?? health.errors?.[id],
          resources: record.spec.resources, recovery_backup: record.recovery_backup,
          revision: record.revision,
          recovery_available: Boolean(health.errors?.[id]),
          screen: Boolean(record.screen),
        }])),
      };
    }
    if (action === 'configure') {
      return this.runtime.request('workspace_configure', { workspace: values.workspace, revision: values.revision,
        spec: await this.runtime.spec({ name: values.name, project: values.project, distribution: values.distribution, desktop: values.desktop,
          tools: values.tools, browser: values.browser, resources: values.resources ?? { cpus: 2, memory_gib: 2, disk_gib: 32 } }),
      });
    }
    if (action === 'reinstall') {
      if (values.confirmed !== true) throw new Error('Confirm erasing the workspace Linux disk before reinstalling');
      if (values.spec && values.revision == null) throw new Error('Refresh workspace settings before reinstalling');
      const status = await this.runtime.request('status');
      requireRuntimeCapability(status.capabilities, 'workspace-reinstall-v1', true);
      const inventory = await this.runtime.request('workspaces');
      const record = inventory.workspaces?.[values.workspace];
      if (!record) throw new Error('Workspace not found on this worker');
      if (values.revision != null && values.revision !== record.revision) throw new Error('Workspace settings changed. Refresh before reinstalling.');
      return this.runtime.request('workspace_reinstall', { workspace: values.workspace, revision: record.revision, confirmed: true,
        ...(values.spec ? { spec: await this.runtime.spec({ ...record.spec, ...values.spec }) } : {}),
      });
    }
    const lifecycle: Record<string, string> = {
      prepare: 'workspace_start', stop: 'workspace_stop', delete: 'workspace_delete', recover: 'workspace_recover',
    };
    if (lifecycle[action]) return this.runtime.request(lifecycle[action], { workspace: values.workspace });
    return this.runtime.request(action, values, 31 * 60_000);
  }
}
