import type { MessageAttachment } from '../types/api';

export function extractImageAttachments(metadata: Record<string, unknown> | null | undefined): MessageAttachment[] {
  if (!Array.isArray(metadata?.attachments)) return [];
  return metadata.attachments.flatMap(item => {
    if (!item || typeof item !== 'object' || typeof item.base64 !== 'string' || !item.base64) return [];
    if (typeof item.mime_type !== 'string' || !item.mime_type.startsWith('image/')) return [];
    return [{ mime_type: item.mime_type, base64: item.base64, filename: typeof item.filename === 'string' ? item.filename : null }];
  });
}

export function imageSource(image: MessageAttachment): string {
  return `data:${image.mime_type};base64,${image.base64}`;
}

async function clipboardPng(image: MessageAttachment): Promise<Blob> {
  const response = await fetch(imageSource(image));
  const blob = await response.blob();
  if (blob.type === 'image/png') return blob;
  // Clipboard image support is PNG; convert other formats at their original size.
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Image conversion unavailable');
    context.drawImage(bitmap, 0, 0);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('Image conversion failed')), 'image/png'));
  } finally { bitmap.close(); }
}

export async function copyImage(image: MessageAttachment): Promise<void> {
  // Start the clipboard write in the click gesture, before asynchronous decoding.
  return navigator.clipboard.write([new ClipboardItem({ 'image/png': clipboardPng(image) })]);
}
