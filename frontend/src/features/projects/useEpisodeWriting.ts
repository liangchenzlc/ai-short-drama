import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import { episodeWritingApi } from '../../api/modules/episode-writing';
import { WritingSession } from './writing-session';
import { installWritingNavigationGuard, type NavigationBarrier } from './writing-navigation';
import { confirmAction } from '../../components/ui/confirm';
import { creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery, isStoredDraft } from './creation-recovery';
import { documentText, isEditorDocument } from './script-document';
import { useAuth } from '../auth/AuthSession';

export function useEpisodeWriting(projectId: string, episodeId: string, extraBarrier?: () => NavigationBarrier | null) {
  const { user } = useAuth();
  const session = useMemo(() => {
    const scope = creationScope(`writing:${projectId}:${episodeId}`);
    return new WritingSession(episodeWritingApi(projectId, episodeId), {
      read: () => {
        const stored = readRecovery(scope, isStoredDraft);
        if (!stored) return null;
        const doc = stored.document as { novel?: unknown; script?: unknown; script_id?: unknown; editor_document?: unknown };
        if (!doc || typeof doc.novel !== 'string' || typeof doc.script !== 'string' || !(doc.script_id === null || typeof doc.script_id === 'string' && /^[0-9]+$/.test(doc.script_id))) throw new Error('正文恢复记录无效');
        if (doc.editor_document != null && (!isEditorDocument(doc.editor_document) || documentText(doc.editor_document) !== doc.script)) throw new Error('剧本格式恢复记录无效');
        return { base_version: stored.base_version, novel: doc.novel, script: doc.script, script_id: doc.script_id, editor_document: doc.editor_document ?? null };
      },
      save: ({ base_version, ...document }) => saveRecovery(scope, { format: 1, base_version, document }),
      clear: () => clearRecovery(scope),
      export: () => readRecoveryText(scope),
    });
  }, [projectId, episodeId, user?.id]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const cleanup = useRef<{ session: WritingSession; timer: ReturnType<typeof setTimeout> } | null>(null);
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useEffect(() => {
    if (cleanup.current?.session === session) clearTimeout(cleanup.current.timer);
    void session.load();
    // React StrictMode immediately mounts the same effect again in development.
    return () => { cleanup.current = { session, timer: setTimeout(() => session.dispose(), 0) }; };
  }, [session]);
  useLayoutEffect(() => installWritingNavigationGuard(navigator, session, window, extraBarrier, confirmAction), [navigator, session, extraBarrier]);
  return { ...state, session };
}
