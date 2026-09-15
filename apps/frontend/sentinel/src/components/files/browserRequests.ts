// Share requests across remounts and browser panels. Only successful responses
// are cached; explicit refresh expires them without duplicating running work.
type Entry = { promise: Promise<unknown>; expires: number; pending: boolean; invalidated: boolean };
export class BrowserRequests {
  private entries = new Map<string, Entry>();
  private readonly now: () => number;
  private readonly capacity: number;
  constructor(now = Date.now, capacity = 48) { this.now = now; this.capacity = capacity; }

  get<T>(key: string, load: () => Promise<T>, ttl = 3000): Promise<T> {
    const existing = this.entries.get(key);
    if (existing && (existing.pending || existing.expires > this.now())) return existing.promise as Promise<T>;
    const entry: Entry = { promise: Promise.resolve(), expires: 0, pending: true, invalidated: false };
    entry.promise = Promise.resolve().then(load).then(value => {
      entry.pending = false;
      entry.expires = entry.invalidated ? 0 : this.now() + ttl;
      for (const [id, item] of this.entries) {
        if (!item.pending && (item.expires <= this.now() || this.entries.size > this.capacity)) this.entries.delete(id);
      }
      return value;
    }, error => {
      this.entries.delete(key);
      throw error;
    });
    this.entries.set(key, entry);
    return entry.promise as Promise<T>;
  }

  invalidate(prefix: string) {
    for (const [key, entry] of this.entries) {
      if (!key.startsWith(prefix)) continue;
      if (entry.pending) entry.invalidated = true;
      else this.entries.delete(key);
    }
  }
}

export const browserRequests = new BrowserRequests();

export function statusRefreshDelay(duration: number, failures: number) {
  return failures ? Math.min(120000, 30000 * 2 ** (failures - 1)) : Math.min(60000, Math.max(15000, duration * 4));
}

// No overlapping scans or hidden-window polling. Returning to the app refreshes
// only when due, rather than starting a scan on every focus event.
export function pollVisible(task: () => Promise<boolean>, visibility: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'>) {
  let disposed = false, running = false, failures = 0, due = 0;
  let timer: ReturnType<typeof setTimeout>;
  async function tick() {
    clearTimeout(timer);
    if (disposed || running || visibility.visibilityState !== 'visible') return;
    if (Date.now() < due) { timer = setTimeout(tick, due - Date.now()); return; }
    running = true;
    const start = Date.now();
    try { failures = await task() ? 0 : failures + 1; }
    catch { failures++; }
    finally {
      running = false;
      due = Date.now() + statusRefreshDelay(Date.now() - start, failures);
      if (!disposed && visibility.visibilityState === 'visible') timer = setTimeout(tick, due - Date.now());
    }
  }
  const visibilityChanged = () => { void tick(); };
  visibility.addEventListener('visibilitychange', visibilityChanged);
  void tick();
  return () => { disposed = true; clearTimeout(timer); visibility.removeEventListener('visibilitychange', visibilityChanged); };
}
