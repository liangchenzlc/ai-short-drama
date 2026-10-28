import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { skillsApi } from '@host/api/skills/catalog';
import { ComposerAttachmentsContext } from '@host/features/assistant/attachments/agent-composer-context';
import { attachmentAccept, attachmentFileIssue } from '@host/features/assistant/attachments/agent-input-context';
import { AgentAssetPicker } from '@host/features/assistant/references/AgentAssetPicker';
import { AgentSkillPicker } from '@host/features/assistant/skills/AgentSkillPicker';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { Icon } from '@host/shared/ui/Icon';
import { PreviewImage } from '@host/shared/ui/ImagePreview';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { ComposerMaterial } from '@host/types/assistant/attachments/agent-composer-context';
import type {
  AgentContextController,
  PendingReference,
  PendingUpload,
} from '@host/types/assistant/attachments/AgentContextComposer';
import type { AgentAttachment, AgentConversation, AgentSkill } from '@host/types/assistant/engine';
import { Alert, Button, Checkbox, Dropdown, Tag, Tooltip } from 'antd';
import { FileText, Music2, Video, X } from 'lucide-react';
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from 'react';
export const AgentContextComposer = forwardRef<
  AgentContextController,
  {
    conversation: Pick<AgentConversation, 'id' | 'project_id'>;
    disabled: boolean;
    attachmentApi?: Pick<
      typeof assistantApi,
      'attachments' | 'uploadAttachment' | 'referenceAttachment' | 'removeAttachment'
    >;
    dialogClassName?: string;
    layout?: 'standard';
    presentation?: 'assistant';
    toolsOpen?: boolean;
    materials?: readonly ComposerMaterial[];
    initialSkills: { id: string; content_version: string }[];
    skillSummary?: ReactNode;
    skillsManaged?: boolean;
    skillPicker?: (onClose: () => void) => ReactNode;
    onSkills: (skills: { id: string; content_version: string }[]) => void;
    onSkillsEdited?: () => void;
    onBusy: (busy: boolean) => void;
    onBlockReason: (reason: string) => void;
    input: ReactNode;
    modelControl: ReactNode;
    sendControl: ReactNode;
  }
