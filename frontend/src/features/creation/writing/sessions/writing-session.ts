import type {
  WritingResponse,
  WritingTask,
  WritingTransport,
} from '@host/types/creation/writing/documents';
import type {
  Attempt,
  Field,
  WritingDraft,
  WritingRecovery,
  WritingSnapshot,
} from '@host/types/creation/writing/sessions/writing-session';
const sameRecord = (a: WritingResponse['editing_script'], b: WritingResponse['editing_script']) =>
  a?.id === b?.id && a?.content === b?.content;
const sameWriting = (a: WritingResponse, b: WritingResponse) =>
  a.content_version === b.content_version &&
  sameRecord(a.editing_script, b.editing_script) &&
  a.editing_script?.state === b.editing_script?.state &&
  a.confirmed_script_id === b.confirmed_script_id;

/** 每个账号与作品只保留一条保存队列；服务端回执只推进基准，不能覆盖用户的新输入。 */
export class WritingSession {
  private server: WritingResponse | null = null;
  private snapshot: WritingSnapshot = {
    loaded: false,
    status: 'loading',
    script: '',
    scriptId: null,
    contentVersion: '0',
    confirmed: false,
    dirty: false,
    busy: false,
    message: '',
    activeWritingTasks: [],
  };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;
  private disposed = false;
  private active = true;
  private epoch = 0;
  private persistenceFailed = false;
  private uncertain: Attempt | null = null;
  private recovered: WritingDraft | null = null;
  private recoveryBlocked = false;
  private persisted = '';
  private scriptChangeSource: 'import' | undefined;
  constructor(
    private readonly api: WritingTransport,
    private readonly recovery?: WritingRecovery,
  ) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  trackTask(task: WritingTask) {
    if (!this.server) return;
    this.server = {
      ...this.server,
      active_writing_tasks: [
        ...(this.server.active_writing_tasks ?? []).filter((item) => item.task_id !== task.task_id),
        task,
      ],
    };
    this.publish();
  }
  async refreshFromServer(): Promise<boolean> {
    if (this.running) await this.running;
    if (this.disposed || !this.active || !this.server || this.snapshot.busy) return false;
    const before = this.server;
    const epoch = this.epoch;
    this.publish({ busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      const patch: Partial<WritingSnapshot> = {};
      let conflict = false;
      const dirty = this.snapshot.script !== (before.editing_script?.content ?? '');
      if (dirty && !sameRecord(before.editing_script, data.editing_script)) conflict = true;
      else if (!dirty) patch.script = data.editing_script?.content ?? '';
      if (conflict || this.uncertain || this.recovered || this.recoveryBlocked) {
        // 冲突时保留原基准和本机输入，必须明确核对后才能继续保存。
        this.server = {
          ...before,
          active_writing_tasks: data.active_writing_tasks,
        };
        return this.fail(true, '服务端正文已变化，本机输入仍保留。请在保存详情中核对后合并。');
      }
      this.server = data;
      this.publish({ ...patch, message: '', status: 'saved' });
      if (this.snapshot.dirty) {
        this.publish({ status: 'unsaved' });
        this.timer = setTimeout(() => {
          void this.flush();
        }, 1000);
      }
      return true;
    } catch {
      this.publish({
        message: '读取最新创作内容失败，现有输入仍保留，请重试。',
      });
      return false;
    } finally {
      this.publish({ busy: false });
    }
  }
  private publish(patch: Partial<WritingSnapshot> = {}) {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...patch };
    const scriptChanged = this.snapshot.script !== (this.server?.editing_script?.content ?? '');
    this.snapshot.dirty = !!this.server && (scriptChanged || !!this.uncertain);
    this.snapshot.activeWritingTasks = this.server?.active_writing_tasks ?? [];
    this.snapshot.scriptId = this.server?.editing_script?.id ?? null;
    this.snapshot.contentVersion = this.server?.content_version ?? '0';
    this.snapshot.confirmed =
      !!this.server?.editing_script &&
      this.server.editing_script.state === 'confirmed' &&
      this.server.confirmed_script_id === this.server.editing_script.id &&
      !scriptChanged;
    this.snapshot.recoverable = this.recovered !== null;
    this.snapshot.recoveryBlocked = this.recoveryBlocked;
    this.snapshot.recoveryChanged =
      !!this.recovered &&
      (this.recovered.base_version !== this.server?.content_version ||
        (this.recovered.script_id !== this.server?.editing_script?.id &&
          !(this.recovered.script_id === null && !this.server?.editing_script)));
    if (this.recovery && this.server && this.snapshot.loaded && !this.recovered && !this.recoveryBlocked) {
      try {
        const draft: WritingDraft = {
          base_version: this.server.content_version,
          script: this.snapshot.script,
          script_id: this.server.editing_script?.id ?? null,
          pending: this.uncertain,
          script_change_source: this.scriptChangeSource,
        };
        const fingerprint = this.snapshot.dirty || this.uncertain ? JSON.stringify(draft) : '';
        if (fingerprint !== this.persisted) {
          if (fingerprint) this.recovery.save(draft);
          else this.recovery.clear();
          this.persisted = fingerprint;
          this.persistenceFailed = false;
        }
      } catch {
        this.persistenceFailed = true;
        this.snapshot.message = '本机无法保留当前输入，正在尝试同步；请在保存详情中复制或下载。';
      }
    }
    this.listeners.forEach((listener) => listener());
  }
  async load(initialOnly = false): Promise<boolean> {
    if (this.disposed || !this.active || this.running || this.snapshot.busy) return false;
    if (initialOnly && this.snapshot.loaded) return true;
    if (this.snapshot.loaded && this.snapshot.dirty) return false;
    const epoch = this.epoch;
    this.publish({ status: 'loading', busy: true, message: '' });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      if (!this.snapshot.loaded && this.recovery) {
        try {
          this.recovered = this.recovery.read();
          if (
            this.recovered &&
            (!this.recovered.pending || this.acknowledged(data, this.recovered.pending)) &&
            this.recovered.script === (data.editing_script?.content ?? '')
          ) {
            this.recovery.clear();
            this.recovered = null;
          }
        } catch {
          this.recoveryBlocked = true;
        }
      } else if (this.snapshot.loaded && this.recovery) {
        this.recovery.clear();
        this.recovered = null;
        this.recoveryBlocked = false;
        this.persisted = '';
      }
      this.server = data;
      this.uncertain = null;
      let restored: WritingDraft | null = null;
      if (this.recovered) {
        const draft = this.recovered;
        const accepted = draft.pending && this.acknowledged(data, draft.pending);
        const sameBase =
          draft.base_version === data.content_version && draft.script_id === (data.editing_script?.id ?? null);
        if (accepted || sameBase) {
          restored = draft;
          this.recovered = null;
          this.uncertain = accepted ? null : (draft.pending ?? null);
        }
      }
      this.publish({
        loaded: true,
        script: data.editing_script?.content ?? '',
        status: this.recoveryBlocked ? 'error' : 'saved',
        busy: false,
        message: this.recoveryBlocked
          ? '本机正文恢复记录无法读取，服务端正文仍可查看。请下载恢复记录核对，明确放弃后才能继续编辑。'
          : '',
      });
      if (restored) {
        this.scriptChangeSource =
          restored.pending?.field === 'script' && this.acknowledged(data, restored.pending)
            ? undefined
            : restored.script_change_source;
        this.publish({
          script: restored.script,
        });
        this.publish({ status: this.snapshot.dirty ? 'unsaved' : 'saved' });
        if (this.snapshot.dirty)
          this.timer = setTimeout(() => {
            void this.flush();
          }, 1000);
      } else if (this.recovered)
        this.publish({
          status: 'conflict',
          message: '本机内容与当前版本不同，已保留双方内容；可在保存详情中核对。',
        });
      return true;
    } catch {
      if (!this.active || epoch !== this.epoch) return false;
      this.publish({
        status: 'error',
        busy: false,
        message: '内容加载失败，请重试。输入仍保留。',
      });
      return false;
    }
  }
  async reloadFromServer(): Promise<boolean> {
    if (this.disposed || !this.active || this.running || this.snapshot.busy) return false;
    clearTimeout(this.timer);
    const epoch = this.epoch;
    const previousStatus = this.snapshot.status;
    this.publish({ status: 'loading', busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      let pending = this.uncertain ?? this.recovered?.pending ?? null;
      if (!pending) {
        try {
          pending = this.recovery?.read()?.pending ?? null;
        } catch {
          /* The UI backs up the original raw record before explicit replacement. */
        }
      }
      const unresolved = !!pending && data.content_version === pending.before.content_version;
      if (!unresolved) this.recovery?.clear();
      this.server = data;
      this.uncertain = unresolved ? pending : null;
      this.recovered = null;
      this.recoveryBlocked = false;
      this.persisted = '';
      this.persistenceFailed = false;
      this.publish({
        loaded: true,
        script: data.editing_script?.content ?? '',
        status: unresolved ? 'unsaved' : 'saved',
        busy: false,
        message: '',
      });
      if (unresolved)
        this.timer = setTimeout(() => {
          void this.flush();
        }, 1000);
      return true;
    } catch {
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      this.publish({
        status: previousStatus === 'conflict' ? 'conflict' : 'error',
        busy: false,
        message: '读取服务端正文失败，本机内容和恢复记录仍保留，请重试。',
      });
      return false;
    }
  }
  edit(field: Field, content: string, changeSource?: 'import') {
    if (
      !this.server ||
      this.disposed ||
      !this.active ||
      this.snapshot.status === 'loading' ||
      this.recovered ||
      this.recoveryBlocked
    )
      return;
    if (this.snapshot.activeWritingTasks.some((task) => task.target === field)) return;
    if (field === 'script' && changeSource) this.scriptChangeSource = changeSource;
    this.publish({ [field]: content });
    if (this.snapshot.status === 'error' || this.snapshot.status === 'conflict') return;
    this.publish({
      status: this.snapshot.busy ? 'saving' : this.snapshot.dirty ? 'unsaved' : 'saved',
    });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      void this.flush();
    }, 1000);
  }
  importScript(content: string) {
    this.edit('script', content, 'import');
  }
  private fail(conflict: boolean, message?: string) {
    this.publish({
      status: conflict ? 'conflict' : 'error',
      message:
        message ??
        (conflict
          ? '服务端内容已变化，自动保存已暂停。本机输入已保留，请下载后核对并手动合并。'
          : '保存未完成，输入仍保留在当前页面。请重试，也可在保存详情中下载当前内容。'),
    });
    return false;
  }
  private acknowledged(data: WritingResponse, attempt: Attempt) {
    const { before, content } = attempt;
    if (data.content_version !== String(BigInt(before.content_version) + 1n)) return false;
    const state = content.trim() ? 'confirmed' : 'unconfirmed';
    return (
      !!data.editing_script &&
      data.editing_script.content === content &&
      data.editing_script.state === state &&
      (!before.editing_script || data.editing_script.id === before.editing_script.id) &&
      data.confirmed_script_id === (content.trim() ? data.editing_script.id : null)
    );
  }
  private async reconcile(attempt: Attempt) {
    const epoch = this.epoch;
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      if (this.acknowledged(data, attempt)) {
        this.server = data;
        this.uncertain = null;
        if (attempt.field === 'script') this.scriptChangeSource = undefined;
        this.publish();
        return true;
      }
      if (sameWriting(data, attempt.before)) return this.fail(false);
      return this.fail(true);
    } catch {
      return !this.active || epoch !== this.epoch
        ? false
        : this.fail(false, '暂未核实保存结果，输入已保留，可稍后重试。');
    }
  }
  flush(): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.running) return this.running;
    if (
      this.disposed ||
      !this.active ||
      !this.server ||
      this.recovered ||
      this.recoveryBlocked ||
      this.snapshot.busy ||
      ['error', 'conflict', 'loading'].includes(this.snapshot.status)
    )
      return Promise.resolve(false);
    this.running = this.drain().finally(() => {
      this.running = null;
      this.publish({ busy: false });
    });
    return this.running;
  }
  private async drain() {
    // 账号切换会推进 epoch；旧请求返回后不得触碰新账号的会话状态。
    const epoch = this.epoch;
    this.publish({ busy: true, status: 'saving', message: '' });
    while (!this.disposed && this.active && epoch === this.epoch && this.snapshot.dirty && this.server) {
      const field: Field = 'script';
      const content = this.snapshot[field];
      if (new TextEncoder().encode(content).length > 1048576)
        return this.fail(false, '单份文本不能超过 1 MiB，请缩短内容后重试。');
      const attempt = {
        before: this.server,
        field,
        content,
        ...(field === 'script' && this.scriptChangeSource ? { changeSource: this.scriptChangeSource } : {}),
      };
      const priorUncertain = this.uncertain;
      this.uncertain = attempt;
      this.publish();
      try {
        const body = { content, content_version: this.server.content_version };
        const result = await this.api.script({
          ...body,
          script_id: this.server.editing_script?.id ?? null,
          ...(attempt.changeSource ? { change_source: attempt.changeSource } : {}),
        });
        if (this.disposed || !this.active || epoch !== this.epoch) return false;
        this.server = {
          ...this.server,
          content_version: result.content_version,
          editing_script: result.script,
          confirmed_script_id: result.confirmed_script_id,
        };
        this.scriptChangeSource = undefined;
        this.uncertain = null;
        this.publish();
      } catch (error) {
        if (this.disposed || !this.active || epoch !== this.epoch) return false;
        const status = (error as { status?: number })?.status;
        if (status === 409) {
          this.uncertain = priorUncertain;
          return this.fail(true);
        }
        if (status && status < 500)
          return this.fail(
            false,
            status === 422
              ? '内容不符合保存要求，请检查后重试。'
              : '无法保存当前作品内容，请检查项目是否仍存在及操作权限。',
          );
        // 受理结果不明时先读取原版本核对，不能把重试当成第二次写入。
        this.uncertain = attempt;
        if (!(await this.reconcile(attempt))) return false;
      }
    }
    if (this.disposed || !this.active || epoch !== this.epoch) return false;
    this.publish({ status: 'saved' });
    return true;
  }
  async retry() {
    if (
      this.disposed ||
      !this.active ||
      this.snapshot.status === 'conflict' ||
      this.snapshot.busy ||
      this.recoveryBlocked
    )
      return false;
    if (!this.server) return this.load();
    const epoch = this.epoch;
    this.publish({ busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      if (this.uncertain && this.acknowledged(data, this.uncertain)) {
        if (this.uncertain.field === 'script') this.scriptChangeSource = undefined;
        this.server = data;
        this.uncertain = null;
      } else if (!sameWriting(data, this.server)) return this.fail(true);
      this.publish({ status: 'unsaved', message: '' });
    } catch {
      return !this.active || epoch !== this.epoch ? false : this.fail(false);
    } finally {
      this.publish({ busy: false });
    }
    return this.flush();
  }
  getRecoveredDraft() {
    return this.recovered;
  }
  exportRecovered() {
    return this.recovery?.export?.() ?? (this.recovered ? JSON.stringify(this.recovered, null, 2) : null);
  }
  discardRecovered() {
    this.recovery?.clear();
    this.recovered = null;
    this.recoveryBlocked = false;
    this.persisted = '';
    this.publish({
      status: this.snapshot.dirty ? 'unsaved' : 'saved',
      message: '',
    });
  }
  async restoreRecovered(rebase = false) {
    if (!this.recovered || this.disposed || !this.active || this.snapshot.busy || this.running) return false;
    const epoch = this.epoch;
    const draft = this.recovered;
    this.publish({ busy: true });
    try {
      const latest = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      this.server = latest;
      const changed =
        latest.content_version !== draft.base_version || (latest.editing_script?.id ?? null) !== draft.script_id;
      if (changed && !rebase) {
        this.publish({
          script: latest.editing_script?.content ?? '',
          message: '恢复稿的基准版本已变化，请对照当前正文并明确决定是否保留恢复稿。',
        });
        return false;
      }
      this.recovered = null;
      this.persisted = '';
      this.scriptChangeSource = draft.script_change_source;
      this.publish({
        script: draft.script,
        status: 'unsaved',
        message: '',
      });
      this.timer = setTimeout(() => {
        void this.flush();
      }, 1000);
      return true;
    } catch {
      this.publish({
        message: '恢复前读取最新正文失败，恢复稿仍保留，请重试。',
      });
      return false;
    } finally {
      this.publish({ busy: false });
    }
  }
  hasUnsettled = () =>
    this.snapshot.dirty ||
    !!this.running ||
    !!this.recovered ||
    this.recoveryBlocked ||
    ['error', 'conflict'].includes(this.snapshot.status);
  hasMemoryOnlyChanges = () => (this.snapshot.dirty || !!this.uncertain) && (!this.recovery || this.persistenceFailed);
  hasUnpersisted = this.hasMemoryOnlyChanges;
  suspend() {
    this.active = false;
    ++this.epoch;
    clearTimeout(this.timer);
  }
  resume() {
    if (this.active || this.disposed) return;
    this.active = true;
    const resume = () => {
      this.publish({ busy: false });
      if (!this.snapshot.loaded) void this.load();
      else if (this.snapshot.status !== 'conflict' && !this.recoveryBlocked) void this.retry();
    };
    if (this.running) void this.running.finally(resume);
    else resume();
  }
  dispose() {
    this.disposed = true;
    this.suspend();
    this.listeners.clear();
  }
}
