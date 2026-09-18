import { readFileSync, writeFileSync } from 'node:fs';
import type { PaneWindowRequest } from '../../shared/ipc.js';

export interface PaneWindowBounds { x?: number; y?: number; width: number; height: number; }

export interface PaneWindowHost {
  webContents: { id: number; mainFrame: unknown };
  close(): void;
  focus(): void;
  isDestroyed(): boolean;
  isMinimized(): boolean;
  restore(): void;
}

export interface PaneWindowEntry<W extends PaneWindowHost = PaneWindowHost> { window: W; request: PaneWindowRequest; }

export function paneWindowKey(request: Pick<PaneWindowRequest, 'instance' | 'session' | 'tabId'>): string {
  return JSON.stringify([request.instance, request.session ?? null, request.tabId]);
}

export function validatePaneWindowRequest(value: unknown): PaneWindowRequest {
  const request = value as Partial<PaneWindowRequest> | null;
  if (!request || typeof request.instance !== 'string' || !request.instance || typeof request.tabId !== 'string' || !/^[a-z-]+$/.test(request.tabId) ||
      (request.session !== null && request.session !== undefined && typeof request.session !== 'string') ||
      typeof request.paneId !== 'string' || !request.paneId || typeof request.title !== 'string') throw new Error('Invalid pane window request');
  return { instance: request.instance, session: request.session ?? null, tabId: request.tabId, paneId: request.paneId, title: request.title.slice(0, 120) };
}

/** Pane windows keyed by webContents id; one window per (instance, session, tab). */
export class PaneWindowRegistry<W extends PaneWindowHost = PaneWindowHost> {
  private readonly entries = new Map<number, PaneWindowEntry<W>>();

  add(window: W, request: PaneWindowRequest): PaneWindowEntry<W> {
    const entry = { window, request };
    this.entries.set(window.webContents.id, entry);
    return entry;
  }

  get(contentsId: number): PaneWindowEntry<W> | undefined { return this.entries.get(contentsId); }
  delete(contentsId: number): void { this.entries.delete(contentsId); }
  all(): PaneWindowEntry<W>[] { return [...this.entries.values()]; }
  get size(): number { return this.entries.size; }

  find(request: Pick<PaneWindowRequest, 'instance' | 'session' | 'tabId'>): PaneWindowEntry<W> | undefined {
    const key = paneWindowKey(request);
    return this.all().find(entry => paneWindowKey(entry.request) === key);
  }

  /** True when `frame` is the main frame of a registered pane window. */
  ownsFrame(contentsId: number, frame: unknown): boolean {
    const entry = this.entries.get(contentsId);
    return Boolean(entry && !entry.window.isDestroyed() && frame && entry.window.webContents.mainFrame === frame);
  }

  closeAll(): void {
    for (const entry of this.all()) if (!entry.window.isDestroyed()) entry.window.close();
  }
}

/** Sender guard shared by the main window and pane windows; form windows opt out separately. */
export function isTrustedDesktopSender(
  sender: { id: number; frame: unknown; url: string | undefined },
  mainFrame: unknown,
  registry: PaneWindowRegistry<PaneWindowHost>,
  isSentinelUrl: (url: string) => boolean,
): boolean {
  if (!sender.frame || !sender.url || !isSentinelUrl(sender.url)) return false;
  return sender.frame === mainFrame || registry.ownsFrame(sender.id, sender.frame);
}

/** Last window bounds per tab, in a small JSON file next to the other desktop settings. */
export class PaneWindowBoundsStore {
  constructor(private readonly file: string) {}

  private read(): Record<string, PaneWindowBounds> {
    try { return JSON.parse(readFileSync(this.file, 'utf8')); } catch { return {}; }
  }

  get(tabId: string): PaneWindowBounds | undefined {
    const bounds = this.read()[tabId];
    return bounds && Number.isFinite(bounds.width) && Number.isFinite(bounds.height) ? bounds : undefined;
  }

  set(tabId: string, bounds: PaneWindowBounds): void {
    try { writeFileSync(this.file, JSON.stringify({ ...this.read(), [tabId]: bounds })); }
    catch { /* a failed write only means the size is not remembered */ }
  }
}
