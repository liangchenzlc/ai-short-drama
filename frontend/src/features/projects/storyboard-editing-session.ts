import { storyboardApi, type ShotRead } from '../../api/modules/storyboard';
import { creationScope, readRecovery, saveRecovery, clearRecovery, readRecoveryText, type StoredDraft } from './creation-recovery';
import { ResourceEditingSession } from './resource-editing-session';

export type ShotDraftValue = Pick<ShotRead, 'title' | 'script' | 'duration_ms' | 'asset_ids' | 'image_settings' | 'video_prompt' | 'video_settings'>;
export function shotDraftValue(shot: ShotRead): ShotDraftValue {
  const { title, script, duration_ms, asset_ids, image_settings, video_prompt, video_settings } = shot;
  return { title, script, duration_ms, asset_ids, image_settings, video_prompt, video_settings };
}
function isShotDraft(value: unknown): value is StoredDraft<ShotDraftValue> {
  if (!value || typeof value !== 'object') return false;
  const stored = value as Partial<StoredDraft<ShotDraftValue>>, doc = stored.document;
  return stored.format === 1 && typeof stored.base_version === 'string' && /^\d+$/.test(stored.base_version)
    && !!doc && typeof doc.script === 'string' && (doc.title === undefined || typeof doc.title === 'string')
    && Number.isInteger(doc.duration_ms) && Array.isArray(doc.asset_ids)
    && doc.asset_ids.every(id => typeof id === 'string' && /^\d+$/.test(id)) && !!doc.image_settings;
}
type Resource = { shot: ShotRead; storyboard_version: string };
export class StoryboardEditingSession {
  private sessions = new Map<string, ResourceEditingSession<Resource, ShotDraftValue>>();
  private listeners = new Set<() => void>();
  private state = { status: 'saved', message: '', dirty: false, busy: false, revision: 0, savedRevision: 0 };
  private savedRevision = 0;
  private active = true;
  constructor(private projectId: string, private episodeId: string, private account = 'anonymous') {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish = () => {
    const states = [...this.sessions.values()].map(session => session.getSnapshot());
    const status = ['conflict', 'error', 'saving', 'unsaved', 'loading'].find(value => states.some(item => item.status === value)) ?? 'saved';
    this.state = { status, message: states.find(item => item.message)?.message ?? '', dirty: states.some(item => item.dirty), busy: states.some(item => item.busy), revision: this.state.revision + 1, savedRevision: this.savedRevision };
    this.listeners.forEach(listener => listener());
  };
  ensure(shot: ShotRead, storyboardVersion = '0') {
    let session = this.sessions.get(shot.id);
    if (!session) {
      const api = storyboardApi(this.projectId, this.episodeId), id = shot.id;
      const scope = creationScope(`shot-edit:${this.projectId}:${this.episodeId}:${id}`, this.account);
      session = new ResourceEditingSession<Resource, ShotDraftValue>({
        read: () => api.shot(id), save: async (base, document, assertActive) => {
          const saved = await api.update(id, { row_version: base.shot.row_version, ...document });
          assertActive(); this.savedRevision++; return saved;
        },
        version: value => value.shot.row_version, document: value => shotDraftValue(value.shot),
        apply: (value, document) => ({ ...value, shot: { ...value.shot, ...document } }),
        recovery: { read: () => readRecovery(scope, isShotDraft), save: draft => saveRecovery(scope, draft), clear: () => clearRecovery(scope), export: () => readRecoveryText(scope), previous: () => readRecoveryText(`${scope}:previous`), backup: raw => localStorage.setItem(`${scope}:previous`, raw) },
      });
      this.sessions.set(id, session); session.subscribe(this.publish);
      if (!this.active) session.suspend();
      session.initialize({ shot, storyboard_version: storyboardVersion });
    }
    return session;
  }
  restore = (shot: ShotRead, version?: string) => { const session = this.ensure(shot, version); session.initialize({ shot, storyboard_version: version ?? '0' }); return session.getSnapshot().value!.shot; };
  get = (id: string) => this.sessions.get(id)?.getSnapshot().value;
  status = (id: string) => this.sessions.get(id)?.getSnapshot().status;
  edit = (shot: ShotRead) => this.ensure(shot).edit(shotDraftValue(shot));
  accept = (shot: ShotRead, version?: string) => this.sessions.get(shot.id)?.accept({ shot, storyboard_version: version ?? this.get(shot.id)?.storyboard_version ?? '0' });
  ids = () => [...this.sessions.keys()];
  unsettledIds = () => new Set([...this.sessions].filter(([, session]) => session.hasUnsettled()).map(([id]) => id));
  hasUnsettled = () => [...this.sessions.values()].some(session => session.hasUnsettled());
  hasMemoryOnlyChanges = () => [...this.sessions.values()].some(session => session.hasMemoryOnlyChanges());
  hasUnpersisted = this.hasMemoryOnlyChanges;
  flushShot = (id: string) => this.sessions.get(id)?.flush() ?? Promise.resolve(true);
  flush = async () => (await Promise.all([...this.sessions.values()].map(session => session.flush()))).every(Boolean);
  retry = async () => (await Promise.all([...this.sessions.values()].map(session => session.retry()))).every(Boolean);
  reload = (id: string, keepLocal = false) => this.sessions.get(id)?.reload(keepLocal) ?? Promise.resolve(null);
  exportRecovered = (id: string) => this.sessions.get(id)?.exportRecovered() ?? null;
  hasPrevious = (id: string) => this.sessions.get(id)?.hasPrevious() ?? false;
  suspend() { this.active = false; this.sessions.forEach(session => session.suspend()); }
  resume() { this.active = true; this.sessions.forEach(session => session.resume()); }
  dispose() { this.sessions.forEach(session => session.dispose()); this.listeners.clear(); }
}
