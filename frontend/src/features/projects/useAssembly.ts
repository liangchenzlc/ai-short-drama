import { useEffect, useMemo, useRef, useState } from 'react';
import { assemblyApi, type AssemblyClip, type AssemblyState } from '../../api/modules/assembly';
import { errorMessage } from '../../api/http';
import type { NavigationBarrier } from './writing-navigation';
import { creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery, type StoredDraft } from './creation-recovery';
import { assemblyDraft, restoreAssemblyDraft, validAssemblyDraft, type AssemblyDraftDocument } from './assembly-recovery';

export function useAssembly(projectId: string, episodeId: string, registerBarrier: (barrier: NavigationBarrier | null) => void) {
  const api = useMemo(() => assemblyApi(projectId, episodeId), [projectId, episodeId]);
  const recoveryScope = useMemo(() => creationScope(`assembly:${projectId}:${episodeId}:draft`), [projectId, episodeId]);
  const [recoveredDraft, setRecoveredDraft] = useState<StoredDraft<AssemblyDraftDocument> | null>(null);
  const [recoveryError, setRecoveryError] = useState('');
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const recoveryPending = useRef(false);
  function offerRecovery(next: AssemblyState) {
    try {
      const draft = readRecovery(recoveryScope, validAssemblyDraft);
      if (draft && next.assembly && JSON.stringify(draft.document) !== JSON.stringify(assemblyDraft(next).document)) {
        recoveryPending.current = true; setRecoveredDraft(draft);
      } else if (draft) clearRecovery(recoveryScope);
    } catch {
      recoveryPending.current = true; setRecoveryBlocked(true);
      setRecoveryError('剪辑恢复记录无法读取，请下载完整恢复记录核对，明确放弃后才能继续编辑。');
    }
  }
  function persistDraft() {
    if (!state.current.value?.assembly || recoveryPending.current) return;
    try {
      if (state.current.saved !== state.current.revision) saveRecovery(recoveryScope, assemblyDraft(state.current.value));
      else clearRecovery(recoveryScope);
      setRecoveryError('');
    } catch { setRecoveryError('本机剪辑恢复记录无法保存，请下载草稿备份。'); }
  }
  const [value, setValue] = useState<AssemblyState | null>(null);
  const [status, setStatus] = useState<'loading' | 'saved' | 'unsaved' | 'saving' | 'error'>('loading');
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const past = useRef<AssemblyClip[][]>([]);
  const future = useRef<AssemblyClip[][]>([]);
  const state = useRef({ value: null as AssemblyState | null, revision: 0, saved: 0, paused: false, mounted: true });
  const pending = useRef<Promise<boolean> | null>(null);
  const latestLoad = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const register = useRef(registerBarrier); register.current = registerBarrier;
  function replace(next: AssemblyState) { state.current.value = next; if (state.current.mounted) setValue(next); }
  async function load() {
    const request = ++latestLoad.current;
    const revision = state.current.revision;
    const before = state.current.value;
    const next = await api.get();
    if (!state.current.mounted || request !== latestLoad.current) return next;
    setConnectionError(''); setLastSyncedAt(Date.now());
    if (state.current.value === before && state.current.revision === revision && state.current.saved === revision && !pending.current) replace(next);
    else if (state.current.value?.assembly?.id === next.assembly?.id && state.current.value) {
      // Render progress is independent of unsaved clip edits; never hide a finished
      // or failed export just because a draft save is paused.
      replace({ ...state.current.value, jobs: next.jobs, current_work: next.current_work });
    }
    return next;
  }
  function flush(): Promise<boolean> {
    if (timer.current) clearTimeout(timer.current);
    if (pending.current) return pending.current;
    if (!state.current.value) return Promise.resolve(false);
    if (state.current.paused) return Promise.resolve(false);
    if (recoveryPending.current) return Promise.resolve(false);
    const run = async () => {
      while (state.current.saved !== state.current.revision) {
        const revision = state.current.revision;
        const current = state.current.value!;
        if (state.current.mounted) setStatus('saving');
        try {
          const saved = await api.save(current);
          state.current.saved = revision;
          const latest = state.current.value!;
          replace(revision === state.current.revision ? saved : { ...latest, assembly: { ...latest.assembly!, row_version: saved.assembly!.row_version } });
          persistDraft();
        } catch (cause) {
          state.current.paused = true;
          if (state.current.mounted) { setError(errorMessage(cause)); setStatus('error'); }
          return false;
        }
      }
      if (state.current.mounted) { setStatus('saved'); setError(''); }
      return true;
    };
    pending.current = run().finally(() => { pending.current = null; });
    return pending.current;
  }
  const flushRef = useRef(flush); flushRef.current = flush;
  function edit(transform: (value: AssemblyState) => AssemblyState, record = true) {
    if (!state.current.value?.assembly || recoveryPending.current) return;
    const before = state.current.value;
    const next = transform(before);
    if (next === before) return;
    if (record && next.clips !== before.clips) {
      past.current = [...past.current.slice(-99), before.clips ?? []]; future.current = [];
    }
    replace(next); state.current.revision++;
    persistDraft();
    setStatus(state.current.paused ? 'error' : 'unsaved');
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flushRef.current(), 650);
  }
  async function reload() {
    if (pending.current) await pending.current;
    const initialLoad = !state.current.value;
    if (!state.current.value && state.current.mounted) setStatus('loading');
    try {
      const next = await api.get();
      if (!state.current.mounted) return;
      state.current.saved = state.current.revision; state.current.paused = false;
      past.current = []; future.current = [];
      replace(next); setError(''); setStatus('saved');
      if (initialLoad) offerRecovery(next);
      else { recoveryPending.current = false; setRecoveredDraft(null); setRecoveryBlocked(false); persistDraft(); }
    } catch (cause) {
      if (state.current.mounted) { setError(errorMessage(cause)); setStatus('error'); }
      throw cause;
    }
  }
  function undo() {
    const clips = past.current.pop();
    if (!clips || !state.current.value) return;
    future.current.push(state.current.value.clips ?? []);
    edit(current => ({ ...current, clips }), false);
  }
  function redo() {
    const clips = future.current.pop();
    if (!clips || !state.current.value) return;
    past.current.push(state.current.value.clips ?? []);
    edit(current => ({ ...current, clips }), false);
  }
  async function refreshMedia() {
    const fresh = await api.get();
    if (!state.current.mounted || !state.current.value) return;
    const sources = [...fresh.clips ?? [], ...fresh.sources ?? []];
    const hydrate = (clips: AssemblyClip[]) => clips.map(c => {
      const match = sources.find(s => s.media_id === c.media_id);
      return match ? { ...c, url: match.url, poster: match.poster, filmstrip: match.filmstrip } : c;
    });
    replace({ ...state.current.value, clips: hydrate(state.current.value.clips ?? []),
      sources: hydrate(state.current.value.sources ?? []), jobs: fresh.jobs, current_work: fresh.current_work });
    past.current = past.current.map(hydrate); future.current = future.current.map(hydrate);
  }
  useEffect(() => {
    state.current.mounted = true;
    let cancelled = false;
    const initial = async () => { try { const next = await api.get(); if (!cancelled) {
      replace(next); setStatus('saved'); setLastSyncedAt(Date.now());
      offerRecovery(next);
    } } catch (cause) { if (!cancelled) { setError(errorMessage(cause)); setStatus('error'); } } };
    void initial();
    register.current({ hasUnsettled: () => state.current.revision !== state.current.saved || !!pending.current || recoveryPending.current, flush: () => flushRef.current() });
    let failures = 0;
    let pollTimer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (cancelled) return;
      if (state.current.value) {
        try { await load(); failures = 0; }
        catch (cause) { failures++; if (!cancelled) setConnectionError(errorMessage(cause)); }
      }
      if (cancelled) return;
      const active = state.current.value?.jobs?.some(job => ['queued', 'running'].includes(job.status));
      const interval = document.hidden ? 30000 : active ? 4000 : 15000;
      const delay = failures ? Math.max(interval, Math.min(30000, 4000 * 2 ** Math.min(failures, 3))) : interval;
      pollTimer = setTimeout(() => void poll(), delay);
    };
    pollTimer = setTimeout(() => void poll(), 4000);
    return () => { cancelled = true; state.current.mounted = false; clearTimeout(pollTimer); if (timer.current) clearTimeout(timer.current); register.current(null); };
  }, [api]); // project/episode remount owns this editing session
  return { api, value, status, error, connectionError, lastSyncedAt, recoveredDraft, recoveryError, recoveryBlocked, edit, flush, load, replace, reload, undo, redo, refreshMedia,
    exportRecovered: () => readRecoveryText(recoveryScope),
    discardRecovered: () => { clearRecovery(recoveryScope); recoveryPending.current = false; setRecoveredDraft(null); setRecoveryBlocked(false); setRecoveryError(''); },
    restoreRecovered: async () => {
      if (!recoveredDraft || !state.current.value?.assembly) return;
      const fresh = await api.get();
      if (!state.current.mounted) return;
      if (fresh.assembly?.id !== recoveredDraft.document.assembly_id) throw new Error('恢复稿属于另一份成片草稿，请先下载恢复稿核对。');
      replace(fresh); recoveryPending.current = false; state.current.paused = false; setRecoveredDraft(null);
      edit(current => restoreAssemblyDraft(current, recoveredDraft));
    },
    canUndo: past.current.length > 0, canRedo: future.current.length > 0,
    clearHistory: () => { past.current = []; future.current = []; }, latest: () => state.current.value,
    retrySave: async () => {
      if (!state.current.value) { try { await reload(); return true; } catch { return false; } }
      state.current.paused = false; return flush();
    } };
}
