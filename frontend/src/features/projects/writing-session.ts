import type { WritingResponse, WritingTransport } from '../../api/modules/episode-writing';

type Field = 'novel' | 'script';
type Status = 'loading' | 'saved' | 'unsaved' | 'saving' | 'error' | 'conflict';
export interface WritingSnapshot {
  loaded: boolean; status: Status; novel: string; script: string; scriptId: string | null;
  contentVersion: string; confirmed: boolean; dirty: boolean; busy: boolean; message: string;
}
type Attempt = { before: WritingResponse; field: Field; content: string };
const sameRecord = (a: WritingResponse['novel'], b: WritingResponse['novel']) => a?.id === b?.id && a?.content === b?.content;
const sameWriting = (a: WritingResponse, b: WritingResponse) => a.content_version === b.content_version
  && sameRecord(a.novel, b.novel) && sameRecord(a.editing_script, b.editing_script)
  && a.editing_script?.state === b.editing_script?.state && a.confirmed_script_id === b.confirmed_script_id;

/** One mounted episode owns one queue. Server acknowledgement never replaces a newer draft. */
export class WritingSession {
  private server: WritingResponse | null = null;
  private snapshot: WritingSnapshot = { loaded: false, status: 'loading', novel: '', script: '', scriptId: null, contentVersion: '0', confirmed: false, dirty: false, busy: false, message: '' };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;
  private operation: Promise<boolean> | null = null;
  private disposed = false;
  private uncertain: Attempt | null = null;
  constructor(private readonly api: WritingTransport) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(patch: Partial<WritingSnapshot> = {}) {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...patch };
    this.snapshot.dirty = !!this.server && (this.snapshot.novel !== (this.server.novel?.content ?? '') || this.snapshot.script !== (this.server.editing_script?.content ?? ''));
    this.snapshot.scriptId = this.server?.editing_script?.id ?? null;
    this.snapshot.contentVersion = this.server?.content_version ?? '0';
    this.snapshot.confirmed = !!this.server?.editing_script && this.server.editing_script.state === 'confirmed'
      && this.server.confirmed_script_id === this.server.editing_script.id && this.snapshot.script === this.server.editing_script.content;
    this.listeners.forEach(listener => listener());
  }
  async load(): Promise<boolean> {
    if (this.disposed || this.running || this.snapshot.busy) return false;
    this.publish({ status: 'loading', busy: true, message: '' });
    try {
      const data = await this.api.get();
      if (this.disposed) return false;
      this.server = data; this.uncertain = null;
      this.publish({ loaded: true, novel: data.novel?.content ?? '', script: data.editing_script?.content ?? '', status: 'saved', busy: false });
      return true;
    } catch { this.publish({ status: 'error', busy: false, message: '内容加载失败，请重试。草稿不会自动提交。' }); return false; }
  }
  edit(field: Field, content: string) {
    if (!this.server || this.disposed || this.snapshot.status === 'loading') return;
    this.publish({ [field]: content });
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
    const { before, field, content } = attempt;
    if (data.content_version !== String(BigInt(before.content_version) + 1n)) return false;
    if (field === 'novel') return !!data.novel && data.novel.content === content
      && (!before.novel || before.novel.id === data.novel.id)
      && sameRecord(data.editing_script, before.editing_script)
      && data.editing_script?.state === before.editing_script?.state && data.confirmed_script_id === before.confirmed_script_id;
    return !!data.editing_script && data.editing_script.content === content && data.editing_script.state === 'unconfirmed'
      && (!before.editing_script || data.editing_script.id === before.editing_script.id)
      && sameRecord(data.novel, before.novel)
      && data.confirmed_script_id === (before.confirmed_script_id === before.editing_script?.id ? null : before.confirmed_script_id);
  }
  private async reconcile(attempt: Attempt) {
    try {
      const data = await this.api.get();
      if (this.disposed) return false;
      if (this.acknowledged(data, attempt)) { this.server = data; this.uncertain = null; this.publish(); return true; }
      if (sameWriting(data, attempt.before)) { this.uncertain = null; return this.fail(false); }
      return this.fail(true);
    } catch { return this.fail(false, '无法核实保存结果，已暂停自动保存。请保持页面打开并重试。'); }
  }
  flush(): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.operation) return this.operation.then(ok => ok ? this.flush() : false);
    if (this.running) return this.running;
    if (this.disposed || !this.server || this.snapshot.busy || ['error', 'conflict', 'loading'].includes(this.snapshot.status)) return Promise.resolve(false);
    this.running = this.drain().finally(() => { this.running = null; this.publish({ busy: false }); });
    return this.running;
  }
  private async drain() {
    this.publish({ busy: true, status: 'saving', message: '' });
    while (!this.disposed && this.snapshot.dirty && this.server) {
      const field: Field = this.snapshot.novel !== (this.server.novel?.content ?? '') ? 'novel' : 'script';
      const content = this.snapshot[field];
      if (new TextEncoder().encode(content).length > 1048576) return this.fail(false, '单份文本不能超过 1 MiB，请缩短内容后重试。');
      const attempt = { before: this.server, field, content };
      try {
        const body = { content, content_version: this.server.content_version };
        if (field === 'novel') {
          const result = await this.api.novel(body);
          this.server = { ...this.server, ...result };
        } else {
          const result = await this.api.script({ ...body, script_id: this.server.editing_script?.id ?? null });
          this.server = { ...this.server, content_version: result.content_version, editing_script: result.script,
            confirmed_script_id: this.server.confirmed_script_id === result.script.id && result.script.state !== 'confirmed' ? null : this.server.confirmed_script_id };
        }
        this.publish();
      } catch (error) {
        const status = (error as { status?: number })?.status;
        if (status === 409) return this.fail(true);
        if (status && status < 500) return this.fail(false, status === 422 ? '内容不符合保存要求，请检查后重试。' : '无法保存本集内容，请检查项目是否仍存在及操作权限。');
        this.uncertain = attempt;
        if (!await this.reconcile(attempt)) return false;
      }
    }
    if (this.disposed) return false;
    this.publish({ status: 'saved' }); return true;
  }
  async retry() {
    if (this.disposed || this.snapshot.status === 'conflict' || this.snapshot.busy) return false;
    if (!this.server) return this.load();
    this.publish({ busy: true });
    try {
      const data = await this.api.get();
      if (this.disposed) return false;
      if (this.uncertain && this.acknowledged(data, this.uncertain)) this.server = data;
      else if (!sameWriting(data, this.server)) return this.fail(true);
      this.uncertain = null; this.publish({ status: 'unsaved', message: '' });
    } catch { return this.fail(false); }
    finally { this.publish({ busy: false }); }
    return this.flush();
  }
  confirm() { return this.startAction('confirm'); }
  select(id: string) { return this.startAction('select', id); }
  private startAction(kind: 'confirm' | 'select', id?: string) {
    if (this.operation) return this.operation;
    this.operation = this.action(kind, id).finally(() => { this.operation = null; });
    return this.operation;
  }
  private async action(kind: 'confirm' | 'select', selectedId?: string) {
    if (!await this.flush() || !this.server || this.snapshot.busy || this.disposed) return false;
    const before = this.server;
    const id = selectedId ?? before.editing_script?.id;
    if (!id || (kind === 'confirm' && !this.snapshot.script.trim())) return false;
    const accept = (data: WritingResponse) => {
      this.server = data;
      if (kind === 'select' && this.snapshot.script !== (before.editing_script?.content ?? '')) {
        return this.fail(true, '切换剧本期间出现了新的本地修改，草稿已保留。请先下载草稿，再载入选中的服务端剧本并手动合并。');
      }
      this.publish({ ...(kind === 'select' ? { script: data.editing_script?.content ?? '' } : {}), status: 'saved' });
      return true;
    };
    this.publish({ busy: true, status: 'saving' });
    try {
      const data = await this.api[kind](id, { content_version: before.content_version });
      return accept(data);
    } catch (error) {
      const status = (error as { status?: number })?.status;
      if (status === 409) return this.fail(true);
      if (!status || status >= 500) {
        try {
          const data = await this.api.get();
          const version = data.content_version === before.content_version || data.content_version === String(BigInt(before.content_version) + 1n);
          const match = data.editing_script?.id === id && (kind === 'select' || (data.confirmed_script_id === id && data.editing_script?.state === 'confirmed' && data.editing_script.content === before.editing_script?.content));
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
  dispose() { this.disposed = true; clearTimeout(this.timer); this.listeners.clear(); }
}
