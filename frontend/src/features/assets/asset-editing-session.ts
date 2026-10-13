import type { AssetDraft, AssetRead, LibraryAssetRead } from '../../api/modules/assets';

export type AssetSaveStatus = 'saved' | 'dirty' | 'saving' | 'error' | 'conflict' | 'shared';
export interface AssetRecovery { base: LibraryAssetRead; fields: AssetDraft }
export interface AssetRecoveryStorage { read: () => AssetRecovery | null; save: (value: AssetRecovery) => void; clear: () => void }
export interface AssetEditingSnapshot {
  asset: LibraryAssetRead;
  saved: LibraryAssetRead;
  status: AssetSaveStatus;
  message: string;
  dirty: boolean;
  recoveryBlocked: boolean;
}
export const assetFields = (asset: AssetDraft): AssetDraft => ({
  kind: asset.kind, name: asset.name, label: asset.label, description: asset.description,
  prompt: asset.prompt, tags: [...asset.tags], scene_time: asset.scene_time,
});
const same = (left: AssetDraft, right: AssetDraft) => JSON.stringify(assetFields(left)) === JSON.stringify(assetFields(right));
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function validRecovery(value: unknown): value is AssetRecovery {
  if (!record(value) || !record(value.base) || !record(value.fields)) return false;
  const { base, fields } = value;
  return typeof base.id === 'string' && /^\d+$/.test(base.id)
    && typeof base.row_version === 'string' && /^\d+$/.test(base.row_version)
    && typeof fields.kind === 'string' && ['character', 'scene', 'prop'].includes(fields.kind)
    && ['name', 'label', 'description', 'prompt', 'scene_time'].every(key => typeof fields[key] === 'string')
    && Array.isArray(fields.tags) && fields.tags.every(tag => typeof tag === 'string');
}

/** Owns the shared artwork fields; private generation input has a separate session. */
export class AssetEditingSession {
  private snapshot: AssetEditingSnapshot;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pending: Promise<LibraryAssetRead | null> | null = null;
  private disposed = false;
  private changeNo = 0;
  private approvedFields: AssetDraft | null = null;

