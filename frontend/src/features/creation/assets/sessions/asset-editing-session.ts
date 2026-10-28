import type { AssetDraft, AssetRead, LibraryAssetRead } from '@host/types/creation/assets/management';
import type {
  AssetEditingSnapshot,
  AssetRecovery,
  AssetRecoveryStorage,
} from '@host/types/creation/assets/sessions/asset-editing-session';
export const assetFields = (asset: AssetDraft): AssetDraft => ({
  kind: asset.kind,
  name: asset.name,
  label: asset.label,
  description: asset.description,
  prompt: asset.prompt,
  tags: [...asset.tags],
  scene_time: asset.scene_time,
});
const same = (left: AssetDraft, right: AssetDraft) =>
  JSON.stringify(assetFields(left)) === JSON.stringify(assetFields(right));
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
function validFields(value: unknown): value is AssetDraft {
  return (
    record(value) &&
    typeof value.kind === 'string' &&
    ['character', 'scene', 'prop'].includes(value.kind) &&
    ['name', 'label', 'description', 'prompt', 'scene_time'].every((key) => typeof value[key] === 'string') &&
    Array.isArray(value.tags) &&
    value.tags.every((tag) => typeof tag === 'string')
  );
}
function validRecovery(value: unknown): value is AssetRecovery {
  if (!record(value) || !record(value.base) || !record(value.fields)) return false;
  const { base, fields } = value;
  return (
    typeof base.id === 'string' &&
    /^\d+$/.test(base.id) &&
    typeof base.row_version === 'string' &&
    /^\d+$/.test(base.row_version) &&
    typeof fields.kind === 'string' &&
    ['character', 'scene', 'prop'].includes(fields.kind) &&
    ['name', 'label', 'description', 'prompt', 'scene_time'].every((key) => typeof fields[key] === 'string') &&
    Array.isArray(fields.tags) &&
    fields.tags.every((tag) => typeof tag === 'string') &&
    (value.pending == null || validFields(value.pending))
  );
}

/** Owns the shared artwork fields; private generation input has a separate session. */
export class AssetEditingSession {
  private snapshot: AssetEditingSnapshot;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pending: Promise<LibraryAssetRead | null> | null = null;
  private disposed = false;
  private active = true;
  private epoch = 0;
  private persistenceFailed = false;
  private submitted: AssetDraft | null = null;
  private changeNo = 0;

