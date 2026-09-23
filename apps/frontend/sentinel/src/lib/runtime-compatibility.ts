import { ApiError } from './api';

export interface RuntimeUpdateRequirement {
  code: 'runtime_update_required' | 'app_update_required';
  message: string;
  machine_id?: string;
}

export function runtimeUpdateRequirement(error: unknown): RuntimeUpdateRequirement | undefined {
  if (!(error instanceof ApiError) || !['runtime_update_required', 'app_update_required'].includes(error.code ?? '')) return;
  const details = error.details as { machine_id?: unknown } | undefined;
  return { code: error.code as RuntimeUpdateRequirement['code'], message: error.message,
    machine_id: typeof details?.machine_id === 'string' ? details.machine_id : undefined };
}