  constructor(
    asset: LibraryAssetRead,
    private update: (id: string, body: AssetDraft & { row_version: string; confirm_shared: boolean }) => Promise<AssetRead>,
    private recovery?: AssetRecoveryStorage,
    private pauseMs = 1000,
  ) {
    let recovered: AssetRecovery | null = null;
    let message = '';
    let recoveryBlocked = false;
    try {
      const value = recovery?.read() ?? null;
      if (value !== null && (!validRecovery(value) || value.base.id !== asset.id)) throw new Error('Invalid asset recovery');
      recovered = value;
    } catch { recoveryBlocked = true; message = '本机恢复记录无法读取。原记录已保留，请下载备份后明确清除。'; }
    const fields = recovered?.base.id === asset.id ? recovered.fields : null;
    const current = fields ? { ...asset, ...fields } : asset;
    const dirty = !same(current, asset);
    const changed = !!fields && recovered?.base.row_version !== asset.row_version;
    this.snapshot = {
      asset: current, saved: asset, dirty, recoveryBlocked,
      status: changed ? 'conflict' : dirty ? 'dirty' : message ? 'error' : 'saved',
      message: changed ? '服务端素材已变化，本机草稿已保留。请核对最新版本。' : message,
    };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(patch: Partial<AssetEditingSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach(listener => listener());
  }
  private persist() {
    if (this.snapshot.recoveryBlocked) return;
    try {
      if (this.snapshot.dirty) this.recovery?.save({ base: this.snapshot.saved, fields: assetFields(this.snapshot.asset) });
      else this.recovery?.clear();
    } catch {
      this.publish({ message: '浏览器未能保留恢复稿，请保持本页打开或下载素材草稿。' });
    }
  }
  edit(patch: Partial<AssetDraft>) {
    if (this.disposed) return;
    const asset = { ...this.snapshot.asset, ...patch };
    const dirty = !same(asset, this.snapshot.saved);
    this.changeNo += 1;
    this.approvedFields = null;
    const paused = ['conflict', 'shared', 'error'].includes(this.snapshot.status);
    this.publish({ asset, dirty, status: paused ? this.snapshot.status : dirty ? 'dirty' : 'saved', message: paused ? this.snapshot.message : '' });
    this.persist();
    clearTimeout(this.timer);
    if (!paused && dirty) this.timer = setTimeout(() => { void this.flush(); }, this.pauseMs);
  }
  hasUnsettled = () => this.snapshot.dirty || !!this.pending || ['error', 'conflict', 'shared'].includes(this.snapshot.status);

  flush = (): Promise<LibraryAssetRead | null> => {
    clearTimeout(this.timer);
    if (this.pending) return this.pending;
    if (this.disposed || this.snapshot.recoveryBlocked || ['conflict', 'shared', 'error'].includes(this.snapshot.status)) return Promise.resolve(null);
    if (!this.snapshot.dirty) return Promise.resolve(this.snapshot.saved);
    const pending = this.saveQueue();
    this.pending = pending;
    void pending.finally(() => { if (this.pending === pending) this.pending = null; });
    return pending;
  };

  private async saveQueue(): Promise<LibraryAssetRead | null> {
    while (this.snapshot.dirty && !this.disposed) {
      const submitted = this.snapshot.asset;
      if (!submitted.name.trim()) {
        this.publish({ status: 'error', message: '请填写素材名称，草稿已保留。' }); return null;
      }
      const submittedFields = assetFields(submitted);
      const sequence = this.changeNo;
      const shared = this.approvedFields !== null && same(this.approvedFields, submittedFields);
      this.publish({ status: 'saving', message: '' });
      try {
        const next = await this.update(submitted.id, {
          ...submittedFields, name: submitted.name.trim(),
          row_version: this.snapshot.saved.row_version, confirm_shared: shared,
        });
        if (this.disposed) return null;
        const saved = { ...this.snapshot.saved, ...next };
        // An acknowledgement updates the base version, but retains newer keystrokes.
        const asset = sequence === this.changeNo ? saved : { ...saved, ...assetFields(this.snapshot.asset) };
        const dirty = !same(asset, saved);
        this.publish({ saved, asset, dirty, status: dirty ? 'dirty' : 'saved' });
        this.approvedFields = null;
        this.persist();
      } catch (cause) {
        if (this.disposed) return null;
        const error = cause as { code?: string; status?: number; message?: string };
        const status = error.code === 'shared_asset_confirmation_required' ? 'shared'
          : error.code === 'asset_version_conflict' || error.status === 409 ? 'conflict' : 'error';
        this.publish({ status, message: status === 'shared' ? '保存会同步影响此素材的其他引用。请确认本次修改。'
          : status === 'conflict' ? '素材已在其他页面修改，草稿已保留。请核对最新版本。'
            : `素材保存失败：${error.message || '无法连接服务端'}，草稿已保留。` });
        this.persist();
        return null;
      }
    }
    return this.disposed ? null : this.snapshot.saved;
  }

  retry = async () => {
    if (this.snapshot.status !== 'error' || this.snapshot.recoveryBlocked) return null;
    this.publish({ status: this.snapshot.dirty ? 'dirty' : 'saved', message: '' });
    return this.flush();
  };
  approveShared = async () => {
    if (this.snapshot.status !== 'shared') return null;
    this.approvedFields = assetFields(this.snapshot.asset);
    this.publish({ status: 'dirty', message: '' });
    return this.flush();
  };
  reconcile(latest: AssetRead, keepDraft: boolean) {
    if (latest.id !== this.snapshot.asset.id || this.pending || this.snapshot.recoveryBlocked) return;
    const saved = { ...this.snapshot.saved, ...latest };
    const asset = keepDraft ? { ...saved, ...assetFields(this.snapshot.asset) } : saved;
    this.approvedFields = null;
    this.changeNo += 1;
    this.publish({ saved, asset, dirty: !same(asset, saved), status: same(asset, saved) ? 'saved' : 'dirty', message: '' });
    this.persist();
    clearTimeout(this.timer);
    if (this.snapshot.dirty && !this.disposed) this.timer = setTimeout(() => { void this.flush(); }, this.pauseMs);
  }
  discardRecovery() {
    if (!this.snapshot.recoveryBlocked || this.disposed) return false;
    try { this.recovery?.clear(); }
    catch { this.publish({ message: '无法清除本机素材恢复记录，请下载备份并核对浏览器存储。' }); return false; }
    this.publish({ recoveryBlocked: false, status: this.snapshot.dirty ? 'dirty' : 'saved', message: '' });
    this.persist();
    if (this.snapshot.dirty) this.timer = setTimeout(() => { void this.flush(); }, this.pauseMs);
    return true;
  }
  /** Media adoption changes the base only after the explicit transaction succeeds. */
  accept(latest: AssetRead) { this.reconcile(latest, true); }
  conflict(message = '素材已在其他页面修改，草稿已保留。请核对最新版本。') {
    clearTimeout(this.timer); this.publish({ status: 'conflict', message }); this.persist();
  }
  dispose() { this.disposed = true; clearTimeout(this.timer); this.listeners.clear(); }
}

export function assetRecoveryKey(actorId: string, projectId: string, episodeId: string, assetId: string) {
  return `short-drama:asset-editor:v1:${actorId}:${projectId}:${episodeId}:${assetId}`;
}

export function assetRecoveryStorage(key: string): AssetRecoveryStorage {
  return {
    read() { const value = localStorage.getItem(key); return value ? JSON.parse(value) as AssetRecovery : null; },
    save(value) { localStorage.setItem(key, JSON.stringify(value)); },
    clear() { localStorage.removeItem(key); },
  };
}
