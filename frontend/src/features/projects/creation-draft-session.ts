import type { CreationDraftTransport, CreationDraftValue, UserCreationDraft } from '../../api/modules/creation-drafts';

export type CreationSaveStatus = 'loading' | 'saved' | 'local' | 'unsaved' | 'saving' | 'error' | 'conflict';
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
  private preserve() {
    try { this.persistence.save({ row_version: this.state.saved?.row_version ?? null, value: this.state.value, pending: this.pending }); }
    catch { this.publish({ status: 'error', message: '无法保留本机生成草稿，请检查浏览器存储后重试。' }); return false; }
    return true;
  }
  async load(discard = false) {
    const epoch = ++this.epoch;
    clearTimeout(this.timer);
    this.publish({ loaded: false, status: 'loading', message: '' });
    try {
      const saved = await this.api.get();
      if (this.disposed || epoch !== this.epoch) return;
      const recovery = discard ? null : this.persistence.read();
      if (discard) this.persistence.clear();
      this.pending = recovery?.pending ?? null;
      this.dirty = !!recovery;
      if (recovery) {
        const accepted = this.pending && saved && equal(draftValue(saved), this.pending.value);
        const conflict = !accepted && recovery.row_version !== (saved?.row_version ?? null);
        if (accepted) this.pending = null;
        this.publish({ loaded: true, saved, value: draftValue(recovery.value), status: conflict ? 'conflict' : 'local',
          message: conflict ? '生成设置已在其他窗口修改，本机输入已保留。请核对后载入服务端版本。' : '本机生成草稿已保留，尚未保存到服务端。' });
      } else {
        this.publish({ loaded: true, saved, value: saved ? draftValue(saved) : this.state.value, status: saved ? 'saved' : 'local' });
      }
    } catch (cause) {
      if (!this.disposed && epoch === this.epoch) this.publish({ status: 'error', message: cause instanceof Error ? cause.message : '生成设置载入失败，请重试。' });
    }
  }
  edit = (patch: Partial<CreationDraftValue>) => {
    if (this.disposed || !this.state.loaded) return;
    const value = draftValue({ ...this.state.value, ...patch });
    if (equal(value, this.state.value)) return;
    this.dirty = true;
    const paused = this.state.status === 'conflict' || this.state.status === 'error';
    this.publish({ value, ...(!paused ? { status: 'unsaved' as const, message: '' } : {}) });
    clearTimeout(this.timer);
    if (this.preserve() && !paused) this.timer = setTimeout(() => { void this.flush(); }, 1000);
  };
  flush = async (): Promise<UserCreationDraft | null> => {
    clearTimeout(this.timer);
    if (this.disposed || !this.state.loaded || this.state.status === 'error' || this.state.status === 'conflict') return null;
    if (this.operation) { const result = await this.operation; return result && this.dirty ? this.flush() : result; }
    if (!this.dirty && this.state.saved) return this.state.saved;
    const epoch = this.epoch;
    const value = draftValue(this.state.value);
    const version = this.state.saved?.row_version ?? null;
    this.pending = { version, value };
    if (!this.preserve()) return null;
    this.dirty = false;
    this.publish({ status: 'saving', message: '' });
    const operation = (async () => {
      try {
        const saved = version ? await this.api.update(version, value) : await this.api.create(value);
        if (this.disposed || epoch !== this.epoch) return null;
        this.pending = null;
        this.publish({ saved, status: this.dirty ? 'unsaved' : 'saved' });
        if (this.dirty) { if (!this.preserve()) return null; }
        else this.persistence.clear();
        return saved;
      } catch (cause) {
        if (this.disposed || epoch !== this.epoch) return null;
        this.dirty = true;
        const error = cause as { status?: number; message?: string };
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
    if (this.state.status === 'conflict' || this.disposed) return null;
    if (!this.state.loaded) { await this.load(); return this.state.saved; }
    try {
      const remote = await this.api.get();
      if (this.disposed) return null;
      if (this.pending && remote && equal(draftValue(remote), this.pending.value)) {
        this.pending = null;
        this.publish({ saved: remote, status: 'unsaved', message: '' });
        this.dirty = !equal(this.state.value, draftValue(remote));
        if (!this.dirty) { this.publish({ status: 'saved' }); this.persistence.clear(); }
      } else if ((remote?.row_version ?? null) !== (this.state.saved?.row_version ?? null)) {
        this.publish({ status: 'conflict', message: '服务端生成设置已变化，本机输入已保留。请核对后载入服务端版本。' }); return null;
      } else this.publish({ status: 'unsaved', loaded: true, message: '' });
      return await this.flush();
    } catch (cause) { this.publish({ status: 'error', message: cause instanceof Error ? cause.message : '生成草稿核对失败。' }); return null; }
  };
  dispose() { this.disposed = true; ++this.epoch; clearTimeout(this.timer); this.listeners.clear(); }
}
