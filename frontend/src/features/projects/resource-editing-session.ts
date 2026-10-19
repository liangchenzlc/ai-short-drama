export interface ResourceRecovery<Document> {
  format: 1; base_version: string; document: Document;
  pending?: { base_version: string; document: Document };
}
export interface ResourceEditingOptions<Resource, Document> {
  read(): Promise<Resource>;
  save(resource: Resource, document: Document, assertActive: () => void): Promise<Resource>;
  version(resource: Resource): string;
  document(resource: Resource): Document;
  apply(resource: Resource, document: Document): Resource;
  recovery: {
    read(): ResourceRecovery<Document> | null;
    save(value: ResourceRecovery<Document>): void;
    clear(): void;
    export?(): string | null;
    previous?(): string | null;
    backup?(raw: string): void;
  };
  pauseMs?: number;
}
export interface ResourceEditingSnapshot<Resource, Document> {
  value: Resource | null; loaded: boolean; dirty: boolean; busy: boolean;
  status: 'loading' | 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict';
  message: string; recoveryBlocked: boolean; recovered: ResourceRecovery<Document> | null;
}
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** A resource queue belongs to the account registry, never to a mounted editor. */
export class ResourceEditingSession<Resource, Document> {
  private state: ResourceEditingSnapshot<Resource, Document> = {
    value: null, loaded: false, dirty: false, busy: false, status: 'loading',
    message: '', recoveryBlocked: false, recovered: null,
  };
  private acknowledged: Resource | null = null;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;
  private loading: Promise<Resource | null> | null = null;
  private reading = new Set<symbol>();
  private readSequence = 0;
  private pending: ResourceRecovery<Document>['pending'];
  private recoveryBase: string | null = null;
  private memoryOnly = false;
  private active = true;
  private disposed = false;
  private epoch = 0;
  private failures = 0;
  constructor(private readonly options: ResourceEditingOptions<Resource, Document>) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  hasUnsettled = () => this.state.dirty || this.state.busy || !!this.running || this.state.recoveryBlocked || ['error', 'conflict'].includes(this.state.status);
  hasMemoryOnlyChanges = () => this.memoryOnly && this.state.dirty;
  hasUnpersisted = this.hasMemoryOnlyChanges;
  private publish(patch: Partial<ResourceEditingSnapshot<Resource, Document>>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener());
  }
  private persist() {
    if (!this.acknowledged || !this.state.value || this.state.recoveryBlocked) { this.memoryOnly = this.state.dirty; return; }
    try {
      if (this.state.dirty || this.pending) this.options.recovery.save({
        format: 1, base_version: this.recoveryBase ?? this.options.version(this.acknowledged),
        document: this.options.document(this.state.value), ...(this.pending ? { pending: this.pending } : {}),
      });
      else this.options.recovery.clear();
      this.memoryOnly = false;
    } catch {
      this.memoryOnly = this.state.dirty;
      this.publish({ message: '本机无法保留当前输入，正在尝试同步；可复制或下载备份。' });
    }
  }
  private schedule(delay = this.options.pauseMs ?? 1000) {
    clearTimeout(this.timer);
    if (this.active && !this.disposed && this.state.dirty && !this.state.recoveryBlocked && !['conflict', 'error'].includes(this.state.status))
      this.timer = setTimeout(() => { void this.flush(); }, delay);
  }
  initialize = (remote: Resource) => {
    if (this.state.loaded) {
      if (!this.state.dirty && !this.running && !this.pending
        && (!this.acknowledged || BigInt(this.options.version(remote)) >= BigInt(this.options.version(this.acknowledged)))) {
        this.acknowledged = remote; this.publish({ value: remote });
      }
      return;
    }
    this.acknowledged = remote;
    let recovery: ResourceRecovery<Document> | null = null;
    try { recovery = this.options.recovery.read(); }
    catch { this.publish({ recoveryBlocked: true, message: '本机恢复记录无法读取，原记录已保留。请下载后核对。' }); }
    if (!recovery) { this.publish({ value: remote, loaded: true, status: this.state.recoveryBlocked ? 'error' : 'saved' }); return; }
    const document = this.options.document(remote);
    const unresolved = recovery.pending && this.options.version(remote) === recovery.pending.base_version;
    if (equal(document, recovery.document) && !unresolved) { this.publish({ value: remote, loaded: true, status: 'saved' }); this.persist(); return; }
    const accepted = recovery.pending && !unresolved && equal(document, recovery.pending.document);
    const conflict = !accepted && recovery.base_version !== this.options.version(remote);
    this.recoveryBase = conflict ? recovery.base_version : null;
    this.pending = accepted ? undefined : recovery.pending;
    this.publish({ value: this.options.apply(remote, recovery.document), loaded: true, dirty: true,
      status: conflict ? 'conflict' : 'unsaved', recovered: conflict ? recovery : null,
      message: conflict ? '服务端版本已变化，本机输入已保留。请核对两个版本。' : '' });
    this.persist(); this.schedule();
  };
  load = async (propagateError = false, freshRead = false): Promise<Resource | null> => {
    if (!this.active || this.disposed) return null;
    if (this.loading && !freshRead) return this.loading;
    const epoch = this.epoch;
    const sequence = ++this.readSequence;
    const readToken = Symbol(); this.reading.add(readToken);
    this.publish({ busy: true });
    const operation = (async () => {
      try {
        const remote = await this.options.read();
        if (!this.active || this.disposed || epoch !== this.epoch) return null;
        if (sequence === this.readSequence) this.initialize(remote);
        return remote;
      } catch (cause) {
        if (this.active && !this.disposed && epoch === this.epoch && sequence === this.readSequence && !this.state.loaded) this.publish({ status: 'error', message: cause instanceof Error ? cause.message : '内容读取失败，请重试。' });
        if (propagateError) throw cause;
        return null;
      } finally {
        this.reading.delete(readToken);
        if (this.active && !this.disposed && epoch === this.epoch) this.publish({ busy: this.running !== null || this.reading.size > 0 });
      }
    })();
    this.loading = operation;
    try { return await operation; } finally { if (this.loading === operation) this.loading = null; }
  };
  edit = (document: Document) => {
    if (!this.active || this.disposed || !this.state.value || !this.acknowledged) return;
    if (equal(document, this.options.document(this.state.value))) return;
    const dirty = !!this.pending || !!this.running || !equal(document, this.options.document(this.acknowledged));
    const paused = ['error', 'conflict'].includes(this.state.status);
    this.publish({ value: this.options.apply(this.state.value, document), dirty,
      ...(!paused ? { status: this.running ? 'saving' as const : dirty ? 'unsaved' as const : 'saved' as const, message: '' } : {}) });
    this.persist(); this.schedule();
  };
  private async reconcile(epoch: number) {
    if (!this.pending || !this.acknowledged) return true;
    const remote = await this.options.read();
    if (!this.active || this.disposed || epoch !== this.epoch) return false;
    if (this.options.version(remote) === this.pending.base_version) {
      // The earlier request can still commit; the latest input must compete via CAS.
      this.acknowledged = remote; this.publish({ dirty: true }); return true;
    }
    if (equal(this.options.document(remote), this.pending.document)) {
      const current = this.options.document(this.state.value!);
      this.acknowledged = remote; this.pending = undefined; this.recoveryBase = null;
      this.publish({ value: this.options.apply(remote, current), dirty: !equal(current, this.options.document(remote)) });
      this.persist(); return true;
    }
    this.publish({ status: 'conflict', message: '服务端版本已变化，本机输入已保留。请核对两个版本。' }); return false;
  }
  flush = (): Promise<boolean> => {
    clearTimeout(this.timer);
    if (this.running) return this.running;
    if (!this.active || this.disposed || !this.acknowledged || !this.state.value || this.state.recoveryBlocked || ['error', 'conflict'].includes(this.state.status)) return Promise.resolve(false);
    const epoch = this.epoch;
    const operation = (async () => {
      this.publish({ busy: true, ...(this.state.dirty ? { status: 'saving' as const } : {}) });
      try {
        if (!await this.reconcile(epoch)) return false;
        while (this.state.dirty && this.active && !this.disposed && epoch === this.epoch) {
          const before = this.acknowledged!;
          const document = this.options.document(this.state.value!);
          this.pending = { base_version: this.options.version(before), document }; this.persist();
          const assertActive = () => {
            if (!this.active || this.disposed || epoch !== this.epoch) throw new Error('编辑会话已切换，原输入仍保留。');
          };
          assertActive();
          const saved = await this.options.save(before, document, assertActive);
          if (!this.active || this.disposed || epoch !== this.epoch) return false;
          const latest = this.options.document(this.state.value!);
          this.acknowledged = saved; this.pending = undefined; this.recoveryBase = null;
          const newer = !equal(latest, document);
          this.publish({ value: newer ? this.options.apply(saved, latest) : saved,
            dirty: newer && !equal(latest, this.options.document(saved)), recovered: null });
          this.persist();
        }
        if (!this.active || this.disposed || epoch !== this.epoch) return false;
        this.failures = 0; this.publish({ status: 'saved', message: this.memoryOnly ? this.state.message : '' }); return true;
      } catch (cause) {
        if (!this.active || this.disposed || epoch !== this.epoch) return false;
        const error = cause as { status?: number; message?: string };
        const conflict = error.status === 409;
        this.publish({ status: conflict ? 'conflict' : 'error',
          message: conflict ? '服务端版本已变化，本机输入已保留。请核对两个版本。' : error.message ?? '暂时无法同步，输入已保留。' });
        this.persist();
        if (!conflict && (!error.status || error.status >= 500) && this.failures++ < 3)
          this.timer = setTimeout(() => { void this.retry(); }, Math.min(30_000, 2000 * 2 ** this.failures));
        return false;
      } finally {
        if (this.active && !this.disposed && epoch === this.epoch) this.publish({ busy: this.reading.size > 0 });
      }
    })();
    this.running = operation;
    return operation.finally(() => { if (this.running === operation) this.running = null; });
  };
  retry = async () => {
    if (!this.active || this.disposed || this.state.status === 'conflict' || this.state.recoveryBlocked) return false;
    if (!this.state.loaded) return !!await this.load();
    this.publish({ status: this.state.dirty ? 'unsaved' : 'saved', message: '' });
    return this.flush();
  };
  /** Explicit choice in the version panel, with a backup before replacing input. */
  reload = async (keepLocal = false) => {
    if (!this.active || this.disposed) return null;
    if (this.running) await this.running;
    this.preserveBackup();
    const epoch = this.epoch;
    const remote = await this.options.read();
    if (!this.active || this.disposed || epoch !== this.epoch) return null;
    this.preserveBackup();
    const document = keepLocal && this.state.value ? this.options.document(this.state.value) : this.options.document(remote);
    const unresolved = !!this.pending && this.options.version(remote) === this.pending.base_version;
    this.acknowledged = remote; if (!unresolved) this.pending = undefined; this.recoveryBase = null;
    const dirty = unresolved || !equal(document, this.options.document(remote));
    this.publish({ value: this.options.apply(remote, document), loaded: true, dirty,
      status: dirty ? 'unsaved' : 'saved', message: '', recovered: null, recoveryBlocked: false });
    this.persist(); this.schedule(); return remote;
  };
  private backup: string | null = null;
  private preserveBackup() {
    if (!this.state.dirty && !this.state.recoveryBlocked && !this.pending) return;
    const previous = this.options.recovery.export?.() ?? null;
    const backup = JSON.stringify({ format: 1, base_version: this.recoveryBase ?? (this.acknowledged ? this.options.version(this.acknowledged) : '0'),
      document: this.state.value ? this.options.document(this.state.value) : null, previous_recovery: previous, pending: this.pending });
    if (backup === this.backup) return;
    if (!this.options.recovery.backup) throw new Error('无法备份本机输入，已保留当前内容。请先复制或下载。');
    this.options.recovery.backup(backup);
    this.backup = backup;
  }
  exportRecovered = () => this.options.recovery.export?.() ?? this.options.recovery.previous?.() ?? this.backup;
  hasPrevious = () => { try { return !!this.options.recovery.previous?.(); } catch { return false; } };
  accept = (resource: Resource) => {
    if (this.state.dirty || this.running || this.pending || this.state.recoveryBlocked
      || (this.acknowledged && BigInt(this.options.version(resource)) < BigInt(this.options.version(this.acknowledged)))) return false;
    this.acknowledged = resource; this.recoveryBase = null; this.publish({ value: resource, loaded: true, status: 'saved', message: '' }); this.persist(); return true;
  };
  updateMetadata = (transform: (resource: Resource) => Resource) => {
    if (this.state.value) this.publish({ value: transform(this.state.value) });
  };
  suspend() { this.active = false; ++this.epoch; this.reading.clear(); clearTimeout(this.timer); this.publish({ busy: false }); }
  resume() {
    if (this.disposed || this.active) return;
    this.active = true; this.running = null; this.loading = null;
    if (this.pending) void this.retry(); else this.schedule();
  }
  dispose() { this.disposed = true; this.active = false; ++this.epoch; clearTimeout(this.timer); this.listeners.clear(); }
}
