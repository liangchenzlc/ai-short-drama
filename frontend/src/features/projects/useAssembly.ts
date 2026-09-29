import { useEffect, useMemo, useRef, useState } from 'react';
import { assemblyApi, type AssemblyClip, type AssemblyState } from '../../api/modules/assembly';
import { errorMessage } from '../../api/http';
import type { NavigationBarrier } from './writing-navigation';

export function useAssembly(projectId: string, episodeId: string, registerBarrier: (barrier: NavigationBarrier | null) => void) {
  const api = useMemo(() => assemblyApi(projectId, episodeId), [projectId, episodeId]);
  const [value, setValue] = useState<AssemblyState | null>(null);
  const [status, setStatus] = useState<'loading' | 'saved' | 'unsaved' | 'saving' | 'error'>('loading');
  const [error, setError] = useState('');
  const past = useRef<AssemblyClip[][]>([]);
  const future = useRef<AssemblyClip[][]>([]);
  const state = useRef({ value: null as AssemblyState | null, revision: 0, saved: 0, paused: false, mounted: true });
  const pending = useRef<Promise<boolean> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const register = useRef(registerBarrier); register.current = registerBarrier;
  function replace(next: AssemblyState) { state.current.value = next; if (state.current.mounted) setValue(next); }
  async function load() {
    const revision = state.current.revision;
    const before = state.current.value;
    const next = await api.get();
    if (!state.current.mounted) return next;
    if (state.current.value === before && state.current.revision === revision && state.current.saved === revision && !pending.current) replace(next);
    return next;
  }
  function flush(): Promise<boolean> {
    if (timer.current) clearTimeout(timer.current);
    if (pending.current) return pending.current;
    if (state.current.paused) return Promise.resolve(false);
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
    if (!state.current.value?.assembly) return;
    const before = state.current.value;
    const next = transform(before);
    if (next === before) return;
    if (record && next.clips !== before.clips) {
      past.current = [...past.current.slice(-99), before.clips ?? []]; future.current = [];
    }
    replace(next); state.current.revision++;
    setStatus(state.current.paused ? 'error' : 'unsaved');
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flushRef.current(), 650);
  }
  async function reload() {
    if (pending.current) await pending.current;
    const next = await api.get();
    state.current.saved = state.current.revision; state.current.paused = false;
    past.current = []; future.current = [];
    replace(next); setError(''); setStatus('saved');
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
      sources: hydrate(state.current.value.sources ?? []), jobs: fresh.jobs });
    past.current = past.current.map(hydrate); future.current = future.current.map(hydrate);
  }
  useEffect(() => {
    state.current.mounted = true;
    let cancelled = false;
    const initial = async () => { try { const next = await api.get(); if (!cancelled) { replace(next); setStatus('saved'); } } catch (cause) { if (!cancelled) { setError(errorMessage(cause)); setStatus('error'); } } };
    void initial();
    register.current({ hasUnsettled: () => state.current.revision !== state.current.saved || !!pending.current, flush: () => flushRef.current() });
    const interval = setInterval(() => { if (!state.current.paused && state.current.saved === state.current.revision && !pending.current) void load().catch(() => {}); }, 4000);
    return () => { cancelled = true; state.current.mounted = false; clearInterval(interval); if (timer.current) clearTimeout(timer.current); register.current(null); };
  }, [api]); // project/episode remount owns this editing session
  return { api, value, status, error, edit, flush, load, replace, reload, undo, redo, refreshMedia,
    canUndo: past.current.length > 0, canRedo: future.current.length > 0,
    clearHistory: () => { past.current = []; future.current = []; }, latest: () => state.current.value,
    retrySave: async () => { state.current.paused = false; return flush(); } };
}
