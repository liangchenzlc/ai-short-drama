import {
  assetCreationAttempt,
  assetCreationUnknown,
  assetDraftBody,
  assetDraftHasInput,
  mergeCreatedAsset,
  readLocalAssetDrafts,
} from '@host/features/creation/assets/sessions/local-asset-draft-model';
import type { LocalAssetDraft } from '@host/types/creation/assets/sessions/local-asset-draft-model';
import type { LocalAssetSnapshot, LocalAssetTransport } from '@host/types/creation/assets/sessions/local-asset-session';
/** Creation idempotency and local placeholders remain owned after their editor unmounts. */
export class LocalAssetSession {
  private state: LocalAssetSnapshot;
  private listeners = new Set<() => void>();
  private creating = new Map<string, Promise<boolean>>();
  private uploading = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private active = true;
  private disposed = false;
  private epoch = 0;
  private persistenceFailed = false;
  private recoveryInvalid = false;
  constructor(
    private key: string,
    private api: LocalAssetTransport,
    private readOnly = false,
  ) {
    let drafts: LocalAssetDraft[] = [];
    let recoveryError = '';
    try {
      drafts = readLocalAssetDrafts(key);
    } catch {
      this.recoveryInvalid = true;
      recoveryError = '本机素材记录无法读取。原记录已保留，请下载后核对。';
    }
    this.state = {
      drafts,
      created: {},
      recoveryError,
      status: recoveryError ? 'error' : 'saved',
      message: recoveryError,
    };
    this.schedule();
  }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<LocalAssetSnapshot> = {}) {
    this.state = { ...this.state, ...patch };
    const problem = this.state.drafts.find((draft) => draft.status === 'error' || draft.status === 'unknown');
    this.state.status = this.state.recoveryError || problem ? 'error' : this.creating.size ? 'saving' : 'saved';
    this.state.message = this.state.recoveryError || problem?.message || '';
    this.listeners.forEach((listener) => listener());
  }
  private preserve() {
    if (this.recoveryInvalid) return;
    const wasFailed = this.persistenceFailed;
    try {
      if (this.state.drafts.length) localStorage.setItem(this.key, JSON.stringify(this.state.drafts));
      else localStorage.removeItem(this.key);
      this.persistenceFailed = false;
      if (wasFailed) this.publish({ recoveryError: '' });
    } catch {
      this.persistenceFailed = true;
      this.publish({
        recoveryError: '本机无法保留素材输入，请在保存详情中复制或下载。',
      });
    }
  }
  replace = (drafts: LocalAssetDraft[]) => {
    if (!this.active || this.disposed || this.recoveryInvalid) return;
    this.publish({ drafts });
    this.preserve();
    this.schedule();
  };
  edit = (id: string, patch: Partial<LocalAssetDraft>) =>
    this.replace(this.state.drafts.map((draft) => (draft.id === id ? { ...draft, ...patch } : draft)));
  private schedule() {
    clearTimeout(this.timer);
    if (!this.active || this.disposed || this.readOnly || this.state.recoveryError) return;
    if (
      this.state.drafts.some(
        (draft) => !this.uploading.has(draft.id) && draft.status === 'local' && assetDraftBody(draft),
      )
    ) {
      this.timer = setTimeout(() => {
        for (const draft of this.state.drafts)
          if (!this.uploading.has(draft.id) && draft.status === 'local' && assetDraftBody(draft))
            void this.create(draft.id);
      }, 1000);
    }
  }
  create = (id: string): Promise<boolean> => {
    const existing = this.creating.get(id);
    if (existing) return existing;
    const draft = this.state.drafts.find((item) => item.id === id);
    if (!draft || !this.active || this.disposed || this.readOnly || this.state.recoveryError || this.uploading.has(id))
      return Promise.resolve(false);
    const attempt = assetCreationAttempt(draft);
    if (!attempt) return Promise.resolve(false);
    const epoch = this.epoch;
    this.edit(id, { attempt, status: 'saving', message: '' });
    const request = (async () => {
      try {
        const saved = await this.api.create(attempt.body, attempt.key);
        if (!this.active || this.disposed || epoch !== this.epoch) return false;
        const current = this.state.drafts.find((item) => item.id === id) ?? draft;
        // Keep the placeholder and its original request if preserving newer fields fails.
        this.api.preserveCreated(saved, current);
        this.publish({
          drafts: this.state.drafts.filter((item) => item.id !== id),
          created: {
            ...this.state.created,
            [id]: {
              asset: mergeCreatedAsset(saved, current),
              generation: current.generation,
            },
          },
        });
        this.preserve();
        return true;
      } catch (cause) {
        if (!this.active || this.disposed || epoch !== this.epoch) return false;
        const unknown = assetCreationUnknown(cause);
        this.edit(id, {
          status: unknown ? 'unknown' : 'error',
          message: unknown
            ? '创建结果待核对，原请求和输入已保留。'
            : `素材创建失败：${cause instanceof Error ? cause.message : '请稍后重试'}`,
        });
        return false;
      }
    })();
    this.creating.set(id, request);
    this.publish();
    void request.finally(() => {
      this.creating.delete(id);
      this.publish();
      this.schedule();
    });
    return request;
  };
  hasUnsettled = () => !!this.creating.size || this.state.drafts.some(assetDraftHasInput) || !!this.state.recoveryError;
  setUploadBusy(id: string, busy: boolean) {
    if (busy) this.uploading.add(id);
    else this.uploading.delete(id);
    this.schedule();
  }
  hasMemoryOnlyChanges = () => this.persistenceFailed && this.state.drafts.some(assetDraftHasInput);
  hasUnpersisted = this.hasMemoryOnlyChanges;
  flush = async () => {
    if (this.recoveryInvalid) return false;
    this.preserve();
    this.schedule();
    return !this.hasMemoryOnlyChanges();
  };
  retry = async () => {
    if (this.recoveryInvalid) return false;
    if (this.persistenceFailed) {
      this.publish({ recoveryError: '' });
      this.preserve();
    }
    this.schedule();
    return !this.state.recoveryError;
  };
  discardRecovery() {
    try {
      localStorage.removeItem(this.key);
      this.persistenceFailed = false;
      this.recoveryInvalid = false;
      this.publish({ recoveryError: '', drafts: [] });
      return true;
    } catch {
      return false;
    }
  }
  suspend() {
    this.active = false;
    ++this.epoch;
    clearTimeout(this.timer);
  }
  resume() {
    if (this.active || this.disposed) return;
    this.active = true;
    this.publish({
      drafts: this.state.drafts.map((draft) =>
        draft.status === 'saving'
          ? {
              ...draft,
              status: 'unknown',
              message: '创建结果待核对，原请求已保留。',
            }
          : draft,
      ),
    });
    this.preserve();
    this.schedule();
  }
  dispose() {
    this.disposed = true;
    this.suspend();
    this.listeners.clear();
  }
}
