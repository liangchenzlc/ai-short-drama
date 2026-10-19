import type { CreationDraftTransport, CreationDraftValue, UserCreationDraft } from '../../api/modules/creation-drafts';

export type CreationSaveStatus = 'idle' | 'loading' | 'saved' | 'local' | 'unsaved' | 'saving' | 'error' | 'conflict';
export interface CreationDraftSnapshot {
  value: CreationDraftValue; loaded: boolean; status: CreationSaveStatus; message: string;
  saved: UserCreationDraft | null;
}
export interface DraftRecovery {
  row_version: string | null; value: CreationDraftValue;
  pending?: { version: string | null; value: CreationDraftValue } | null;
}
export interface DraftPersistence {
  read(): DraftRecovery | null; save(value: DraftRecovery): void; clear(): void;
  backup?(value: DraftRecovery): void;
}
const equal = (a: CreationDraftValue, b: CreationDraftValue) => JSON.stringify(a) === JSON.stringify(b);
export function draftValue(value: CreationDraftValue): CreationDraftValue {
  return { model_config_id: value.model_config_id ?? null, input_text: value.input_text,
    image_settings: value.image_settings ?? null, video_settings: value.video_settings ?? null,
    reference_media_ids: [...new Set(value.reference_media_ids ?? [])] };
}

