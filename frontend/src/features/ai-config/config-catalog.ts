import type { AiConfig, ServiceType } from './config-model';

interface CatalogPage { items: AiConfig[]; total: number }
interface CatalogSnapshot { items: AiConfig[]; loading: boolean; error: string }
type CatalogLoader = (kind: ServiceType, offset: number, limit: number, signal: AbortSignal) => Promise<CatalogPage>;
interface Entry {
  snapshot: CatalogSnapshot;
  stale: boolean;
  listeners: Set<() => void>;
  controller: AbortController | null;
}

/** Read-only model catalogs belong to one authenticated account and share in-flight reads. */
export class ConfigCatalog {
  private entries = new Map<ServiceType, Entry>();

  constructor(private loadPage: CatalogLoader, private formatError: (cause: unknown) => string) {}

  private entry(kind: ServiceType) {
    let value = this.entries.get(kind);
    if (!value) {
      value = { snapshot: { items: [], loading: true, error: '' }, stale: true, listeners: new Set(), controller: null };
      this.entries.set(kind, value);
    }
    return value;
  }

  getSnapshot(kind: ServiceType) { return this.entry(kind).snapshot; }

  subscribe(kind: ServiceType, listener: () => void) {
    const entry = this.entry(kind);
    entry.listeners.add(listener);
    if (entry.stale && !entry.controller) void this.load(kind, entry);
    return () => {
      entry.listeners.delete(listener);
      // StrictMode resubscribes synchronously. One selector leaving must not cancel others.
      queueMicrotask(() => {
        if (entry.listeners.size || !entry.controller) return;
        entry.controller.abort(); entry.controller = null; entry.stale = true;
      });
    };
  }

  private publish(entry: Entry, snapshot: CatalogSnapshot) {
    entry.snapshot = snapshot;
    for (const listener of entry.listeners) listener();
  }

  refresh(kind: ServiceType) {
    const entry = this.entry(kind);
    entry.controller?.abort(); entry.controller = null; entry.stale = true;
    this.publish(entry, { items: [], loading: true, error: '' });
    if (entry.listeners.size) void this.load(kind, entry);
  }

  invalidate() { for (const kind of this.entries.keys()) this.refresh(kind); }

  private async load(kind: ServiceType, entry: Entry) {
    const controller = new AbortController(); entry.controller = controller; entry.stale = false;
    this.publish(entry, { items: [], loading: true, error: '' });
    try {
      const items: AiConfig[] = [];
      let offset = 0;
      do {
        const page = await this.loadPage(kind, offset, 100, controller.signal);
        if (controller.signal.aborted || entry.controller !== controller) return;
        items.push(...page.items); offset += page.items.length;
        if (!page.items.length || offset >= page.total) break;
      } while (!controller.signal.aborted);
      if (!controller.signal.aborted && entry.controller === controller) this.publish(entry, { items, loading: false, error: '' });
    } catch (cause) {
      if (!controller.signal.aborted && entry.controller === controller) this.publish(entry, { items: [], loading: false, error: this.formatError(cause) });
    } finally { if (entry.controller === controller) entry.controller = null; }
  }
}
