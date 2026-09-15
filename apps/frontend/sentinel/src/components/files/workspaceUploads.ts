import { api } from '../../lib/api';
import { browserRequests } from './browserRequests';

export type UploadItem = { name: string; file: File | null };
export type UploadState = { busy: boolean; message: string; error: boolean; completed: number; total: number; bytes: number; totalBytes: number };
const empty: UploadState = { busy: false, message: '', error: false, completed: 0, total: 0, bytes: 0, totalBytes: 0 };
const jobs = new Map<string, { state: UploadState; controller: AbortController | null }>();
const listeners = new Set<() => void>();
const key = (instance: string, workspace: string) => JSON.stringify([instance, workspace]);
export const subscribeUploads = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const uploadState = (instance: string, workspace: string) => jobs.get(key(instance, workspace))?.state ?? empty;
export function cancelUpload(instance: string, workspace: string) { jobs.get(key(instance, workspace))?.controller?.abort(); }
export function clearUpload(instance: string, workspace: string) {
  if (uploadState(instance, workspace).busy) return;
  jobs.delete(key(instance, workspace)); listeners.forEach(listener => listener());
}

// Jobs belong to a workspace, not a mounted pane or active conversation.
export async function startUpload(instance: string, workspace: string, folder: string, items: Promise<UploadItem[]> | UploadItem[]) {
  const id = key(instance, workspace);
  if (jobs.get(id)?.state.busy) return;
  const controller = new AbortController();
  const job = { state: { ...empty, busy: true, message: 'Preparing upload…' }, controller: controller as AbortController | null };
  jobs.set(id, job);
  const update = (patch: Partial<UploadState>) => { job.state = { ...job.state, ...patch }; listeners.forEach(listener => listener()); };
  update({});
  const prefix = `/instances/${encodeURIComponent(instance)}/workspaces/${workspace}/browse/`;
  let completed = 0, confirmedBytes = 0, savedName = '';
  try {
    const files = await items;
    update({ total: files.length, totalBytes: files.reduce((sum, item) => sum + (item.file?.size ?? 0), 0) });
    for (const item of files) {
      if (controller.signal.aborted) break;
      const transferId = crypto.randomUUID();
      update({ message: `Uploading ${completed + 1} of ${files.length}: ${item.name}` });
      const query = new URLSearchParams({ path: folder, name: item.name, size: String(item.file?.size ?? 0), kind: item.file ? 'file' : 'directory', transfer_id: transferId });
      let polling = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const poll = async () => {
        try {
          const status = await api.get<{ received: number }>(`${prefix}uploads/${transferId}`, { timeoutMs: 5000 });
          if (polling) update({ bytes: confirmedBytes + Math.min(status.received, item.file?.size ?? 0) });
        } catch { /* The upload response owns error reporting; progress is best effort. */ }
        if (polling) timer = setTimeout(() => void poll(), 500);
      };
      timer = setTimeout(() => void poll(), 250);
      try {
        const result = await api.upload<{ name: string }>(`${prefix}upload?${query}`, item.file ?? new Blob([]), controller.signal);
        savedName = result.name;
        completed++; confirmedBytes += item.file?.size ?? 0;
        update({ completed, bytes: confirmedBytes });
      } finally { polling = false; clearTimeout(timer); }
    }
    update({ message: controller.signal.aborted ? `Upload cancelled. ${completed} of ${files.length} items saved.` : files.length === 1 ? `Uploaded ${savedName}` : `Uploaded ${completed} items to ${folder || 'project folder'}` });
  } catch (reason) {
    update({ error: !controller.signal.aborted, message: controller.signal.aborted ? `Upload cancelled. ${completed} items saved.` : `${reason instanceof Error ? reason.message : 'Upload failed'}${completed ? ` ${completed} items were saved.` : ''}` });
  } finally {
    job.controller = null;
    update({ busy: false });
    browserRequests.invalidate(prefix);
    window.dispatchEvent(new CustomEvent('sentinel:workspace-files-uploaded', { detail: { instance, workspaceId: workspace } }));
  }
}
