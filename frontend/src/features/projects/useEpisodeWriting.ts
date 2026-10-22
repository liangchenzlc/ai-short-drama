import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
import { episodeWritingApi } from '../../api/modules/episode-writing';
import { WritingSession, type WritingDraft } from './writing-session';
import { releaseStandardSession, retainStandardSession } from './standard-session-registry';
import { installWritingNavigationGuard, type NavigationBarrier } from './writing-navigation';
import { confirmAction } from '../../components/ui/confirm';
import { creationScope, readRecovery, readRecoveryText, saveRecovery, clearRecovery, isStoredDraft } from './creation-recovery';
import { documentText, isEditorDocument } from './script-document';
import { useAuth } from '../auth/AuthSession';

export function useEpisodeWriting(projectId: string, extraBarrier?: () => NavigationBarrier | null) {
  const { user, enabled } = useAuth();
  const actor = user?.id ?? (enabled ? 'signed-out' : 'anonymous');
  const resourceKey = `writing:${projectId}`;
  const session = useMemo(() => {
    const scope = creationScope(resourceKey, actor);
    return retainStandardSession(actor, resourceKey, () => new WritingSession(episodeWritingApi(projectId), {
      read: () => {
        const stored = readRecovery(scope, isStoredDraft);
        if (!stored) return null;
        const doc = stored.document as Partial<WritingDraft>;
        if (!doc || typeof doc.novel !== 'string' || typeof doc.script !== 'string' || !(doc.script_id === null || typeof doc.script_id === 'string' && /^[0-9]+$/.test(doc.script_id))) throw new Error('正文恢复记录无效');
        if (doc.editor_document != null && (!isEditorDocument(doc.editor_document) || documentText(doc.editor_document) !== doc.script)) throw new Error('剧本格式恢复记录无效');
        if (doc.pending && (!['novel', 'script'].includes(doc.pending.field) || typeof doc.pending.content !== 'string' || !doc.pending.before || !/^\d+$/.test(doc.pending.before.content_version))) throw new Error('正文保存回执恢复记录无效');
        return { base_version: stored.base_version, novel: doc.novel, script: doc.script, script_id: doc.script_id, editor_document: doc.editor_document ?? null, pending: doc.pending ?? null };
      },
      save: ({ base_version, ...document }) => saveRecovery(scope, { format: 1, base_version, document }),
      clear: () => clearRecovery(scope),
      export: () => readRecoveryText(scope),
    }), { label: '剧本', projectId });
  }, [projectId, actor, resourceKey]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useEffect(() => {
    void session.load(true);
    return () => releaseStandardSession(actor, resourceKey);
  }, [session, actor, resourceKey]);
  useLayoutEffect(() => installWritingNavigationGuard(navigator, session, window, extraBarrier, confirmAction), [navigator, session, extraBarrier]);
  return { ...state, session };
}
