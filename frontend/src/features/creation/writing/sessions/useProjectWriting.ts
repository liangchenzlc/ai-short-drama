import { projectWritingApi } from '@host/api/creation/writing/documents';
import {
  clearRecovery,
  creationScope,
  isStoredDraft,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { installWritingNavigationGuard } from '@host/features/creation/writing/sessions/writing-navigation';
import { WritingSession } from '@host/features/creation/writing/sessions/writing-session';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { confirmAction } from '@host/shared/ui/confirm';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { WritingDraft } from '@host/types/creation/writing/sessions/writing-session';
import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from 'react';
import { UNSAFE_NavigationContext } from 'react-router-dom';
export function useProjectWriting(projectId: string, extraBarrier?: () => NavigationBarrier | null) {
  const { user, enabled } = useAuth();
  const actor = user?.id ?? (enabled ? 'signed-out' : 'anonymous');
  const resourceKey = `writing:${projectId}`;
  const session = useMemo(() => {
    const scope = creationScope(resourceKey, actor);
    return retainStandardSession(
      actor,
      resourceKey,
      () =>
        new WritingSession(projectWritingApi(projectId), {
          read: () => {
            const stored = readRecovery(scope, isStoredDraft);
            if (!stored) return null;
            const doc = stored.document as Partial<WritingDraft>;
            if (
              !doc ||
              typeof doc.script !== 'string' ||
              !(doc.script_id === null || (typeof doc.script_id === 'string' && /^[0-9]+$/.test(doc.script_id)))
            )
              throw new Error('正文恢复记录无效');
            if (
              doc.pending &&
              (!['script'].includes(doc.pending.field) ||
                typeof doc.pending.content !== 'string' ||
                !doc.pending.before ||
                !/^\d+$/.test(doc.pending.before.content_version))
            )
              throw new Error('正文保存回执恢复记录无效');
            if (doc.script_change_source !== undefined && doc.script_change_source !== 'import')
              throw new Error('剧本变更来源恢复记录无效');
            return {
              base_version: stored.base_version,
              script: doc.script,
              script_id: doc.script_id,
              pending: doc.pending ?? null,
              script_change_source: doc.script_change_source,
            };
          },
          save: ({ base_version, ...document }) => saveRecovery(scope, { format: 1, base_version, document }),
          clear: () => clearRecovery(scope),
          export: () => readRecoveryText(scope),
        }),
      { label: '剧本', projectId },
    );
  }, [projectId, actor, resourceKey]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { navigator } = useContext(UNSAFE_NavigationContext);
  useEffect(() => {
    void session.load(true);
    return () => releaseStandardSession(actor, resourceKey);
  }, [session, actor, resourceKey]);
  useLayoutEffect(
    () => installWritingNavigationGuard(navigator, session, window, extraBarrier, confirmAction),
    [navigator, session, extraBarrier],
  );
  return { ...state, session };
}
