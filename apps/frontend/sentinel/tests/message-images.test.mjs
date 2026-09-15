import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('image attachments preserve all formats and copy the chosen image bytes', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    server: { middlewareMode: true }, logLevel: 'error', optimizeDeps: { noDiscovery: true, include: [] } });
  const previousClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const previousItem = globalThis.ClipboardItem;
  const previousDocument = globalThis.document;
  const previousBitmap = globalThis.createImageBitmap;
  try {
    const { extractImageAttachments, imageSource, copyImage } = await server.ssrLoadModule('/src/lib/message-images.ts');
    const images = extractImageAttachments({ attachments: [
      { mime_type: 'image/jpeg', base64: 'anBlZw==', filename: 'first.jpg' },
      { mime_type: 'application/pdf', base64: 'cGRm' }, null,
      { mime_type: 'image/png', base64: 'c2Vjb25k', filename: 'second.png' },
      { mime_type: 'image/webp', base64: 'd2VicA==' },
      { mime_type: 'image/png', base64: '' },
    ] });
    assert.equal(images.length, 3);
    assert.equal(imageSource(images[0]), 'data:image/jpeg;base64,anBlZw==');
    assert.equal(images[1].filename, 'second.png');
    assert.equal(images[2].mime_type, 'image/webp');
    assert.deepEqual(extractImageAttachments(null), []);
    let copied;
    globalThis.ClipboardItem = class { constructor(data) { this.data = data; } };
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write: async items => { copied = items; } } });
    await copyImage(images[1]);
    assert.equal(copied.length, 1);
    assert.deepEqual(Object.keys(copied[0].data), ['image/png']);
    const blob = await copied[0].data['image/png'];
    assert.equal(blob.type, 'image/png');
    assert.equal(await blob.text(), 'second');
    let bitmapClosed = false;
    const bitmap = { width: 1600, height: 3200, close: () => { bitmapClosed = true; } };
    const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: image => assert.equal(image, bitmap) }),
      toBlob: (callback, type) => callback(new Blob(['converted'], { type })) };
    globalThis.createImageBitmap = async () => bitmap;
    globalThis.document = { createElement: name => { assert.equal(name, 'canvas'); return canvas; } };
    await copyImage(images[0]);
    const converted = await copied[0].data['image/png'];
    assert.equal(converted.type, 'image/png');
    assert.equal(canvas.width, 1600);
    assert.equal(canvas.height, 3200);
    assert.equal(bitmapClosed, true);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write: async () => { throw new Error('denied'); } } });
    await assert.rejects(copyImage(images[1]), /denied/);
  } finally {
    if (previousClipboard) Object.defineProperty(navigator, 'clipboard', previousClipboard);
    else delete navigator.clipboard;
    if (previousItem) globalThis.ClipboardItem = previousItem;
    else delete globalThis.ClipboardItem;
    if (previousDocument) globalThis.document = previousDocument;
    else delete globalThis.document;
    if (previousBitmap) globalThis.createImageBitmap = previousBitmap;
    else delete globalThis.createImageBitmap;
    await server.close();
  }
});
