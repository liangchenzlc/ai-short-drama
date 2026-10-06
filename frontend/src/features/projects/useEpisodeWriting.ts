import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import { episodeWritingApi } from '../../api/modules/episode-writing';
import { WritingSession } from './writing-session';
import { installWritingNavigationGuard, type NavigationBarrier } from './writing-navigation';
import { confirmAction } from '../../components/ui/confirm';
import { creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery, isStoredDraft } from './creation-recovery';

export function useEpisodeWriting(projectId: string, episodeId: string, extraBarrier?: () => NavigationBarrier | null) {
  const session = useMemo(() => {
    const scope = creationScope(`writing:${projectId}:${episodeId}`);
    return new WritingSession(episodeWritingApi(projectId, episodeId), {
      read: () => {
        const stored = readRecovery(scope, isStoredDraft);
        if (!stored) return null;
        const doc = stored.document as { novel?: unknown; script?: unknown; script_id?: unknown };
        if (!doc || typeof doc.novel !== 'string' || typeof doc.script !== 'string' || !(doc.script_id === null || typeof doc.script_id === 'string' && /^[0-9]+$/.test(doc.script_id))) throw new Error('正文恢复记录无效');
        return { base_version: stored.base_version, novel: doc.novel, script: doc.script, script_id: doc.script_id };
      },
      save: ({ base_version, ...document }) => saveRecovery(scope, { format: 1, base_version, document }),
      clear: () => clearRecovery(scope),
      export: () => readRecoveryText(scope),
    });
  }, [projectId, episodeId]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const cleanup = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useEffect(() => {
    clearTimeout(cleanup.current);
    void session.load();
    // React StrictMode immediately mounts the same effect again in development.
    return () => { cleanup.current = setTimeout(() => session.dispose(), 0); };
  }, [session]);
  useLayoutEffect(() => installWritingNavigationGuard(navigator, session, window, extraBarrier, confirmAction), [navigator, session, extraBarrier]);
  return { ...state, session };
}