/** One queue per account/object/purpose; acknowledgements never replace newer input. */
export class CreationDraftSession {
  private state: CreationDraftSnapshot;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private operation: Promise<UserCreationDraft | null> | null = null;
  private pending: DraftRecovery['pending'] = null;
  private epoch = 0;
  private dirty = false;
  private disposed = false;
  private active = true;
  private persistenceFailed = false;
  private readonly api: CreationDraftTransport;
  private readonly persistence: DraftPersistence;
  constructor(api: CreationDraftTransport, persistence: DraftPersistence, initial: CreationDraftValue) {
    this.api = api; this.persistence = persistence;
    this.state = { value: draftValue(initial), loaded: false, status: 'loading', message: '', saved: null };
  }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private publish(patch: Partial<CreationDraftSnapshot>) {
    this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener());
  }
  hasUnsettled = () => this.dirty || this.operation !== null || this.state.status === 'conflict' || this.state.status === 'error';
  hasMemoryOnlyChanges = () => (this.dirty || !!this.pending) && this.persistenceFailed;
  hasUnpersisted = this.hasMemoryOnlyChanges;
  private preserve() {
    try { this.persistence.save({ row_version: this.state.saved?.row_version ?? null, value: this.state.value, pending: this.pending }); this.persistenceFailed = false; }
    catch { this.persistenceFailed = true; this.publish({ message: '本机无法保留生成设置，正在尝试同步；可在保存详情中复制输入。' }); return false; }
    return true;
  }
  private clearAcceptedRecovery() {
    this.persistenceFailed = false;
    try { this.persistence.clear(); } catch { /* A confirmed server receipt remains saved; the next load can remove the identical local copy. */ }
  }
  async load(discard = false) {
    if (!this.active || this.disposed || !discard && (this.state.loaded || this.state.status === 'loading' && this.epoch > 0)) return;
    const epoch = ++this.epoch;
    clearTimeout(this.timer);
    this.publish({ loaded: false, status: 'loading', message: '' });
    try {
      const saved = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return;
      const recovery = discard ? null : this.persistence.read();
      if (discard) {
        let pending = this.pending;
        if (!pending) {
          try { pending = this.persistence.read()?.pending ?? null; }
          catch { /* The backup provider preserves the original raw record before replacement. */ }
        }
        this.persistence.backup?.({ row_version: this.state.saved?.row_version ?? null, value: this.state.value, pending });
        const unresolved = !!pending && pending.version === (saved?.row_version ?? null);
        this.pending = unresolved ? pending : null;
        this.dirty = unresolved;
        if (!unresolved) this.persistence.clear();
        this.publish({ loaded: true, saved, value: saved ? draftValue(saved) : this.state.value,
          status: unresolved ? 'unsaved' : saved ? 'saved' : 'idle' });
        if (unresolved) { this.preserve(); this.timer = setTimeout(() => { void this.flush(); }, 1000); }
        return;
      }
      this.pending = recovery?.pending ?? null;
      this.dirty = !!recovery;
      if (recovery) {
        const accepted = this.pending && saved && equal(draftValue(saved), this.pending.value)
          && (this.pending.version === null || saved.row_version === String(BigInt(this.pending.version) + 1n));
        const identical = (!this.pending || accepted) && !!saved && equal(draftValue(saved), recovery.value);
        const conflict = !accepted && !identical && recovery.row_version !== (saved?.row_version ?? null);
        if (accepted) this.pending = null;
        this.dirty = !identical;
        this.publish({ loaded: true, saved, value: draftValue(recovery.value), status: conflict ? 'conflict' : identical ? 'saved' : 'unsaved',
          message: conflict ? '生成设置已在其他窗口修改，本机输入已保留。请核对后载入服务端版本。' : '' });
        if (identical) { this.pending = null; this.clearAcceptedRecovery(); }
        else if (!conflict) { this.preserve(); this.timer = setTimeout(() => { void this.flush(); }, 1000); }
      } else {
        this.publish({ loaded: true, saved, value: saved ? draftValue(saved) : this.state.value, status: saved ? 'saved' : 'idle' });
      }
    } catch (cause) {
      if (!this.disposed && epoch === this.epoch) this.publish({ status: 'error', message: cause instanceof Error ? cause.message : '生成设置载入失败，请重试。' });
    }
  }
  edit = (patch: Partial<CreationDraftValue>) => {
    if (this.disposed || !this.active || !this.state.loaded) return;
    const value = draftValue({ ...this.state.value, ...patch });
    if (equal(value, this.state.value)) return;
    this.dirty = true;
    const paused = this.state.status === 'conflict' || this.state.status === 'error';
    this.publish({ value, ...(!paused ? { status: 'unsaved' as const, message: '' } : {}) });
    clearTimeout(this.timer);
    this.preserve();
    if (!paused) this.timer = setTimeout(() => { void this.flush(); }, 1000);
  };
  flush = async (): Promise<UserCreationDraft | null> => {
    clearTimeout(this.timer);
    if (this.disposed || !this.active || !this.state.loaded || this.state.status === 'error' || this.state.status === 'conflict') return null;
    if (this.operation) {
      const epoch = this.epoch, operation = this.operation;
      const result = await operation;
      if (this.operation === operation) this.operation = null;
      if (this.disposed || !this.active || epoch !== this.epoch) return null;
      return this.dirty ? this.flush() : result;
    }
    if (!this.dirty && this.state.saved) { this.publish({ status: 'saved', message: '' }); return this.state.saved; }
    const epoch = this.epoch;
    const value = draftValue(this.state.value);
    let version = this.state.saved?.row_version ?? null;
    const unresolvedCreate = this.pending?.version === null && version === null;
    if (!unresolvedCreate) this.pending = { version, value };
    this.preserve();
    this.dirty = false;
    this.publish({ status: 'saving', message: '' });
    const operation = (async () => {
      try {
        if (unresolvedCreate) {
          const remote = await this.api.get();
          if (this.disposed || !this.active || epoch !== this.epoch) return null;
          // Scope uniqueness makes an existing record a fence for the earlier create.
          if (remote) { version = remote.row_version; this.publish({ saved: remote }); }
          this.pending = { version, value }; this.preserve();
        }
        const saved = version ? await this.api.update(version, value) : await this.api.create(value);
        if (this.disposed || !this.active || epoch !== this.epoch) return null;
        this.pending = null;
        this.publish({ saved, status: this.dirty ? 'unsaved' : 'saved' });
        if (this.dirty) this.preserve();
        else this.clearAcceptedRecovery();
        return saved;
      } catch (cause) {
        if (this.disposed || !this.active || epoch !== this.epoch) return null;
        this.dirty = true;
        const error = cause as { status?: number; message?: string };
        if (!error.status || error.status >= 500 || error.status === 408) {
          try {
            const remote = await this.api.get();
            if (this.disposed || !this.active || epoch !== this.epoch) return null;
            if (remote && equal(draftValue(remote), value) && (version === null || remote.row_version === String(BigInt(version) + 1n))) {
              this.pending = null; this.dirty = !equal(this.state.value, draftValue(remote));
              this.publish({ saved: remote, status: this.dirty ? 'unsaved' : 'saved', message: '' });
              if (this.dirty) this.preserve(); else this.clearAcceptedRecovery();
              return remote;
            }
            if ((remote?.row_version ?? null) !== version) {
              this.publish({ status: 'conflict', message: '服务端生成设置已变化，本机输入已保留，请核对版本。' }); this.preserve(); return null;
            }
          } catch { /* Keep the original submitted snapshot for a later read-only reconciliation. */ }
        }
        this.publish({ status: error.status === 409 ? 'conflict' : 'error',
          message: error.status === 409 ? '生成设置版本冲突，本机输入已保留，请载入服务端版本后核对。' : error.message ?? '生成设置保存失败，本机输入已保留。' });
        this.preserve(); return null;
      }
    })();
    this.operation = operation;
    const saved = await operation;
    if (this.operation === operation) this.operation = null;
    return saved && this.dirty ? this.flush() : saved;
  };
  retry = async () => {
    if (this.state.status === 'conflict' || this.disposed || !this.active) return null;
    if (!this.state.loaded) { await this.load(); return this.state.saved; }
    const epoch = this.epoch;
    try {
      const remote = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return null;
      if (this.pending && remote && equal(draftValue(remote), this.pending.value)
        && (this.pending.version === null || remote.row_version === String(BigInt(this.pending.version) + 1n))) {
        this.pending = null;
        this.publish({ saved: remote, status: 'unsaved', message: '' });
        this.dirty = !equal(this.state.value, draftValue(remote));
        if (!this.dirty) { this.publish({ status: 'saved' }); this.clearAcceptedRecovery(); }
      } else if ((remote?.row_version ?? null) !== (this.state.saved?.row_version ?? null)) {
        this.publish({ status: 'conflict', message: '服务端生成设置已变化，本机输入已保留。请核对后载入服务端版本。' }); return null;
      } else { this.dirty = !!this.pending || !remote || !equal(this.state.value, draftValue(remote)); this.publish({ status: 'unsaved', loaded: true, message: '' }); }
      return await this.flush();
    } catch (cause) { if (!this.disposed && epoch === this.epoch) this.publish({ status: 'error', message: cause instanceof Error ? cause.message : '生成草稿核对失败。' }); return null; }
  };
  suspend() { this.active = false; ++this.epoch; clearTimeout(this.timer); }
  resume() {
    if (this.active || this.disposed) return;
    this.active = true;
    const resume = () => { if (!this.active) return; if (!this.state.loaded) { this.publish({ status: 'error' }); void this.load(); } else if (this.state.status !== 'conflict') void this.retry(); };
    if (this.operation) void this.operation.finally(resume); else resume();
  }
  dispose() { this.disposed = true; this.suspend(); this.listeners.clear(); }
}
