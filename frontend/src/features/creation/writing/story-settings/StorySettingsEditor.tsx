import { storySettingsApi } from '@host/api/creation/writing/documents';
import {
  clearRecovery,
  creationScope,
  readRecovery,
  readRecoveryText,
  saveRecovery,
} from '@host/features/creation/shared/recovery/creation-recovery';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { ResourceEditingSession } from '@host/features/creation/shared/sessions/resource-editing-session';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { ResourceRecovery } from '@host/types/creation/shared/sessions/resource-editing-session';
import type { StorySettings, StorySettingsDocument } from '@host/types/creation/writing/documents';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { Field } from '@host/types/creation/writing/story-settings/StorySettingsEditor';
import { Button, Input, Skeleton } from 'antd';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
const fields = [
  { key: 'logline', label: '一句话卖点', limit: 200, rows: 1 },
  { key: 'synopsis', label: '故事梗概', limit: 20000, rows: 5 },
  { key: 'genre', label: '故事类型', limit: 100, rows: 1 },
  { key: 'character_bios', label: '人物小传', limit: 20000, rows: 6 },
] as const;
const document = ({ logline, synopsis, genre, character_bios }: StorySettingsDocument): StorySettingsDocument => ({
  logline,
  synopsis,
  genre,
  character_bios,
});
function isRecovery(value: unknown): value is ResourceRecovery<StorySettingsDocument> {
  if (!value || typeof value !== 'object') return false;
  const item = value as ResourceRecovery<StorySettingsDocument>;
  const validVersion = (version: unknown) => typeof version === 'string' && /^\d+$/.test(version);
  const validDocument = (doc: StorySettingsDocument | undefined) =>
    !!doc && fields.every((field) => typeof doc[field.key] === 'string');
  return (
    item.format === 1 &&
    validVersion(item.base_version) &&
    validDocument(item.document) &&
    (!item.pending || (validVersion(item.pending.base_version) && validDocument(item.pending.document)))
  );
}

export function StorySettingsEditor({
  projectId,
  readOnly,
  registerBarrier,
}: {
  projectId: string;
  readOnly: boolean;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
}) {
  const { user, enabled } = useAuth();
  const actor = user?.id ?? (enabled ? 'signed-out' : 'anonymous');
  const resourceKey = `story-settings:${projectId}`;
  const session = useMemo(() => {
    const scope = creationScope(resourceKey, actor),
      api = storySettingsApi(projectId);
    return retainStandardSession(
      actor,
      resourceKey,
      () =>
        new ResourceEditingSession<StorySettings, StorySettingsDocument>({
          read: api.get,
          save: (resource, doc, assertActive) => {
            assertActive();
            return api.save({ ...doc, row_version: resource.row_version });
          },
          version: (resource) => resource.row_version,
          document,
          apply: (resource, doc) => ({ ...resource, ...doc }),
          recovery: {
            read: () => readRecovery(scope, isRecovery),
            save: (value) => saveRecovery(scope, value),
            clear: () => clearRecovery(scope),
            export: () => readRecoveryText(scope),
            previous: () => readRecoveryText(`${scope}:previous`),
            backup: (raw) => saveRecovery(`${scope}:previous`, { raw }),
          },
        }),
      { projectId, label: '故事设定' },
    );
  }, [actor, projectId, resourceKey]);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [error, setError] = useState('');
  const composing = useRef<Field | null>(null);
  const [composition, setComposition] = useState<{
    field: Field;
    text: string;
  } | null>(null);
  useEffect(() => {
    void session.load();
    registerBarrier(session);
    return () => {
      registerBarrier(null);
      releaseStandardSession(actor, resourceKey);
    };
  }, [session, registerBarrier, actor, resourceKey]);
  function edit(field: Field, text: string) {
    const value = session.getSnapshot().value;
    if (value && !readOnly && !state.recoveryBlocked) session.edit({ ...document(value), [field]: text });
  }
  function finish(field: Field, text: string) {
    if (composing.current !== field) return;
    composing.current = null;
    setComposition(null);
    edit(field, text);
  }
  async function reload(keepLocal: boolean) {
    try {
      setError('');
      await session.reload(keepLocal);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '故事设定读取失败，请重试。');
    }
  }
  function download() {
    try {
      const raw = session.exportRecovered() ?? JSON.stringify(state.value);
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = window.document.createElement('a');
      link.href = url;
      link.download = `project-${projectId}-story-settings.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '故事设定备份失败。');
    }
  }
  return (
    <div className="sd-settings-scroll">
      {(state.message || error) && (
        <StudioFeedback
          type={state.status === 'conflict' || state.recoveryBlocked ? 'warning' : 'error'}
          title="故事设定保存"
          message={error || state.message}
          noticeKey={`story-settings:${projectId}`}
          actions={
            <>
              <Button size="small" onClick={download}>
                下载本机设定
              </Button>
              {state.status === 'error' && !state.recoveryBlocked && (
                <Button size="small" disabled={state.busy} onClick={() => void session.retry()}>
                  重试
                </Button>
              )}
              {state.loaded && (
                <>
                  <Button
                    size="small"
                    disabled={state.busy || state.recoveryBlocked || readOnly}
                    onClick={() => void reload(true)}
                  >
                    核对后保留本机设定
                  </Button>
                  <Button size="small" disabled={state.busy} onClick={() => void reload(false)}>
                    使用服务端设定
                  </Button>
                </>
              )}
            </>
          }
        />
      )}
      {!state.loaded ? (
        <div role="status" aria-label="载入故事设定">
          <Skeleton active paragraph={{ rows: 5 }} />
        </div>
      ) : (
        <div className="sd-story-settings">
          {fields.map((field) => (
            <label className="sd-story-field" key={field.key}>
              <span>{field.label}</span>
              <Input.TextArea
                aria-label={field.label}
                value={composition?.field === field.key ? composition.text : (state.value?.[field.key] ?? '')}
                autoSize={{ minRows: field.rows }}
                maxLength={field.limit}
                readOnly={readOnly || state.recoveryBlocked}
                onCompositionStart={(event) => {
                  composing.current = field.key;
                  setComposition({
                    field: field.key,
                    text: event.currentTarget.value,
                  });
                }}
                onCompositionEnd={(event) => finish(field.key, event.currentTarget.value)}
                onBlur={(event) => finish(field.key, event.currentTarget.value)}
                onChange={(event) => {
                  if (composing.current === field.key)
                    setComposition({
                      field: field.key,
                      text: event.target.value,
                    });
                  else edit(field.key, event.target.value);
                }}
              />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
