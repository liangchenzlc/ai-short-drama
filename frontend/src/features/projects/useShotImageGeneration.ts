import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../../api/http';
import { generations } from '../../api/modules/generations';
import { mediaLibrary } from '../../api/modules/media-library';
import { createShotImageHistory, emptyShotImageHistory } from './shot-image-history';

export function useShotImageGeneration({ shotId, enabled }: { shotId: string; enabled: boolean }) {
  const [snapshot, setSnapshot] = useState(() => ({ shotId, ...emptyShotImageHistory() }));
  const history = useRef<ReturnType<typeof createShotImageHistory> | null>(null);
  const current = useRef({ shotId, enabled });
  current.current = { shotId, enabled };

  useEffect(() => {
    let alive = true;
    const session = createShotImageHistory({
      shotId,
      api: { listTasks: generations.list, listCandidates: mediaLibrary.list, detail: generations.detail },
      describeError: errorMessage,
      onChange: state => {
        if (alive && current.current.shotId === shotId) setSnapshot({ shotId, ...state });
      },
    });
    history.current = session;
    setSnapshot({ shotId, ...emptyShotImageHistory() });
    return () => {
      alive = false;
      session.stop();
      if (history.current === session) history.current = null;
    };
  }, [shotId]);

  useEffect(() => {
    const session = history.current;
    if (!session || !enabled) return;
    const resume = () => {
      if (document.visibilityState === 'hidden') session.stop();
      else void session.start();
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('focus', resume);
    resume();
    return () => {
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('focus', resume);
      session.stop();
    };
  }, [shotId, enabled]);

  const invoke = useCallback((operation: 'refresh' | 'loadMoreTasks' | 'loadMoreCandidates') => {
    if (!current.current.enabled || document.visibilityState === 'hidden') return Promise.resolve();
    return history.current?.[operation]() ?? Promise.resolve();
  }, []);
  const refresh = useCallback(() => invoke('refresh'), [invoke]);
  const loadMoreTasks = useCallback(() => invoke('loadMoreTasks'), [invoke]);
  const loadMoreCandidates = useCallback(() => invoke('loadMoreCandidates'), [invoke]);
  const state = snapshot.shotId === shotId ? snapshot : emptyShotImageHistory();

  return {
    tasks: state.tasks, candidates: state.candidates, loading: state.loading, error: state.error,
    hasMoreTasks: state.hasMoreTasks, hasMoreCandidates: state.hasMoreCandidates,
    refresh, loadMoreTasks, loadMoreCandidates,
  };
}
