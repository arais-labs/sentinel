import { requestJson } from './api';
import type { ModelsResponse } from '../types/api';

// Share only in-flight reads: opening a picker always checks the current runtime,
// but several retained panes opening together make a single request per instance.
const pending = new Map<string, { controller: AbortController; promise: Promise<ModelsResponse> }>();

export function loadModelCatalog(instance: string): Promise<ModelsResponse> {
  const existing = pending.get(instance);
  if (existing) return existing.promise;
  const controller = new AbortController();
  const promise = requestJson<ModelsResponse>(`/instances/${encodeURIComponent(instance)}/models`, { signal: controller.signal })
    .finally(() => {
      if (pending.get(instance)?.controller === controller) pending.delete(instance);
    });
  pending.set(instance, { controller, promise });
  return promise;
}

export function invalidateModelCatalog(instance: string) {
  // A read started before a settings write must not win over the updated catalog.
  pending.get(instance)?.controller.abort();
  pending.delete(instance);
}
