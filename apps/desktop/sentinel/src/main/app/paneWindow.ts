import { BrowserWindow, screen } from 'electron';
import type { PaneWindowRequest } from '../../shared/ipc.js';
import { PaneWindowBoundsStore, PaneWindowRegistry, type PaneWindowEntry } from './paneWindowRegistry.js';

const DEFAULT_SIZE = { width: 1100, height: 760 };

export interface PaneWindowOptions {
  request: PaneWindowRequest;
  baseUrl: string;
  preload: string;
  registry: PaneWindowRegistry<BrowserWindow>;
  bounds: PaneWindowBoundsStore;
  isSentinelUrl: (url: string) => boolean;
  openExternal: (url: string) => void;
  onClosed: (entry: PaneWindowEntry<BrowserWindow>) => void;
  log?: (line: string) => void;
}

function initialBounds(saved: { x?: number; y?: number; width: number; height: number } | undefined) {
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  const width = Math.min(saved?.width ?? DEFAULT_SIZE.width, area.width);
  const height = Math.min(saved?.height ?? DEFAULT_SIZE.height, area.height);
  const inside = saved?.x !== undefined && saved.y !== undefined && screen.getAllDisplays().some(display =>
    saved.x! >= display.workArea.x && saved.y! >= display.workArea.y &&
    saved.x! + width <= display.workArea.x + display.workArea.width && saved.y! + height <= display.workArea.y + display.workArea.height);
  return inside
    ? { x: saved!.x!, y: saved!.y!, width, height }
    : { x: Math.round(area.x + (area.width - width) / 2), y: Math.round(area.y + (area.height - height) / 2), width, height };
}

export async function openPaneWindow(options: PaneWindowOptions): Promise<BrowserWindow> {
  const { request, registry } = options;
  const existing = registry.find(request);
  if (existing && !existing.window.isDestroyed()) {
    if (existing.window.isMinimized()) existing.window.restore();
    existing.window.focus();
    return existing.window;
  }
  const window = new BrowserWindow({
    ...initialBounds(options.bounds.get(request.tabId)),
    minWidth: 520, minHeight: 360,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 14, y: 14 },
    title: request.title ? `${request.title} — Sentinel` : 'Sentinel',
    backgroundColor: '#09090b',
    webPreferences: { preload: options.preload, contextIsolation: true, nodeIntegration: false, sandbox: false, backgroundThrottling: false, autoplayPolicy: 'no-user-gesture-required' },
  });
  const entry = registry.add(window, request);
  const contentsId = window.webContents.id;
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (!options.isSentinelUrl(url)) options.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (options.isSentinelUrl(url)) return;
    event.preventDefault();
    options.openExternal(url);
  });
  const label = `pane window ${request.tabId}`;
  window.webContents.on('console-message', event => {
    if (event.level === 'error') options.log?.(`${label}: ${event.message} (${event.sourceId}:${event.lineNumber})`);
  });
  window.webContents.on('did-fail-load', (_event, code, description, failedUrl) => {
    options.log?.(`${label}: failed to load ${failedUrl}: ${code} ${description}`);
  });
  window.webContents.on('render-process-gone', (_event, details) => {
    options.log?.(`${label}: renderer gone: ${details.reason}`);
  });
  window.on('close', () => {
    const { x, y, width, height } = window.getNormalBounds();
    options.bounds.set(request.tabId, { x, y, width, height });
  });
  window.once('closed', () => {
    registry.delete(contentsId);
    options.onClosed(entry);
  });
  const url = new URL(`/instances/${encodeURIComponent(request.instance)}/pane`, options.baseUrl);
  url.search = new URLSearchParams({
    tab: request.tabId, pane: request.paneId, ...(request.session ? { session: request.session } : {}),
  }).toString();
  try { await window.loadURL(url.toString()); }
  catch (error) { window.destroy(); throw error; }
  return window;
}
