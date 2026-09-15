import { BrowserWindow, session, shell } from 'electron';
import { randomUUID } from 'node:crypto';
import { previewOrigin, previewTargetPath } from '../../shared/preview.js';
import type { LocalTransport } from '../transport/localTransport.js';

const previews = new Set<BrowserWindow>();

export async function openPreviewWindow(href: string, transport: LocalTransport): Promise<void> {
  const target = previewTargetPath(href);
  if (!target) throw new Error('Invalid workspace preview link');
  const response = await transport.request(new Request(`sentinel://app${target}`));
  if (!response.ok) throw new Error('This preview is no longer available. Ask the agent to reopen the port forward.');
  const details = await response.json() as { url: string; label?: string };
  const origin = previewOrigin(details.url);
  const isolatedSession = session.fromPartition(`preview-${randomUUID()}`);
  isolatedSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolatedSession.setPermissionCheckHandler(() => false);
  const window = new BrowserWindow({
    width: 1200, height: 850, title: details.label || 'Workspace preview',
    webPreferences: { session: isolatedSession, nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  previews.add(window);
  const external = (url: string) => { if (/^https?:\/\//.test(url)) void shell.openExternal(url); };
  window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
  const guard = (event: Electron.Event, url: string) => {
    if (new URL(url).origin === origin) return;
    event.preventDefault(); external(url);
  };
  window.webContents.on('will-navigate', guard);
  window.webContents.on('will-redirect', guard);
  window.on('closed', () => { previews.delete(window); void isolatedSession.clearStorageData(); });
  try { await window.loadURL(`${origin}/`); }
  catch (error) { window.destroy(); throw error; }
}
