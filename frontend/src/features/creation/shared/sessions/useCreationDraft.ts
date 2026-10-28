import { creationDraftsApi } from '@host/api/creation/drafts/requests';
import {
  clearRecovery,
  creationScope,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { CreationDraftSession } from '@host/features/creation/shared/sessions/creation-draft-session';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { useAuth } from '@host/features/identity/session/AuthSession';
import type { CreationDraftValue, CreationPurpose } from '@host/types/creation/drafts/requests';
import type { DraftRecovery } from '@host/types/creation/shared/sessions/creation-draft-session';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
const initial: CreationDraftValue = {
  model_config_id: null,
  input_text: '',
  image_settings: null,
  video_settings: null,
  reference_media_ids: [],
};
function isRecovery(value: unknown): value is DraftRecovery {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<DraftRecovery>;
  const doc = item.value;
  return (
    (item.row_version === null || (typeof item.row_version === 'string' && /^\d+$/.test(item.row_version))) &&
    !!doc &&
    typeof doc.input_text === 'string' &&
    (doc.model_config_id === null || (typeof doc.model_config_id === 'string' && /^\d+$/.test(doc.model_config_id))) &&
    Array.isArray(doc.reference_media_ids) &&
    doc.reference_media_ids.every((id) => typeof id === 'string' && /^\d+$/.test(id)) &&
    (!item.pending ||
      isRecovery({
        row_version: item.pending.version,
        value: item.pending.value,
      }))
  );
}
export function useCreationDraft({
  projectId,
  objectId,
  purpose,
  initialValue,
  readOnly = false,
  registerBarrier,
}: {
  projectId: string;
  objectId: string;
  purpose: CreationPurpose;
  sourceVersion?: string;
  initialValue?: Partial<CreationDraftValue>;
  readOnly?: boolean;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const { user, enabled } = useAuth();
  const actor = user?.id ?? (enabled ? 'signed-out' : 'anonymous');
  const resourceKey = `generation-draft:${projectId}:${objectId}:${purpose}`;
  const seed = useRef(initialValue);
  seed.current = initialValue;
  const session = useMemo(() => {
    const scope = creationScope(resourceKey, actor);
    return retainStandardSession(
      actor,
      resourceKey,
      () =>
        new CreationDraftSession(
          creationDraftsApi(projectId, objectId, purpose),
          {
            read: () => readRecovery(scope, isRecovery),
            save: (value) => saveRecovery(scope, value),
            clear: () => clearRecovery(scope),
            backup: (value) =>
              saveRecovery(`${scope}:previous`, {
                current: value,
                recovery: readRecoveryText(scope),
              }),
          },
          { ...initial, ...seed.current },
        ),
      { label: '生成设置', projectId },
    );
  }, [projectId, objectId, purpose, actor, resourceKey]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    void session.load();
    return () => releaseStandardSession(actor, resourceKey);
  }, [session, actor, resourceKey]);
  useEffect(() => {
    if (!registerBarrier) return;
    registerBarrier({
      hasUnsettled: session.hasUnsettled,
      hasMemoryOnlyChanges: session.hasMemoryOnlyChanges,
      flush: async () => !!(await session.flush()),
    });
    return () => registerBarrier(null);
  }, [session, registerBarrier]);
  useEffect(() => {
    const key = `${projectId}:${objectId}:${purpose}`;
    window.dispatchEvent(
      new CustomEvent('creation-save-state', {
        detail: { key, status: state.status, message: state.message },
      }),
    );
  }, [projectId, objectId, purpose, state.status, state.message]);
  useEffect(
    () => () => {
      window.dispatchEvent(
        new CustomEvent('creation-save-state', {
          detail: { key: `${projectId}:${objectId}:${purpose}`, status: null },
        }),
      );
    },
    [projectId, objectId, purpose],
  );
  return {
    ...state,
    session,
    edit: (patch: Partial<CreationDraftValue>) => {
      if (!readOnly) session.edit(patch);
    },
    flush: session.flush,
    retry: session.retry,
    reload: () => session.load(true),
  };
}
