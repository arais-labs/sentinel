import { useState, useSyncExternalStore, type DragEvent } from 'react';
import { cancelUpload, clearUpload, startUpload, subscribeUploads, uploadState, type UploadItem } from './workspaceUploads';
type Entry = FileSystemEntry;
const fileDrag = (event: DragEvent) => Array.from(event.dataTransfer.types).includes('Files');

// Capture entries during the drop event; browsers clear DataTransfer afterwards.
export async function droppedItems(data: DataTransfer): Promise<UploadItem[]> {
  const entries = Array.from(data.items).filter(item => item.kind === 'file').map(item => ({
    entry: item.webkitGetAsEntry?.(), file: item.getAsFile(),
  }));
  const result: UploadItem[] = [];
  const add = (item: UploadItem) => {
    if (result.length >= 10000) throw new Error('Drop up to 10,000 files and folders at a time.');
    result.push(item);
  };
  async function visit(entry: Entry, parent = '') {
    const name = parent + entry.name;
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
      add({ name, file });
    } else if (entry.isDirectory) {
      add({ name, file: null });
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      while (true) {
        const children = await new Promise<Entry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (!children.length) break;
        for (const child of children) await visit(child, name + '/');
      }
    }
  }
  if (!entries.length) return Array.from(data.files).map(file => ({ name: file.name, file }));
  for (const { entry, file } of entries) {
    if (entry) await visit(entry);
    else if (file) add({ name: file.name, file });
  }
  return result;
}

export function useFileUploads({ instance, workspaceId, root, disabled }: {
  instance: string; workspaceId: string; root: string; disabled: boolean;
}) {
  const state = useSyncExternalStore(subscribeUploads, () => uploadState(instance, workspaceId));
  const [target, setTarget] = useState<string | null>(null);
  const destination = (event: DragEvent) => (event.target instanceof Element
    ? event.target.closest<HTMLElement>('[data-upload-directory]')?.dataset.uploadDirectory : undefined) ?? root;
  return {
    ...state, target, clear: () => clearUpload(instance, workspaceId), cancel: () => cancelUpload(instance, workspaceId),
    choose: (files: FileList) => { if (!disabled) void startUpload(instance, workspaceId, root, Array.from(files).map(file => ({ name: file.name, file }))); },
    handlers: {
      onDragOverCapture: (event: DragEvent) => {
        if (!fileDrag(event)) return;
        event.preventDefault(); event.stopPropagation();
        event.dataTransfer.dropEffect = disabled || state.busy ? 'none' : 'copy';
        setTarget(destination(event));
      },
      onDragLeave: (event: DragEvent) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setTarget(null);
      },
      onDropCapture: (event: DragEvent) => {
        if (!fileDrag(event)) return;
        event.preventDefault(); event.stopPropagation(); setTarget(null);
        if (disabled || state.busy) return;
        void startUpload(instance, workspaceId, destination(event), droppedItems(event.dataTransfer));
      },
    },
  };
}