>(function AgentContextComposer(
  {
    conversation,
    initialSkills,
    skillSummary,
    skillsManaged,
    skillPicker,
    onSkills,
    onSkillsEdited,
    disabled,
    onBusy,
    onBlockReason,
    input,
    modelControl,
    sendControl,
    attachmentApi = assistantApi,
    dialogClassName,
    presentation,
    toolsOpen,
    layout,
    materials = [],
  },
  ref,
) {
  const [attachments, setAttachments] = useState<AgentAttachment[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [revision, setRevision] = useState(0);
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null);
  const [pendingReference, setPendingReference] = useState<PendingReference | null>(null);
  const [showAssets, setShowAssets] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [videoAudio, setVideoAudio] = useState<'include' | 'visual_only'>('include');
  const [skillsLoading, setSkillsLoading] = useState(!skillsManaged && !skillPicker && initialSkills.length > 0);
  const upload = useRef<HTMLInputElement>(null);
  const uploadKind = useRef<AgentAttachment['kind']>('image');
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError('');
    attachmentApi
      .attachments(conversation.id, 0, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setAttachments(page.items.filter((item) => item.pending));
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setLoadError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [conversation.id, revision, attachmentApi]);
  useEffect(() => {
    if (skillsManaged || skillPicker || !initialSkills.length) return;
    const controller = new AbortController();
    (async () => {
      const all: AgentSkill[] = [];
      let total = 1;
      while (all.length < total) {
        const page = await skillsApi.skills(all.length, controller.signal);
        all.push(...page.items);
        total = page.total;
        if (!page.items.length) break;
      }
      if (controller.signal.aborted) return;
      const restored = initialSkills.flatMap((reference) =>
        all.filter(
          (skill) => skill.id === reference.id && skill.content_version === reference.content_version && skill.enabled,
        ),
      );
      setSkills(restored);
      onSkills(restored.map(({ id, content_version }) => ({ id, content_version })));
      if (restored.length !== initialSkills.length)
        setError('部分 Skill 已更新、停用或删除，请重新选择；原消息仍使用提交时的版本。');
    })()
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setSkillsLoading(false);
      });
    return () => controller.abort();
  }, [conversation.id]);
  function changeSkills(next: AgentSkill[]) {
    onSkillsEdited?.();
    setSkills(next);
    onSkills(next.map(({ id, content_version }) => ({ id, content_version })));
  }
  const issue = pendingUpload || pendingReference ? '附件添加结果尚未确认，请使用原请求核对后再发送。' : '';
  const blockReason = loadError ? '附件恢复失败，请重新载入后再发送。' : issue;
  const canAdd = !disabled && !busy && !loading && !loadError && !issue && attachments.length < 16;
  useEffect(() => {
    onBlockReason(blockReason);
  }, [blockReason, onBlockReason]);
  useEffect(() => {
    onBusy(busy || loading || skillsLoading);
    return () => onBusy(false);
  }, [busy, loading, skillsLoading, onBusy]);
  useImperativeHandle(
    ref,
    () => ({
      openSkills: () => setShowSkills(true),
      openUpload: (kind) => {
        if (!canAdd || !upload.current) return;
        uploadKind.current = kind;
        upload.current.accept = attachmentAccept[kind];
        upload.current.click();
      },
      openAssets: () => {
        if (canAdd) setShowAssets(true);
      },
      snapshot: () => {
        if (blockReason || busy)
          throw new ApiError(blockReason || '附件正在处理，请稍后发送。', 'assistant_input_busy');
        return {
          ...(attachments.length
            ? {
                attachment_ids: attachments.map((item) => item.id),
                video_audio: videoAudio,
              }
            : {}),
          ...(skills.length
            ? {
                skills: skills.map((item) => ({
                  id: item.id,
                  content_version: item.content_version,
                })),
              }
            : {}),
        };
      },
      sent: (ids) => setAttachments((previous) => previous.filter((item) => !ids.includes(item.id))),
      reference,
    }),
    [attachments, skills, videoAudio, blockReason, busy, loading, loadError, disabled, attachmentApi, canAdd],
  );
  async function perform(operation: () => Promise<void>) {
    if (lock.current || disabled || loading || loadError) return false;
    lock.current = true;
    setBusy(true);
    setError('');
    try {
      await operation();
      return true;
    } catch (cause) {
      if (alive.current) setError(errorMessage(cause));
      return false;
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function add(item: AgentAttachment) {
    if (alive.current)
      setAttachments((previous) => (previous.some((old) => old.id === item.id) ? previous : [...previous, item]));
  }
  async function uploadFile(file: File, retry?: PendingUpload) {
    await perform(async () => {
      if (!retry && attachments.length >= 16)
        throw new ApiError('一次消息最多添加 16 项附件。', 'assistant_attachment_limit');
      const kind = retry?.kind ?? uploadKind.current;
      const fileIssue = attachmentFileIssue(file, kind);
      if (fileIssue) throw new ApiError(fileIssue, 'assistant_attachment_invalid');
      const fingerprint = retry
        ? ''
        : Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())), (byte) =>
            byte.toString(16).padStart(2, '0'),
          ).join('');
      const attemptScope = retry?.scope ?? `agent-upload:${conversation.id}:${fingerprint}`;
      const key =
        retry?.key ??
        (await requestAttempt(
          attemptScope,
          { filename: file.name, checksum: fingerprint, size: file.size },
          attemptStorage(),
        ));
      const pending = { file, key, scope: attemptScope, kind };
      try {
        const item = await attachmentApi.uploadAttachment(conversation.id, file, key);
        clearAttempt(attemptScope, attemptStorage());
        if (alive.current) {
          setPendingUpload(null);
          add(item);
        }
      } catch (cause) {
        if (cause instanceof ApiError && cause.status && cause.status < 500) {
          clearAttempt(attemptScope, attemptStorage());
          if (alive.current) setPendingUpload(null);
        } else if (alive.current) setPendingUpload(pending);
        throw cause;
      }
    });
  }
  async function reference(type: 'media' | 'asset', id: string, retry?: PendingReference) {
    return perform(async () => {
      if (!retry && attachments.length >= 16)
        throw new ApiError('一次消息最多添加 16 项附件。', 'assistant_attachment_limit');
      if (!retry && (pendingUpload || pendingReference))
        throw new ApiError('附件添加结果尚未确认，请先使用原请求核对。', 'assistant_attachment_pending');
      const attemptScope = retry?.scope ?? `agent-reference:${conversation.id}:${type}:${id}`;
      const key =
        retry?.key ?? (await requestAttempt(attemptScope, { source_type: type, source_id: id }, attemptStorage()));
      try {
        const item = await attachmentApi.referenceAttachment(conversation.id, type, id, key);
        clearAttempt(attemptScope, attemptStorage());
        add(item);
        if (alive.current) {
          setPendingReference(null);
          setShowAssets(false);
        }
      } catch (cause) {
        if (cause instanceof ApiError && cause.status && cause.status < 500) {
          clearAttempt(attemptScope, attemptStorage());
          if (alive.current) setPendingReference(null);
        } else if (alive.current) {
          setPendingReference({ type, id, key, scope: attemptScope });
          setShowAssets(false);
        }
        throw cause;
      }
    });
  }
  const contextDisabled = disabled || busy;
  const addingDisabled =
    contextDisabled || loading || !!loadError || !!pendingUpload || !!pendingReference || attachments.length >= 16;
  function remove(item: AgentAttachment) {
    return perform(async () => {
      await attachmentApi.removeAttachment(conversation.id, item.id);
      if (alive.current) setAttachments((previous) => previous.filter((old) => old.id !== item.id));
    });
  }
  return (
    <>
      {(attachments.length > 0 || materials.length > 0) && (
        <div
          className={`agent-attached-items${presentation === 'assistant' ? ' ai-assistant-attachments' : ''}`}
          aria-label="本次消息素材"
        >
          {materials.map((item) => (
            <article key={item.key} className="ai-assistant-attachment" data-kind={item.kind} title={item.name}>
              <span className="ai-assistant-attachment-thumb">
                {item.thumbnail ? (
                  <img src={item.thumbnail} alt="" loading="lazy" decoding="async" />
                ) : (
                  <FileText size={14} aria-hidden="true" />
                )}
              </span>
              <span className="ai-assistant-attachment-name">{item.name}</span>
              <Button
                className="ai-assistant-attachment-remove"
                type="text"
                size="small"
                aria-label={`移除素材${item.name}`}
                icon={<X size={12} />}
                disabled={contextDisabled}
                onClick={item.onRemove}
              />
            </article>
          ))}
          {attachments.map((item) =>
            presentation === 'assistant' ? (
              <article key={item.id} className="ai-assistant-attachment" data-kind={item.kind} title={item.name}>
                <span className="ai-assistant-attachment-thumb">
                  {item.kind === 'image' && item.url ? (
                    <PreviewImage
                      src={item.url}
                      alt={item.name}
                      presentation={layout === 'standard' ? 'standard-creation' : undefined}
                    />
                  ) : item.kind === 'audio' ? (
                    <Music2 size={14} aria-hidden="true" />
                  ) : item.kind === 'video' ? (
                    <Video size={14} aria-hidden="true" />
                  ) : (
                    <FileText size={14} aria-hidden="true" />
                  )}
                </span>
                <span className="ai-assistant-attachment-name">{item.name}</span>
                <Button
                  className="ai-assistant-attachment-remove"
                  type="text"
                  size="small"
                  aria-label={`移除附件${item.name}`}
                  icon={<X size={12} />}
                  disabled={contextDisabled}
                  onClick={() => void remove(item)}
                />
              </article>
            ) : (
              <article key={item.id}>
                {item.kind === 'image' && item.url ? (
                  <PreviewImage
                    src={item.url}
                    alt={item.name}
                    presentation={layout === 'standard' ? 'standard-creation' : undefined}
                  />
                ) : item.kind === 'audio' && item.url ? (
                  <audio src={item.url} controls preload="metadata" aria-label={`${item.name}试听`} />
                ) : item.kind === 'video' && item.url ? (
                  <video src={item.url} controls preload="metadata" aria-label={`${item.name}预览`} />
                ) : null}
                <span title={item.name}>{item.name}</span>
                <Button
                  type="text"
                  size="small"
                  aria-label={`移除附件${item.name}`}
                  icon={<Icon name="close" size={14} />}
                  disabled={contextDisabled}
                  onClick={() => void remove(item)}
                />
              </article>
            ),
          )}
        </div>
      )}
      {skillsManaged || skillPicker
        ? skillSummary
        : skills.length > 0 && (
            <div className="agent-loaded-skills" aria-label="已加载技能">
              {skills.map((skill) => (
                <Tag
                  key={skill.id}
                  closable={!contextDisabled}
                  onClose={() => changeSkills(skills.filter((item) => item.id !== skill.id))}
                >
                  {skill.name} v{skill.content_version}
                </Tag>
              ))}
            </div>
          )}
      {attachments.some((item) => item.kind === 'video' && item.metadata.has_audio) && (
        <Checkbox
          checked={videoAudio === 'visual_only'}
          disabled={contextDisabled}
          onChange={(event) => setVideoAudio(event.target.checked ? 'visual_only' : 'include')}
        >
          视频只理解画面（忽略声音）
        </Checkbox>
      )}
      {loading && (
        <span className="sr-only" role="status">
          正在恢复本次消息附件…
        </span>
      )}
      <input
        hidden
        ref={upload}
        type="file"
        aria-label="上传对话附件"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          if (file) void uploadFile(file);
        }}
      />
      <ComposerAttachmentsContext.Provider value={attachments}>{input}</ComposerAttachmentsContext.Provider>
      <div className="agent-input-tools" hidden={presentation === 'assistant' && !toolsOpen}>
        <Dropdown
          trigger={['click']}
          overlayClassName={
            layout === 'standard' ? 'standard-assistant-menu standard-creation-menu standard-creation-popup' : undefined
          }
          menu={{
            items: [
              { key: 'image', label: '添加图片' },
              { key: 'text', label: '添加文本' },
              { key: 'video', label: '添加视频' },
              { key: 'audio', label: '添加音频' },
            ],
            onClick: ({ key }) => {
              uploadKind.current = key as AgentAttachment['kind'];
              if (upload.current) {
                upload.current.accept = attachmentAccept[uploadKind.current];
                upload.current.click();
              }
            },
          }}
          disabled={addingDisabled}
        >
          <Tooltip title="添加图片、文本、视频或音频">
            <Button
              type="text"
              aria-label="添加附件"
              loading={busy}
              disabled={addingDisabled}
              icon={<Icon name="plus" size={18} />}
            />
          </Tooltip>
        </Dropdown>
        <Tooltip title="添加资产库上下文">
          <Button
            type="text"
            aria-label="添加资产库"
            disabled={addingDisabled}
            icon={<Icon name="folder" size={18} />}
            onClick={() => setShowAssets(true)}
          />
        </Tooltip>
        {modelControl}
        <Tooltip title="加载 Skill">
          <Button
            type="text"
            aria-label="加载 Skill"
            disabled={contextDisabled}
            icon={<Icon name="library" size={18} />}
            onClick={() => setShowSkills(true)}
          />
        </Tooltip>
        <span className="agent-input-tools-spacer" />
        {sendControl}
      </div>
      {layout === 'standard' ? (
        (error || loadError || issue) && (
          <StudioFeedback
            scope="chat"
            type="warning"
            title="助手附件与技能"
            message={error || loadError || issue}
            noticeKey={`assistant-context:${conversation.id}`}
            actions={
              <>
                {loadError && (
                  <Button
                    type="link"
                    size="small"
                    disabled={contextDisabled || loading}
                    onClick={() => setRevision((value) => value + 1)}
                  >
                    重新载入附件
                  </Button>
                )}
                {pendingUpload && (
                  <Button
                    size="small"
                    disabled={contextDisabled}
                    onClick={() => void uploadFile(pendingUpload.file, pendingUpload)}
                  >
                    使用原请求核对上传
                  </Button>
                )}
                {pendingReference && (
                  <Button
                    size="small"
                    disabled={contextDisabled}
                    onClick={() => void reference(pendingReference.type, pendingReference.id, pendingReference)}
                  >
                    使用原请求核对资产引用
                  </Button>
                )}
              </>
            }
          />
        )
      ) : (
        <>
          {(error || loadError || issue) && (
            <Alert className="agent-context-error" type="warning" message={error || loadError || issue} />
          )}
          {loadError && (
            <Button
              type="link"
              size="small"
              disabled={contextDisabled || loading}
              onClick={() => setRevision((value) => value + 1)}
            >
              重新载入附件
            </Button>
          )}
          {pendingUpload && (
            <Button
              size="small"
              disabled={contextDisabled}
              onClick={() => void uploadFile(pendingUpload.file, pendingUpload)}
            >
              使用原请求核对上传
            </Button>
          )}
          {pendingReference && (
            <Button
              size="small"
              disabled={contextDisabled}
              onClick={() => void reference(pendingReference.type, pendingReference.id, pendingReference)}
            >
              使用原请求核对资产引用
            </Button>
          )}
        </>
      )}
      {showAssets && (
        <AgentAssetPicker
          projectId={conversation.project_id}
          layout={layout}
          dialogClassName={dialogClassName}
          disabled={contextDisabled}
          onClose={() => setShowAssets(false)}
          onSelect={async (type, id) => {
            await reference(type, id);
          }}
        />
      )}
      {showSkills &&
        (skillPicker ? (
          skillPicker(() => setShowSkills(false))
        ) : (
          <AgentSkillPicker
            layout={layout}
            dialogClassName={dialogClassName}
            selected={skills}
            disabled={contextDisabled}
            onChange={changeSkills}
            onClose={() => setShowSkills(false)}
          />
        ))}
    </>
  );
});
