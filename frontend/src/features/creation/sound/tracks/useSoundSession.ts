import {
  clearRecovery,
  creationScope,
  isStoredDraft,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { ResourceEditingSession } from '@host/features/creation/shared/sessions/resource-editing-session';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import type { StoredDraft } from '@host/types/creation/shared/recovery/creation-recovery';
import type { SoundState } from '@host/types/creation/sound/tracks';
import { useEffect, useMemo } from 'react';

import type { SoundApi } from '@host/types/creation/sound/api';
import type { SoundEdit } from '@host/types/creation/sound/editor';

// 所有声音编辑共用同一个资源会话；切换面板不会丢失未保存输入或重建保存队列。
export function useSoundSession(projectId: string, api: SoundApi) {
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous',
    sessionKey = `sound:${projectId}`;
  const recoveryScope = useMemo(() => creationScope(`sound:${projectId}:draft`, account), [account, projectId]);
  const session = useMemo(
    () =>
      retainStandardSession(
        account,
        sessionKey,
        () =>
          new ResourceEditingSession<SoundState, SoundEdit>({
            read: () => api.get(),
            save: async (base, value, assertActive) => {
              // “已核对”只对同一剪辑指纹有效；时间轴变化后必须重新核对声音与字幕。
              const body = {
                row_version: base.row_version,
                timeline_hash: base.timeline_hash,
                document: value.document,
                reviewed: value.reviewed && value.timeline_hash === base.timeline_hash,
              };
              const request_id = await requestAttempt(`${sessionKey}:save`, body, attemptStorage());
              assertActive();
              const saved = await api.save({ ...body, request_id });
              // 账号或资源切换后不能确认旧请求，未核对的请求键继续留在本机。
              assertActive();
              clearAttempt(`${sessionKey}:save`, attemptStorage());
              return saved;
            },
            version: (value) => value.row_version,
            document: (value) => ({
              document: value.document,
              reviewed: !value.needs_review,
              timeline_hash: value.timeline_hash,
            }),
            apply: (value, edit) => ({
              ...value,
              document: edit.document,
              needs_review: !edit.reviewed || edit.timeline_hash !== value.timeline_hash,
            }),
            recovery: {
              read: () => {
                const stored = readRecovery(
                  recoveryScope,
                  (value): value is StoredDraft<SoundEdit> =>
                    isStoredDraft(value) &&
                    !!value.document &&
                    typeof value.document === 'object' &&
                    !!(value.document as SoundEdit).document &&
                    Array.isArray((value.document as SoundEdit).document.dialogue) &&
                    Array.isArray((value.document as SoundEdit).document.subtitles) &&
                    typeof (value.document as SoundEdit).reviewed === 'boolean',
                );
                return stored
                  ? {
                      ...stored,
                      document: {
                        document: stored.document.document,
                        reviewed: stored.document.reviewed,
                        timeline_hash: stored.document.timeline_hash,
                      },
                    }
                  : null;
              },
              save: (value) => saveRecovery(recoveryScope, value),
              clear: () => clearRecovery(recoveryScope),
              export: () => readRecoveryText(recoveryScope),
              previous: () => readRecoveryText(`${recoveryScope}:previous`),
              backup: (raw) => localStorage.setItem(`${recoveryScope}:previous`, raw),
            },
          }),
        { label: '声音与字幕', projectId },
      ),
    [account, sessionKey, api, recoveryScope, projectId],
  );

  useEffect(() => () => releaseStandardSession(account, sessionKey), [account, sessionKey]);
  return { session, recoveryScope };
}
