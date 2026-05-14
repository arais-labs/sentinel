import { Database, Loader2, Plus, RefreshCw } from 'lucide-react';
import { FormEvent, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { api } from '../lib/api';

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
  workspace_root: string;
  runtime_backend: string;
}

export function InstancePickerPage() {
  const navigate = useNavigate();
  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [name, setName] = useState('main');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setInstances(await api.get<SentinelInstance[]>('/instances'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load instances');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const openInstance = (instanceName: string) => {
    navigate(`/instances/${encodeURIComponent(instanceName)}/sessions`);
  };

  const createInstance = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    try {
      const instance = await api.post<SentinelInstance>('/instances', { name: trimmed });
      setName('');
      setInstances((current) => [...current.filter((row) => row.name !== instance.name), instance].sort((a, b) => a.name.localeCompare(b.name)));
      openInstance(instance.name);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create instance');
    } finally {
      setCreating(false);
    }
  };

  return (
    <AppShell
      title="Instances"
      subtitle="Choose a Sentinel workspace"
      actions={
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-[color:var(--border-subtle)] px-3 text-sm text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)]"
        >
          <RefreshCw size={15} />
          Refresh
        </button>
      }
      hideSidebar
      contentClassName="max-w-5xl w-full mx-auto"
    >
      <div className="space-y-6">
        <form onSubmit={createInstance} className="flex flex-col gap-3 border-b border-[color:var(--border-subtle)] pb-6 sm:flex-row">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="instance name"
            className="h-10 min-w-0 flex-1 rounded-md border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-sm outline-none focus:border-[color:var(--accent-solid)]"
          />
          <button
            type="submit"
            disabled={creating}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[color:var(--accent-solid)] px-4 text-sm font-medium text-[color:var(--app-bg)] disabled:opacity-60"
          >
            {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
            Create
          </button>
        </form>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-[color:var(--text-muted)]">
            <Loader2 size={16} className="animate-spin" />
            Loading instances
          </div>
        ) : instances.length === 0 ? (
          <div className="rounded-md border border-dashed border-[color:var(--border-subtle)] p-8 text-sm text-[color:var(--text-muted)]">
            No instances registered.
          </div>
        ) : (
          <div className="divide-y divide-[color:var(--border-subtle)] border-y border-[color:var(--border-subtle)]">
            {instances.map((instance) => (
              <button
                type="button"
                key={instance.name}
                onClick={() => openInstance(instance.name)}
                className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 px-2 py-4 text-left hover:bg-[color:var(--surface-1)]"
              >
                <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]">
                  <Database size={17} />
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{instance.display_name || instance.name}</div>
                  <div className="truncate font-mono text-[11px] text-[color:var(--text-muted)]">{instance.database_name}</div>
                </div>
                <div className="rounded-md border border-[color:var(--border-subtle)] px-2 py-1 text-xs text-[color:var(--text-secondary)]">
                  {instance.runtime_backend}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
