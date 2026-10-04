import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Checkbox, Dropdown, Tag, Tooltip } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentAttachment, AgentConversation, AgentModel, AgentSendInput, AgentSkill } from '../../api/types/agents';
import { Icon } from '../../components/ui/Icon';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { attachmentAccept, attachmentFileIssue, attachmentInputIssue } from './agent-input-context';
import { AgentAssetPicker } from './AgentAssetPicker';
import { AgentSkillPicker } from './AgentSkillPicker';

type ContextSnapshot = Pick<AgentSendInput, 'attachment_ids' | 'skills' | 'video_audio'>;
export interface AgentContextController {
  snapshot: () => ContextSnapshot;
  sent: (ids: string[]) => void;
}
interface PendingUpload { file: File; key: string; scope: string; kind: AgentAttachment['kind'] }
interface PendingReference { type: 'media' | 'asset'; id: string; key: string; scope: string }

export const AgentContextComposer = forwardRef<AgentContextController, {
  conversation: AgentConversation; model?: AgentModel; disabled: boolean;
  onBusy: (busy: boolean) => void; onBlockReason: (reason: string) => void;
  input: ReactNode; modelControl: ReactNode; sendControl: ReactNode;
}>(function AgentContextComposer({ conversation, model, disabled, onBusy, onBlockReason, input, modelControl, sendControl }, ref) {
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
  const upload = useRef<HTMLInputElement>(null);
  const uploadKind = useRef<AgentAttachment['kind']>('image');
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError('');
    agentsApi.attachments(conversation.id, 0, controller.signal).then(page => {
      if (!controller.signal.aborted) setAttachments(page.items.filter(item => item.pending));
    }).catch(cause => { if (!controller.signal.aborted) setLoadError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [conversation.id, revision]);
  const issue = pendingUpload || pendingReference ? '附件添加结果尚未确认，请使用原请求核对后再发送。' : attachmentInputIssue(attachments, model, videoAudio);
  const blockReason = loading ? '正在恢复本次消息附件…' : loadError ? '附件恢复失败，请重新载入后再发送。' : issue;
  useEffect(() => { onBlockReason(blockReason); }, [blockReason, onBlockReason]);
  useEffect(() => { onBusy(busy || loading); return () => onBusy(false); }, [busy, loading, onBusy]);
  useImperativeHandle(ref, () => ({
    snapshot: () => {
      if (blockReason || busy) throw new Error(blockReason || '附件正在处理，请稍后发送。');
      return {
      ...(attachments.length ? { attachment_ids: attachments.map(item => item.id), video_audio: videoAudio } : {}),
      ...(skills.length ? { skills: skills.map(item => ({ id: item.id, content_version: item.content_version })) } : {}),
      };
    },
    sent: ids => setAttachments(previous => previous.filter(item => !ids.includes(item.id))),
  }), [attachments, skills, videoAudio, blockReason, busy]);
  async function perform(operation: () => Promise<void>) {
    if (lock.current || disabled || loading || loadError) return;
    lock.current = true; setBusy(true); setError('');
    try { await operation(); }
    catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  function add(item: AgentAttachment) {
    if (alive.current) setAttachments(previous => previous.some(old => old.id === item.id) ? previous : [...previous, item]);
  }
  async function uploadFile(file: File, retry?: PendingUpload) {
    await perform(async () => {
      if (!retry && attachments.length >= 16) throw new Error('一次消息最多添加 16 项附件。');
      const kind = retry?.kind ?? uploadKind.current;
      const fileIssue = attachmentFileIssue(file, kind);
      if (fileIssue) throw new Error(fileIssue);
      const fingerprint = retry ? '' : Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('');
      const scope = retry?.scope ?? `agent-upload:${conversation.id}:${fingerprint}`;
      const key = retry?.key ?? await requestAttempt(scope, { filename: file.name, checksum: fingerprint, size: file.size }, attemptStorage());
      const pending = { file, key, scope, kind };
      try {
        const item = await agentsApi.uploadAttachment(conversation.id, file, key);
        clearAttempt(scope, attemptStorage());
        if (alive.current) { setPendingUpload(null); add(item); }
      } catch (cause) {
        if (cause instanceof ApiError && cause.status && cause.status < 500) {
          clearAttempt(scope, attemptStorage()); if (alive.current) setPendingUpload(null);
        } else if (alive.current) setPendingUpload(pending);
        throw cause;
      }
    });
  }
  async function reference(type: 'media' | 'asset', id: string, retry?: PendingReference) {
    await perform(async () => {
      const scope = retry?.scope ?? `agent-reference:${conversation.id}:${type}:${id}`;
      const key = retry?.key ?? await requestAttempt(scope, { source_type: type, source_id: id }, attemptStorage());
      try {
        const item = await agentsApi.referenceAttachment(conversation.id, type, id, key);
        clearAttempt(scope, attemptStorage()); add(item);
        if (alive.current) { setPendingReference(null); setShowAssets(false); }
      } catch (cause) {
        if (cause instanceof ApiError && cause.status && cause.status < 500) {
          clearAttempt(scope, attemptStorage()); if (alive.current) setPendingReference(null);
        } else if (alive.current) { setPendingReference({ type, id, key, scope }); setShowAssets(false); }
        throw cause;
      }
    });
  }
  const contextDisabled = disabled || busy;
  const addingDisabled = contextDisabled || loading || !!loadError || !!pendingUpload || !!pendingReference || attachments.length >= 16;
  return <>
    {attachments.length > 0 && <div className="agent-attached-items" aria-label="本次消息附件">{attachments.map(item => <article key={item.id}>
      {item.kind === 'image' && item.url ? <PreviewImage src={item.url} alt={item.name}/>
        : item.kind === 'audio' && item.url ? <audio src={item.url} controls preload="metadata" aria-label={`${item.name}试听`}/>
          : item.kind === 'video' && item.url ? <video src={item.url} controls preload="metadata" aria-label={`${item.name}预览`}/> : null}
      <span title={item.name}>{item.name}</span>
      <Button type="text" size="small" aria-label={`移除附件${item.name}`} icon={<Icon name="close" size={14}/>} disabled={contextDisabled} onClick={() => void perform(async () => {
        await agentsApi.removeAttachment(conversation.id, item.id);
        if (alive.current) setAttachments(previous => previous.filter(old => old.id !== item.id));
      })}/>
    </article>)}</div>}
    {skills.length > 0 && <div className="agent-loaded-skills" aria-label="已加载技能">{skills.map(skill => <Tag key={skill.id} closable={!contextDisabled} onClose={() => setSkills(previous => previous.filter(item => item.id !== skill.id))}>{skill.name} v{skill.content_version}</Tag>)}</div>}
    {attachments.some(item => item.kind === 'video' && item.metadata.has_audio) && <Checkbox checked={videoAudio === 'visual_only'} disabled={contextDisabled} onChange={event => setVideoAudio(event.target.checked ? 'visual_only' : 'include')}>视频只理解画面（忽略声音）</Checkbox>}
    {loading && <span className="sr-only" role="status">正在恢复本次消息附件…</span>}
    {input}
    <div className="agent-input-tools">
      <Dropdown trigger={['click']} menu={{ items: [
        { key: 'image', label: '添加图片' }, { key: 'text', label: '添加文本' }, { key: 'video', label: '添加视频' }, { key: 'audio', label: '添加音频' },
      ], onClick: ({ key }) => {
        uploadKind.current = key as AgentAttachment['kind'];
        if (upload.current) { upload.current.accept = attachmentAccept[uploadKind.current]; upload.current.click(); }
      } }} disabled={addingDisabled}>
        <Tooltip title="添加图片、文本、视频或音频"><Button type="text" aria-label="添加附件" loading={busy} disabled={addingDisabled} icon={<Icon name="plus" size={18}/>}/></Tooltip>
      </Dropdown>
      <input hidden ref={upload} type="file" aria-label="上传对话附件" onChange={event => {
        const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
        if (file) void uploadFile(file);
      }}/>
      <Tooltip title="添加资产库上下文"><Button type="text" aria-label="添加资产库" disabled={addingDisabled} icon={<Icon name="folder" size={18}/>} onClick={() => setShowAssets(true)}/></Tooltip>
      {modelControl}
      <Tooltip title="加载 Skill"><Button type="text" aria-label="加载 Skill" disabled={contextDisabled} icon={<Icon name="library" size={18}/>} onClick={() => setShowSkills(true)}/></Tooltip>
      <span className="agent-input-tools-spacer"/>{sendControl}
    </div>
    {(error || loadError || issue) && <Alert className="agent-context-error" type="warning" message={error || loadError || issue}/>}
    {loadError && <Button type="link" size="small" disabled={contextDisabled || loading} onClick={() => setRevision(value => value + 1)}>重新载入附件</Button>}
    {pendingUpload && <Button size="small" disabled={contextDisabled} onClick={() => void uploadFile(pendingUpload.file, pendingUpload)}>使用原请求核对上传</Button>}
    {pendingReference && <Button size="small" disabled={contextDisabled} onClick={() => void reference(pendingReference.type, pendingReference.id, pendingReference)}>使用原请求核对资产引用</Button>}
    {showAssets && <AgentAssetPicker projectId={conversation.project_id} disabled={contextDisabled} onClose={() => setShowAssets(false)} onSelect={reference}/>}
    {showSkills && <AgentSkillPicker selected={skills} disabled={contextDisabled} onChange={setSkills} onClose={() => setShowSkills(false)}/>}
  </>;
});
