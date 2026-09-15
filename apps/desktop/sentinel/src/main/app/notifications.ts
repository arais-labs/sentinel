import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { AppNotification, NotificationInput } from '../../shared/notifications.js';

const severities = new Set(['info', 'success', 'warning', 'error', 'urgent']);
const uuid = /^[0-9a-f-]{36}$/i;
function bounded(value: unknown, limit: number): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= limit;
}
export function validateNotification(value: NotificationInput): void {
  if (!value || !bounded(value.source, 80) || !bounded(value.title, 160) || typeof value.message !== 'string' || value.message.length > 4000
      || (value.key !== undefined && !bounded(value.key, 200))
      || (value.severity !== undefined && !severities.has(value.severity))
      || (value.progress !== undefined && typeof value.progress !== 'boolean')
      || (value.durationMs !== undefined && (!Number.isFinite(value.durationMs) || value.durationMs < 1000 || value.durationMs > 30000))) throw new Error('Invalid notification');
  if (value.target) {
    const { instanceName, sessionId, workspaceId } = value.target;
    if ((instanceName !== undefined && !bounded(instanceName, 80))
        || (sessionId !== undefined && (typeof sessionId !== 'string' || !uuid.test(sessionId)))
        || (workspaceId !== undefined && (typeof workspaceId !== 'string' || !uuid.test(workspaceId)))) throw new Error('Invalid notification target');
  }
}

/** Local, bounded inbox. Publishers are independent of rendering and delivery preferences. */
export class NotificationCenter {
  readonly events = new EventEmitter();
  private items: AppNotification[] = [];
  constructor(private readonly file: string) {
    mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    try { this.items = JSON.parse(readFileSync(file, 'utf8')).slice(0, 200); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  }
  list(): AppNotification[] { return structuredClone(this.items); }
  publish(input: NotificationInput): AppNotification {
    validateNotification(input);
    const previous = input.key ? this.items.find(item => item.source === input.source && item.key === input.key) : undefined;
    const content = { source: input.source, key: input.key, title: input.title.trim(), message: input.message.trim(), severity: input.severity ?? 'info', progress: input.progress ?? false, durationMs: input.durationMs, target: input.target };
    if (previous && Object.entries(content).every(([key, value]) => JSON.stringify(previous[key as keyof AppNotification]) === JSON.stringify(value))) return structuredClone(previous);
    const now = new Date().toISOString();
    const meaningful = !previous || !content.progress || previous.progress !== content.progress || previous.severity !== content.severity;
    const item: AppNotification = { ...content, id: previous?.id ?? randomUUID(), createdAt: previous?.createdAt ?? now, updatedAt: now,
      read: meaningful ? false : previous.read, dismissed: meaningful ? false : previous.dismissed };
    this.persist([item, ...this.items.filter(other => other.id !== item.id)].slice(0, 200));
    this.events.emit('published', structuredClone(item), meaningful);
    return structuredClone(item);
  }
  update(id: string | null, action: 'read' | 'dismiss'): void {
    if (!['read', 'dismiss'].includes(action) || (id !== null && typeof id !== 'string')) throw new Error('Invalid notification action');
    this.persist(this.items.map(item => id === null || item.id === id ? { ...item, read: true, ...(action === 'dismiss' ? { dismissed: true } : {}) } : item));
  }
  private persist(items: AppNotification[]): void {
    writeFileSync(this.file + '.next', JSON.stringify(items), { mode: 0o600 });
    renameSync(this.file + '.next', this.file);
    this.items = items;
    this.events.emit('changed', this.list());
  }
}
