import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { assemblyApi, type AssemblyClip, type AssemblyState } from '../../api/modules/assembly';
import { errorMessage } from '../../api/http';
import { useAuth } from '../auth/AuthSession';
import type { NavigationBarrier } from './writing-navigation';
import { creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery } from './creation-recovery';
import { assemblyDraft, restoreAssemblyDraft, validAssemblyDraft, type AssemblyDraftDocument } from './assembly-recovery';
import { ResourceEditingSession } from './resource-editing-session';
import { retainStandardSession, releaseStandardSession } from './standard-session-registry';

export function useAssembly(projectId: string, episodeId: string, registerBarrier: (barrier: NavigationBarrier | null) => void) {
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous';
  const key = `assembly:${projectId}:${episodeId}`;
  const api = useMemo(() => assemblyApi(projectId, episodeId), [projectId, episodeId]);
  const session = useMemo(() => retainStandardSession(account, key, () => {
    const scope = creationScope(`${key}:draft`, account);
    return new ResourceEditingSession<AssemblyState, AssemblyDraftDocument>({
      read: () => api.get(), save: (base, document) => api.save(restoreAssemblyDraft(base, { format: 1, base_version: base.assembly!.row_version, document })),
      version: value => value.assembly?.row_version ?? '0',
      document: value => value.assembly ? assemblyDraft(value).document : { assembly_id: '0', resolution: '1080p', clips: [] },
      apply: (value, document) => value.assembly ? restoreAssemblyDraft(value, { format: 1, base_version: value.assembly.row_version, document }) : value,
      recovery: { read: () => readRecovery(scope, validAssemblyDraft), save: draft => saveRecovery(scope, draft), clear: () => clearRecovery(scope), export: () => readRecoveryText(scope), previous: () => readRecoveryText(`${scope}:previous`), backup: raw => localStorage.setItem(`${scope}:previous`, raw) },
      pauseMs: 650,
    });
  }, { label: '成片剪辑', projectId, episodeId }), [account, key, api, projectId, episodeId]);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const value = snapshot.value;
  const [connectionError, setConnectionError] = useState('');
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const past = useRef<AssemblyClip[][]>([]), future = useRef<AssemblyClip[][]>([]);
  const latestLoad = useRef(0);
  async function load() {
    const request = ++latestLoad.current;
    const fresh = await session.load(true, true).catch(cause => {
      if (request !== latestLoad.current) return session.getSnapshot().value;
      throw cause;
    });
    if (!fresh) throw new Error(session.getSnapshot().message || '成片读取失败');
    if (request !== latestLoad.current) return fresh;
    session.updateMetadata(current => ({ ...current, jobs: fresh.jobs, current_work: fresh.current_work }));
    setConnectionError(''); setLastSyncedAt(Date.now()); return fresh;
  }
  function edit(transform: (value: AssemblyState) => AssemblyState, record = true) {
    const before = session.getSnapshot().value;
    if (!before?.assembly) return;
    const next = transform(before);
    if (next === before) return;
    if (record && next.clips !== before.clips) { past.current = [...past.current.slice(-99), before.clips ?? []]; future.current = []; }
    session.edit(assemblyDraft(next).document);
  }
  function undo() {
    const clips = past.current.pop(), current = session.getSnapshot().value;
    if (!clips || !current) return;
    future.current.push(current.clips ?? []); edit(before => ({ ...before, clips }), false);
  }
  function redo() {
    const clips = future.current.pop(), current = session.getSnapshot().value;
    if (!clips || !current) return;
    past.current.push(current.clips ?? []); edit(before => ({ ...before, clips }), false);
  }
  async function refreshMedia() {
    const fresh = await api.get(), sources = [...fresh.clips ?? [], ...fresh.sources ?? []];
    const hydrate = (clips: AssemblyClip[]) => clips.map(clip => {
      const source = sources.find(item => item.media_id === clip.media_id);
      return source ? { ...clip, url: source.url, poster: source.poster, filmstrip: source.filmstrip } : clip;
    });
    session.updateMetadata(current => ({ ...current, clips: hydrate(current.clips ?? []), sources: hydrate(fresh.sources ?? []), jobs: fresh.jobs, current_work: fresh.current_work }));
    past.current = past.current.map(hydrate); future.current = future.current.map(hydrate);
  }
  useEffect(() => {
    let cancelled = false, pollTimer: ReturnType<typeof setTimeout>;
    let failures = 0;
    void session.load();
    registerBarrier({ hasUnsettled: session.hasUnsettled, flush: session.flush });
    const poll = async () => {
      if (cancelled) return;
      if (session.getSnapshot().value) {
        try { await load(); failures = 0; }
        catch (cause) { failures++; if (!cancelled) setConnectionError(errorMessage(cause)); }
      }
      if (cancelled) return;
      const active = session.getSnapshot().value?.jobs?.some(job => ['queued', 'running'].includes(job.status));
      const interval = document.hidden ? 30_000 : active ? 4000 : 15_000;
      const delay = failures ? Math.max(interval, Math.min(30_000, 4000 * 2 ** Math.min(failures, 3))) : interval;
      pollTimer = setTimeout(() => void poll(), delay);
    };
    pollTimer = setTimeout(() => void poll(), 4000);
    return () => { cancelled = true; clearTimeout(pollTimer); registerBarrier(null); releaseStandardSession(account, key); };
  }, [session, account, key]);
  return {
    api, value, status: snapshot.status, error: snapshot.message, connectionError, lastSyncedAt,
    recoveredDraft: snapshot.recovered, recoveryError: snapshot.recoveryBlocked ? snapshot.message : '', recoveryBlocked: snapshot.recoveryBlocked,
    edit, flush: session.flush, load, replace: session.accept, reload: () => session.getSnapshot().loaded ? session.reload(false) : session.load(true), undo, redo, refreshMedia,
    exportRecovered: session.exportRecovered, hasPrevious: session.hasPrevious, discardRecovered: () => session.reload(false), restoreRecovered: () => session.reload(true),
    canUndo: past.current.length > 0, canRedo: future.current.length > 0,
    clearHistory: () => { past.current = []; future.current = []; }, latest: () => session.getSnapshot().value,
    retrySave: session.retry,
  };
}
