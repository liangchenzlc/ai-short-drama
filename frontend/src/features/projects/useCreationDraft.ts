import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { creationDraftsApi, type CreationDraftValue, type CreationPurpose } from '../../api/modules/creation-drafts';
import { useAuth } from '../auth/AuthSession';
import { CreationDraftSession, type DraftRecovery } from './creation-draft-session';
import { clearRecovery, creationScope, readRecovery, saveRecovery } from './creation-recovery';
import type { NavigationBarrier } from './writing-navigation';

const initial: CreationDraftValue = { model_config_id: null, input_text: '', image_settings: null, video_settings: null, reference_media_ids: [] };
function isRecovery(value: unknown): value is DraftRecovery {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<DraftRecovery>;
  const doc = item.value;
  return (item.row_version === null || typeof item.row_version === 'string' && /^\d+$/.test(item.row_version))
    && !!doc && typeof doc.input_text === 'string'
    && (doc.model_config_id === null || typeof doc.model_config_id === 'string' && /^\d+$/.test(doc.model_config_id))
    && Array.isArray(doc.reference_media_ids) && doc.reference_media_ids.every(id => typeof id === 'string' && /^\d+$/.test(id));
}
export function useCreationDraft({ projectId, episodeId, objectId, purpose, initialValue, readOnly = false, registerBarrier }: {
  projectId: string; episodeId: string; objectId: string; purpose: CreationPurpose;
  sourceVersion?: string; initialValue?: Partial<CreationDraftValue>; readOnly?: boolean;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  const { user } = useAuth();
  const seed = useRef(initialValue); seed.current = initialValue;
  const session = useMemo(() => {
    const scope = creationScope(`generation-draft:${projectId}:${episodeId}:${objectId}:${purpose}`);
    return new CreationDraftSession(creationDraftsApi(projectId, episodeId, objectId, purpose), {
      read: () => readRecovery(scope, isRecovery), save: value => saveRecovery(scope, value), clear: () => clearRecovery(scope),
    }, { ...initial, ...seed.current });
  }, [projectId, episodeId, objectId, purpose, user?.id]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const cleanup = useRef<{ session: CreationDraftSession; timer: ReturnType<typeof setTimeout> } | undefined>(undefined);
  useEffect(() => {
    if (cleanup.current?.session === session) clearTimeout(cleanup.current.timer);
    void session.load();
    return () => { cleanup.current = { session, timer: setTimeout(() => session.dispose(), 0) }; };
  }, [session]);
  useEffect(() => {
    if (!registerBarrier) return;
    registerBarrier({ hasUnsettled: session.hasUnsettled, flush: async () => !!await session.flush() });
    return () => registerBarrier(null);
  }, [session, registerBarrier]);
  useEffect(() => {
    const key = `${projectId}:${episodeId}:${objectId}:${purpose}`;
    window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key, status: state.status, message: state.message } }));
  }, [projectId, episodeId, objectId, purpose, state.status, state.message]);
  useEffect(() => () => {
    window.dispatchEvent(new CustomEvent('creation-save-state', { detail: { key: `${projectId}:${episodeId}:${objectId}:${purpose}`, status: null } }));
  }, [projectId, episodeId, objectId, purpose]);
  return { ...state, session, edit: (patch: Partial<CreationDraftValue>) => { if (!readOnly) session.edit(patch); },
    flush: session.flush, retry: session.retry, reload: () => session.load(true) };
}
