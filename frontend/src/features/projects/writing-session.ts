import type { EditorDocument, WritingResponse, WritingTransport } from '../../api/modules/episode-writing';
import { documentText, sameDocument } from './script-document';

type Field = 'novel' | 'script';
type Status = 'loading' | 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict';
export interface WritingSnapshot {
  loaded: boolean; status: Status; novel: string; script: string; scriptId: string | null;
  contentVersion: string; confirmed: boolean; dirty: boolean; busy: boolean; message: string;
  editorDocument: EditorDocument | null;
  recoverable?: boolean; recoveryChanged?: boolean; recoveryBlocked?: boolean;
}
export interface WritingDraft { base_version: string; novel: string; script: string; script_id: string | null; editor_document?: EditorDocument | null; pending?: Attempt | null }
export interface WritingRecovery { read: () => WritingDraft | null; save: (draft: WritingDraft) => void; clear: () => void; export?: () => string | null }
type Attempt = { before: WritingResponse; field: Field; content: string; editorDocument: EditorDocument | null };
const sameRecord = (a: WritingResponse['novel'], b: WritingResponse['novel']) => a?.id === b?.id && a?.content === b?.content;
const sameScript = (a: WritingResponse['editing_script'], b: WritingResponse['editing_script']) => sameRecord(a, b)
  && sameDocument(a?.editor_document, b?.editor_document, a?.content ?? '');
const sameWriting = (a: WritingResponse, b: WritingResponse) => a.content_version === b.content_version
  && sameRecord(a.novel, b.novel) && sameScript(a.editing_script, b.editing_script)
  && a.editing_script?.state === b.editing_script?.state && a.confirmed_script_id === b.confirmed_script_id;

