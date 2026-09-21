import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import { episodeWritingApi } from '../../api/modules/episode-writing';
import { WritingSession } from './writing-session';
import { installWritingNavigationGuard, type NavigationBarrier } from './writing-navigation';

export function useEpisodeWriting(projectId: string, episodeId: string, extraBarrier?: () => NavigationBarrier | null) {
  const session = useMemo(() => new WritingSession(episodeWritingApi(projectId, episodeId)), [projectId, episodeId]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const cleanup = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useEffect(() => {
    clearTimeout(cleanup.current);
    void session.load();
    // React StrictMode immediately mounts the same effect again in development.
    return () => { cleanup.current = setTimeout(() => session.dispose(), 0); };
  }, [session]);
  useLayoutEffect(() => installWritingNavigationGuard(navigator, session, window, extraBarrier), [navigator, session, extraBarrier]);
  return { ...state, session };
}
