import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import type { ApprovalListResponse, SubAgentTask } from '../../types/api';

/** Poll only while this chat has active children; permissions stay child-scoped. */
export function useSubAgentApprovals(instanceName: string | null, tasks: SubAgentTask[]) {
  const sessions = JSON.stringify(tasks
    .filter(task => task.status === 'running' || task.status === 'pending')
    .map(task => task.result?.child_session_id)
    .filter((id): id is string => typeof id === 'string').sort());
  const [pending, setPending] = useState<Set<string>>(new Set());
  useEffect(() => {
    setPending(new Set());
    const ids: string[] = JSON.parse(sessions);
    if (!instanceName || !ids.length) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      const results = await Promise.allSettled(ids.map(async id => {
        const result = await api.get<ApprovalListResponse>(`/instances/${encodeURIComponent(instanceName!)}/approvals?status=pending&session_id=${encodeURIComponent(id)}&limit=1`);
        return { id, pending: result.items.some(item => item.pending) };
      }));
      if (stopped) return;
      setPending(previous => {
        const next = new Set(previous);
        for (const result of results) if (result.status === 'fulfilled') {
          if (result.value.pending) next.add(result.value.id);
          else next.delete(result.value.id);
        }
        return next;
      });
      timer = setTimeout(refresh, 3000);
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, [instanceName, sessions]);
  return pending;
}