/** One retained account/resource queue. Server acknowledgement never replaces newer input. */
export class WritingSession {
  private server: WritingResponse | null = null;
  private snapshot: WritingSnapshot = { loaded: false, status: 'loading', novel: '', script: '', editorDocument: null, scriptId: null, contentVersion: '0', confirmed: false, dirty: false, busy: false, message: '' };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;
  private operation: Promise<boolean> | null = null;
  private disposed = false;
  private active = true;
  private epoch = 0;
  private persistenceFailed = false;
  private uncertain: Attempt | null = null;
  private recovered: WritingDraft | null = null;
  private recoveryBlocked = false;
  private persisted = '';
  constructor(private readonly api: WritingTransport, private readonly recovery?: WritingRecovery) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(patch: Partial<WritingSnapshot> = {}) {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...patch };
    const scriptChanged = this.snapshot.script !== (this.server?.editing_script?.content ?? '')
      || !sameDocument(this.snapshot.editorDocument, this.server?.editing_script?.editor_document, this.snapshot.script);
    this.snapshot.dirty = !!this.server && (this.snapshot.novel !== (this.server.novel?.content ?? '') || scriptChanged || !!this.uncertain);
    this.snapshot.scriptId = this.server?.editing_script?.id ?? null;
    this.snapshot.contentVersion = this.server?.content_version ?? '0';
    this.snapshot.confirmed = !!this.server?.editing_script && this.server.editing_script.state === 'confirmed'
      && this.server.confirmed_script_id === this.server.editing_script.id && !scriptChanged;
    this.snapshot.recoverable = this.recovered !== null;
    this.snapshot.recoveryBlocked = this.recoveryBlocked;
    this.snapshot.recoveryChanged = !!this.recovered && (this.recovered.base_version !== this.server?.content_version || this.recovered.script_id !== this.server?.editing_script?.id && !(this.recovered.script_id === null && !this.server?.editing_script));
    if (this.recovery && this.server && this.snapshot.loaded && !this.recovered && !this.recoveryBlocked) {
      try {
        const draft: WritingDraft = { base_version: this.server.content_version, novel: this.snapshot.novel, script: this.snapshot.script, editor_document: this.snapshot.editorDocument, script_id: this.server.editing_script?.id ?? null, pending: this.uncertain };
        const fingerprint = this.snapshot.dirty || this.uncertain ? JSON.stringify(draft) : '';
        if (fingerprint !== this.persisted) {
          if (fingerprint) this.recovery.save(draft); else this.recovery.clear();
          this.persisted = fingerprint;
          this.persistenceFailed = false;
        }
      } catch { this.persistenceFailed = true; this.snapshot.message = '本机无法保留当前输入，正在尝试同步；请在保存详情中复制或下载。'; }
    }
    this.listeners.forEach(listener => listener());
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
          if (this.recovered && (!this.recovered.pending || this.acknowledged(data, this.recovered.pending)) && this.recovered.novel === (data.novel?.content ?? '') && this.recovered.script === (data.editing_script?.content ?? '') && sameDocument(this.recovered.editor_document, data.editing_script?.editor_document, this.recovered.script)) { this.recovery.clear(); this.recovered = null; }
        } catch { this.recoveryBlocked = true; }
      } else if (this.snapshot.loaded && this.recovery) { this.recovery.clear(); this.recovered = null; this.recoveryBlocked = false; this.persisted = ''; }
      this.server = data; this.uncertain = null;
      let restored: WritingDraft | null = null;
      if (this.recovered) {
        const draft = this.recovered;
        const accepted = draft.pending && this.acknowledged(data, draft.pending);
        const sameBase = draft.base_version === data.content_version && draft.script_id === (data.editing_script?.id ?? null);
        if (accepted || sameBase) { restored = draft; this.recovered = null; this.uncertain = accepted ? null : draft.pending ?? null; }
      }
      this.publish({ loaded: true, novel: data.novel?.content ?? '', script: data.editing_script?.content ?? '', editorDocument: data.editing_script?.editor_document ?? null, status: this.recoveryBlocked ? 'error' : 'saved', busy: false, message: this.recoveryBlocked ? '本机正文恢复记录无法读取，服务端正文仍可查看。请下载恢复记录核对，明确放弃后才能继续编辑。' : '' });
      if (restored) {
        this.publish({ novel: restored.novel, script: restored.script, editorDocument: restored.editor_document ?? null });
        this.publish({ status: this.snapshot.dirty ? 'unsaved' : 'saved' });
        if (this.snapshot.dirty) this.timer = setTimeout(() => { void this.flush(); }, 1000);
      } else if (this.recovered) this.publish({ status: 'conflict', message: '本机内容与当前版本不同，已保留双方内容；可在保存详情中核对。' });
      return true;
    } catch { if (!this.active || epoch !== this.epoch) return false; this.publish({ status: 'error', busy: false, message: '内容加载失败，请重试。输入仍保留。' }); return false; }
  }
  async reloadFromServer(): Promise<boolean> {
    if (this.disposed || !this.active || this.running || this.operation || this.snapshot.busy) return false;
    clearTimeout(this.timer);
    const epoch = this.epoch;
    const previousStatus = this.snapshot.status;
    this.publish({ status: 'loading', busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      let pending = this.uncertain ?? this.recovered?.pending ?? null;
      if (!pending) {
        try { pending = this.recovery?.read()?.pending ?? null; }
        catch { /* The UI backs up the original raw record before explicit replacement. */ }
      }
      const unresolved = !!pending && data.content_version === pending.before.content_version;
      if (!unresolved) this.recovery?.clear();
      this.server = data; this.uncertain = unresolved ? pending : null; this.recovered = null; this.recoveryBlocked = false;
      this.persisted = ''; this.persistenceFailed = false;
      this.publish({ loaded: true, novel: data.novel?.content ?? '', script: data.editing_script?.content ?? '', editorDocument: data.editing_script?.editor_document ?? null, status: unresolved ? 'unsaved' : 'saved', busy: false, message: '' });
      if (unresolved) this.timer = setTimeout(() => { void this.flush(); }, 1000);
      return true;
    } catch {
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      this.publish({ status: previousStatus === 'conflict' ? 'conflict' : 'error', busy: false, message: '读取服务端正文失败，本机内容和恢复记录仍保留，请重试。' });
      return false;
    }
  }
  edit(field: Field, content: string, editorDocument: EditorDocument | null = null) {
    if (!this.server || this.disposed || !this.active || this.snapshot.status === 'loading' || this.recovered || this.recoveryBlocked) return;
    if (field === 'script' && editorDocument && documentText(editorDocument) !== content) {
      this.fail(false, '剧本文字与格式不一致，已保留原稿。请重新载入编辑器后重试。');
      return;
    }
    this.publish({ [field]: content, ...(field === 'script' ? { editorDocument } : {}) });
    if (this.snapshot.status === 'error' || this.snapshot.status === 'conflict') return;
    this.publish({ status: this.snapshot.busy ? 'saving' : this.snapshot.dirty ? 'unsaved' : 'saved' });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flush(); }, 1000);
  }
  private fail(conflict: boolean, message?: string) {
    this.publish({ status: conflict ? 'conflict' : 'error', message: message ?? (conflict
      ? '服务端内容已变化，自动保存已暂停。请先复制本地草稿，再载入服务端版本并手动合并。'
      : '保存未完成，草稿仍保留在当前页面。请重试；离开前请复制草稿。') });
    return false;
  }
  private acknowledged(data: WritingResponse, attempt: Attempt) {
    const { before, field, content, editorDocument } = attempt;
    if (data.content_version !== String(BigInt(before.content_version) + 1n)) return false;
    if (field === 'novel') return !!data.novel && data.novel.content === content
      && (!before.novel || before.novel.id === data.novel.id)
      && sameScript(data.editing_script, before.editing_script)
      && data.editing_script?.state === before.editing_script?.state && data.confirmed_script_id === before.confirmed_script_id;
    const formatOnly = before.editing_script?.content === content;
    const state = formatOnly ? before.editing_script?.state : 'unconfirmed';
    return !!data.editing_script && data.editing_script.content === content && data.editing_script.state === state
      && sameDocument(data.editing_script.editor_document, editorDocument, content)
      && (!before.editing_script || data.editing_script.id === before.editing_script.id)
      && sameRecord(data.novel, before.novel)
      && data.confirmed_script_id === (!formatOnly && before.confirmed_script_id === before.editing_script?.id ? null : before.confirmed_script_id);
  }
  private async reconcile(attempt: Attempt) {
    const epoch = this.epoch;
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      if (this.acknowledged(data, attempt)) { this.server = data; this.uncertain = null; this.publish(); return true; }
      if (sameWriting(data, attempt.before)) return this.fail(false);
      return this.fail(true);
    } catch { return !this.active || epoch !== this.epoch ? false : this.fail(false, '暂未核实保存结果，输入已保留，可稍后重试。'); }
  }
  flush(): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.operation) return this.operation.then(ok => ok ? this.flush() : false);
    if (this.running) return this.running;
    if (this.disposed || !this.active || !this.server || this.recovered || this.recoveryBlocked || this.snapshot.busy || ['error', 'conflict', 'loading'].includes(this.snapshot.status)) return Promise.resolve(false);
    this.running = this.drain().finally(() => { this.running = null; this.publish({ busy: false }); });
    return this.running;
  }
  private async drain() {
    const epoch = this.epoch;
    this.publish({ busy: true, status: 'saving', message: '' });
    while (!this.disposed && this.active && epoch === this.epoch && this.snapshot.dirty && this.server) {
      const novelChanged = this.snapshot.novel !== (this.server.novel?.content ?? '');
      const scriptChanged = this.snapshot.script !== (this.server.editing_script?.content ?? '') || !sameDocument(this.snapshot.editorDocument, this.server.editing_script?.editor_document, this.snapshot.script);
      const field: Field = novelChanged ? 'novel' : scriptChanged ? 'script' : this.uncertain?.field ?? 'script';
      const content = this.snapshot[field];
      const editorDocument = this.snapshot.editorDocument;
      if (new TextEncoder().encode(content).length > 1048576) return this.fail(false, '单份文本不能超过 1 MiB，请缩短内容后重试。');
      const attempt = { before: this.server, field, content, editorDocument };
      this.uncertain = attempt; this.publish();
      try {
        const body = { content, content_version: this.server.content_version };
        if (field === 'novel') {
          const result = await this.api.novel(body);
          if (this.disposed || !this.active || epoch !== this.epoch) return false;
          this.server = { ...this.server, ...result };
        } else {
          const result = await this.api.script({ ...body, ...(editorDocument ? { editor_document: editorDocument } : {}), script_id: this.server.editing_script?.id ?? null });
          if (this.disposed || !this.active || epoch !== this.epoch) return false;
          this.server = { ...this.server, content_version: result.content_version, editing_script: result.script,
            confirmed_script_id: this.server.confirmed_script_id === result.script.id && result.script.state !== 'confirmed' ? null : this.server.confirmed_script_id };
          if (!sameDocument(result.script.editor_document, editorDocument, content)) return this.fail(true, '服务端返回的剧本格式与保存内容不一致，草稿已保留，请下载草稿并核对。');
        }
        this.uncertain = null; this.publish();
      } catch (error) {
        if (this.disposed || !this.active || epoch !== this.epoch) return false;
        const status = (error as { status?: number })?.status;
        if (status === 409) return this.fail(true);
        if (status && status < 500) return this.fail(false, status === 422 ? '内容不符合保存要求，请检查后重试。' : '无法保存本集内容，请检查项目是否仍存在及操作权限。');
        this.uncertain = attempt;
        if (!await this.reconcile(attempt)) return false;
      }
    }
    if (this.disposed || !this.active || epoch !== this.epoch) return false;
    this.publish({ status: 'saved' }); return true;
  }
  async retry() {
    if (this.disposed || !this.active || this.snapshot.status === 'conflict' || this.snapshot.busy || this.recoveryBlocked) return false;
    if (!this.server) return this.load();
    const epoch = this.epoch;
    this.publish({ busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      if (this.uncertain && this.acknowledged(data, this.uncertain)) { this.server = data; this.uncertain = null; }
      else if (!sameWriting(data, this.server)) return this.fail(true);
      this.publish({ status: 'unsaved', message: '' });
    } catch { return !this.active || epoch !== this.epoch ? false : this.fail(false); }
    finally { this.publish({ busy: false }); }
    return this.flush();
  }
  confirm() { return this.startAction('confirm'); }
  getRecoveredDraft() { return this.recovered; }
  exportRecovered() { return this.recovery?.export?.() ?? (this.recovered ? JSON.stringify(this.recovered, null, 2) : null); }
  discardRecovered() { this.recovery?.clear(); this.recovered = null; this.recoveryBlocked = false; this.persisted = ''; this.publish({ status: this.snapshot.dirty ? 'unsaved' : 'saved', message: '' }); }
  async restoreRecovered(rebase = false) {
    if (!this.recovered || this.disposed || !this.active || this.snapshot.busy || this.running) return false;
    const epoch = this.epoch;
    const draft = this.recovered;
    this.publish({ busy: true });
    try {
      const latest = await this.api.get();
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      this.server = latest;
      const changed = latest.content_version !== draft.base_version || (latest.editing_script?.id ?? null) !== draft.script_id;
      if (changed && !rebase) { this.publish({ novel: latest.novel?.content ?? '', script: latest.editing_script?.content ?? '', editorDocument: latest.editing_script?.editor_document ?? null, message: '恢复稿的基准版本已变化，请对照当前正文并明确决定是否保留恢复稿。' }); return false; }
      this.recovered = null; this.persisted = '';
      this.publish({ novel: draft.novel, script: draft.script, editorDocument: draft.editor_document ?? null, status: 'unsaved', message: '' });
      this.timer = setTimeout(() => { void this.flush(); }, 1000);
      return true;
    } catch { this.publish({ message: '恢复前读取最新正文失败，恢复稿仍保留，请重试。' }); return false; }
    finally { this.publish({ busy: false }); }
  }
  select(id: string) { return this.startAction('select', id); }
  private startAction(kind: 'confirm' | 'select', id?: string) {
    if (this.operation) return this.operation;
    this.operation = this.action(kind, id).finally(() => { this.operation = null; });
    return this.operation;
  }
  private async action(kind: 'confirm' | 'select', selectedId?: string) {
    if (!await this.flush() || !this.server || this.snapshot.busy || this.disposed) return false;
    const before = this.server;
    const epoch = this.epoch;
    const id = selectedId ?? before.editing_script?.id;
    if (!id || (kind === 'confirm' && !this.snapshot.script.trim())) return false;
    const accept = (data: WritingResponse) => {
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      this.server = data;
      if (kind === 'select' && (this.snapshot.script !== (before.editing_script?.content ?? '') || !sameDocument(this.snapshot.editorDocument, before.editing_script?.editor_document, this.snapshot.script))) {
        return this.fail(true, '切换剧本期间出现了新的本地修改，草稿已保留。请先下载草稿，再载入选中的服务端剧本并手动合并。');
      }
      this.publish({ ...(kind === 'select' ? { script: data.editing_script?.content ?? '', editorDocument: data.editing_script?.editor_document ?? null } : {}), status: 'saved' });
      return true;
    };
    this.publish({ busy: true, status: 'saving' });
    try {
      const data = await this.api[kind](id, { content_version: before.content_version });
      return accept(data);
    } catch (error) {
      if (this.disposed || !this.active || epoch !== this.epoch) return false;
      const status = (error as { status?: number })?.status;
      if (status === 409) return this.fail(true);
      if (!status || status >= 500) {
        try {
          const data = await this.api.get();
          const version = data.content_version === before.content_version || data.content_version === String(BigInt(before.content_version) + 1n);
          const match = data.editing_script?.id === id && (kind === 'select' || (data.confirmed_script_id === id && data.editing_script?.state === 'confirmed' && sameScript(data.editing_script, before.editing_script)));
          if (version && match && sameRecord(data.novel, before.novel)) {
            return accept(data);
          }
          if (!sameWriting(data, before)) return this.fail(true);
        } catch { /* Keep draft and pause. Retry must re-read the same server version. */ }
      }
      return this.fail(false, '操作未能确认完成，草稿仍保留。请重试核实状态后再操作。');
    } finally {
      this.publish({ busy: false });
      if (this.snapshot.dirty && this.snapshot.status === 'saved') this.timer = setTimeout(() => { void this.flush(); }, 1000);
    }
  }
  hasUnsettled = () => this.snapshot.dirty || !!this.running || !!this.operation || !!this.recovered || this.recoveryBlocked || ['error', 'conflict'].includes(this.snapshot.status);
  hasMemoryOnlyChanges = () => (this.snapshot.dirty || !!this.uncertain) && (!this.recovery || this.persistenceFailed);
  hasUnpersisted = this.hasMemoryOnlyChanges;
  suspend() { this.active = false; ++this.epoch; clearTimeout(this.timer); }
  resume() {
    if (this.active || this.disposed) return;
    this.active = true;
    const resume = () => {
      this.publish({ busy: false });
      if (!this.snapshot.loaded) void this.load();
      else if (this.snapshot.status !== 'conflict' && !this.recoveryBlocked) void this.retry();
    };
    if (this.running || this.operation) void (this.running ?? this.operation)!.finally(resume); else resume();
  }
  dispose() { this.disposed = true; this.suspend(); this.listeners.clear(); }
}