  constructor(
    asset: LibraryAssetRead,
    private update: (id: string, body: AssetDraft & { row_version: string }) => Promise<AssetRead>,
    private recovery?: AssetRecoveryStorage,
    private pauseMs = 1000,
    private read?: (id: string) => Promise<AssetRead>,
  ) {
    let recovered: AssetRecovery | null = null;
    let message = '';
    let recoveryBlocked = false;
    try {
      const value = recovery?.read() ?? null;
      if (value !== null && (!validRecovery(value) || value.base.id !== asset.id))
        throw new Error('Invalid asset recovery');
      recovered = value;
    } catch {
      recoveryBlocked = true;
      message = '本机恢复记录无法读取。原记录已保留，请下载备份后明确清除。';
    }
    const fields = recovered?.base.id === asset.id ? recovered.fields : null;
    const current = fields ? { ...asset, ...fields } : asset;
    const accepted =
      !!recovered?.pending &&
      same(asset, recovered.pending) &&
      asset.row_version === String(BigInt(recovered.base.row_version) + 1n);
    this.submitted = accepted ? null : (recovered?.pending ?? null);
    const dirty = !same(current, asset) || !!this.submitted;
    const changed =
      !!fields &&
      (!same(current, asset) || !!this.submitted) &&
      !accepted &&
      recovered?.base.row_version !== asset.row_version;
    this.snapshot = {
      asset: current,
      saved: asset,
      dirty,
      recoveryBlocked,
      status: changed ? 'conflict' : dirty ? 'dirty' : message ? 'error' : 'saved',
      message: changed ? '服务端素材已变化，本机草稿已保留。请核对最新版本。' : message,
    };
    if (dirty && !changed && !recoveryBlocked)
      this.timer = setTimeout(() => {
        void this.flush();
      }, this.pauseMs);
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<AssetEditingSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  private persist() {
    if (this.snapshot.recoveryBlocked) return;
    try {
      if (this.snapshot.dirty || this.submitted)
        this.recovery?.save({
          base: this.snapshot.saved,
          fields: assetFields(this.snapshot.asset),
          pending: this.submitted,
        });
      else this.recovery?.clear();
      this.persistenceFailed = false;
    } catch {
      this.persistenceFailed = true;
      this.publish({
        message: '浏览器未能保留恢复稿，请保持本页打开或下载素材草稿。',
      });
    }
  }
  edit(patch: Partial<AssetDraft>) {
    if (this.disposed || !this.active) return;
    const asset = { ...this.snapshot.asset, ...patch };
    const dirty = !same(asset, this.snapshot.saved) || !!this.submitted;
    this.changeNo += 1;
    const paused = ['conflict', 'error'].includes(this.snapshot.status);
    this.publish({
      asset,
      dirty,
      status: paused ? this.snapshot.status : dirty ? 'dirty' : 'saved',
      message: paused ? this.snapshot.message : '',
    });
    this.persist();
    clearTimeout(this.timer);
    if (!paused && dirty)
      this.timer = setTimeout(() => {
        void this.flush();
      }, this.pauseMs);
  }
  hasUnsettled = () => this.snapshot.dirty || !!this.pending || ['error', 'conflict'].includes(this.snapshot.status);
  hasMemoryOnlyChanges = () =>
    (this.snapshot.dirty || !!this.submitted) &&
    (!this.recovery || this.persistenceFailed || this.snapshot.recoveryBlocked);
  hasUnpersisted = this.hasMemoryOnlyChanges;

  flush = (): Promise<LibraryAssetRead | null> => {
    clearTimeout(this.timer);
    if (this.pending) return this.pending;
    if (
      this.disposed ||
      !this.active ||
      this.snapshot.recoveryBlocked ||
      ['conflict', 'error'].includes(this.snapshot.status)
    )
      return Promise.resolve(null);
    if (!this.snapshot.dirty) return Promise.resolve(this.snapshot.saved);
    const pending = this.saveQueue();
    this.pending = pending;
    void pending.finally(() => {
      if (this.pending === pending) this.pending = null;
    });
    return pending;
  };

  private async saveQueue(): Promise<LibraryAssetRead | null> {
    const epoch = this.epoch;
    while (this.snapshot.dirty && !this.disposed && this.active && epoch === this.epoch) {
      const submitted = this.snapshot.asset;
      if (!submitted.name.trim()) {
        this.publish({
          status: 'error',
          message: '请填写素材名称，草稿已保留。',
        });
        return null;
      }
      const submittedFields = assetFields(submitted);
      const sequence = this.changeNo;
      const baseVersion = this.snapshot.saved.row_version;
      this.submitted = { ...submittedFields, name: submitted.name.trim() };
      this.persist();
      this.publish({ status: 'saving', message: '' });
      try {
        const next = await this.update(submitted.id, {
          ...submittedFields,
          name: submitted.name.trim(),
          row_version: this.snapshot.saved.row_version,
        });
        if (this.disposed || !this.active || epoch !== this.epoch) return null;
        const saved = { ...this.snapshot.saved, ...next };
        // An acknowledgement updates the base version, but retains newer keystrokes.
        const asset = sequence === this.changeNo ? saved : { ...saved, ...assetFields(this.snapshot.asset) };
        const dirty = !same(asset, saved);
        this.publish({
          saved,
          asset,
          dirty,
          status: dirty ? 'dirty' : 'saved',
        });
        this.submitted = null;
        this.persist();
      } catch (cause) {
        if (this.disposed || !this.active || epoch !== this.epoch) return null;
        const error = cause as {
          code?: string;
          status?: number;
          message?: string;
        };
        if (this.read && (!error.status || error.status >= 500 || error.status === 408)) {
          try {
            const latest = await this.read(submitted.id);
            if (this.disposed || !this.active || epoch !== this.epoch) return null;
            if (latest.row_version === String(BigInt(baseVersion) + 1n) && same(latest, this.submitted!)) {
              const saved = { ...this.snapshot.saved, ...latest };
              const asset = sequence === this.changeNo ? saved : { ...saved, ...assetFields(this.snapshot.asset) };
              const dirty = !same(asset, saved);
              this.submitted = null;
              this.publish({
                saved,
                asset,
                dirty,
                status: dirty ? 'dirty' : 'saved',
              });
              this.persist();
              continue;
            }
            if (latest.row_version !== baseVersion) {
              this.conflict();
              return null;
            }
          } catch {
            /* Retain the submitted snapshot until the next safe read. */
          }
        }
        const status = error.code === 'asset_version_conflict' || error.status === 409 ? 'conflict' : 'error';
        this.publish({
          status,
          message:
            status === 'conflict'
              ? '素材已在其他页面修改，草稿已保留。请核对最新版本。'
              : `素材保存失败：${error.message || '无法连接服务端'}，草稿已保留。`,
        });
        this.persist();
        return null;
      }
    }
    return this.disposed || !this.active || epoch !== this.epoch ? null : this.snapshot.saved;
  }

  retry = async () => {
    if (!this.active || this.disposed || this.snapshot.status !== 'error' || this.snapshot.recoveryBlocked) return null;
    if (this.read) {
      const epoch = this.epoch;
      try {
        const latest = await this.read(this.snapshot.asset.id);
        if (!this.active || epoch !== this.epoch) return null;
        const accepted =
          this.submitted &&
          latest.row_version === String(BigInt(this.snapshot.saved.row_version) + 1n) &&
          same(latest, this.submitted);
        if (accepted) {
          this.submitted = null;
          this.reconcile(latest, true);
        } else if (latest.row_version !== this.snapshot.saved.row_version) {
          this.conflict();
          return null;
        }
      } catch {
        return null;
      }
    }
    this.publish({
      status: this.snapshot.dirty ? 'dirty' : 'saved',
      message: '',
    });
    return this.flush();
  };
  reconcile(latest: AssetRead, keepDraft: boolean) {
    if (latest.id !== this.snapshot.asset.id || this.pending || this.snapshot.recoveryBlocked) return;
    if (!keepDraft && this.snapshot.dirty) {
      try {
        this.recovery?.backup?.({
          base: this.snapshot.saved,
          fields: assetFields(this.snapshot.asset),
          pending: this.submitted,
        });
      } catch {
        this.publish({
          message: '无法保留替换前的素材副本，当前输入仍保留。请先下载后重试。',
        });
        return;
      }
    }
    const unresolved = !!this.submitted && latest.row_version === this.snapshot.saved.row_version;
    const saved = { ...this.snapshot.saved, ...latest };
    const asset = keepDraft ? { ...saved, ...assetFields(this.snapshot.asset) } : saved;
    if (!keepDraft && !unresolved) this.submitted = null;
    this.changeNo += 1;
    const dirty = !same(asset, saved) || !!this.submitted;
    this.publish({
      saved,
      asset,
      dirty,
      status: dirty ? 'dirty' : 'saved',
      message: '',
    });
    this.persist();
    clearTimeout(this.timer);
    if (this.snapshot.dirty && !this.disposed)
      this.timer = setTimeout(() => {
        void this.flush();
      }, this.pauseMs);
  }
  discardRecovery() {
    if (!this.snapshot.recoveryBlocked || this.disposed) return false;
    try {
      this.recovery?.clear();
    } catch {
      this.publish({
        message: '无法清除本机素材恢复记录，请下载备份并核对浏览器存储。',
      });
      return false;
    }
    this.publish({
      recoveryBlocked: false,
      status: this.snapshot.dirty ? 'dirty' : 'saved',
      message: '',
    });
    this.persist();
    if (this.snapshot.dirty)
      this.timer = setTimeout(() => {
        void this.flush();
      }, this.pauseMs);
    return true;
  }
  /** Media adoption changes the base only after the explicit transaction succeeds. */
  accept(latest: AssetRead) {
    this.reconcile(latest, true);
  }
  observe(latest: AssetRead) {
    if (
      latest.id !== this.snapshot.asset.id ||
      BigInt(latest.row_version) <= BigInt(this.snapshot.saved.row_version) ||
      this.pending
    )
      return;
    if (this.hasUnsettled()) this.conflict();
    else this.reconcile(latest, false);
  }
  conflict(message = '素材已在其他页面修改，草稿已保留。请核对最新版本。') {
    clearTimeout(this.timer);
    this.publish({ status: 'conflict', message });
    this.persist();
  }
  suspend() {
    this.active = false;
    ++this.epoch;
    clearTimeout(this.timer);
  }
  resume() {
    if (this.active || this.disposed) return;
    this.active = true;
    const resume = () => {
      if (['conflict'].includes(this.snapshot.status) || this.snapshot.recoveryBlocked) return;
      if (this.submitted) this.publish({ status: 'error' });
      if (this.snapshot.status === 'error') void this.retry();
      else void this.flush();
    };
    if (this.pending) void this.pending.finally(resume);
    else resume();
  }
  dispose() {
    this.disposed = true;
    this.suspend();
    this.listeners.clear();
  }
}

export function assetRecoveryKey(actorId: string, projectId: string, assetId: string) {
  return `short-drama:asset-editor:v2:${actorId}:${projectId}:${assetId}`;
}

export function assetRecoveryStorage(key: string, legacyKey?: string): AssetRecoveryStorage {
  return {
    read() {
      const value = localStorage.getItem(key) ?? (legacyKey ? localStorage.getItem(legacyKey) : null);
      return value ? (JSON.parse(value) as AssetRecovery) : null;
    },
    save(value) {
      localStorage.setItem(key, JSON.stringify(value));
      if (legacyKey) localStorage.removeItem(legacyKey);
    },
    clear() {
      localStorage.removeItem(key);
      if (legacyKey) localStorage.removeItem(legacyKey);
    },
    backup(value) {
      localStorage.setItem(`${key}:previous`, JSON.stringify(value));
    },
  };
}
