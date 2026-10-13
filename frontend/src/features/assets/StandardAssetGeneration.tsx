import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Input, Popover, Select, Skeleton, Tag } from 'antd';
import type { LibraryAssetRead, AssetScope } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';
import { errorMessage } from '../../api/http';
import { ConfigSelect } from '../generations/ConfigSelect';
import { dateLabel, statusLabels, taskLabel } from '../generations/presentation';
import { GenerationComposer } from '../projects/GenerationComposer';
import type { useCreationDraft } from '../projects/useCreationDraft';
import { useAssetImageGeneration } from './useAssetImageGeneration';
import { assetImageGenerationBlockReason, assetPrivateInputBlockReason } from './asset-image-generation';
import { PrivateAssetReferences } from './PrivateAssetReferences';

type PrivateDraft = ReturnType<typeof useCreationDraft>;
export const emptyAssetGeneration: CreationDraftValue = { model_config_id: null, input_text: '', image_settings: { count: 1 }, video_settings: null, reference_media_ids: [] };

export function AssetGenerationParameters({ value, disabled, onChange, capabilities }: {
  value: CreationDraftValue; disabled: boolean; onChange: (value: Partial<CreationDraftValue>) => void; capabilities?: AiModelCapabilitiesDto | null;
}) {
  const settings = value.image_settings ?? { count: 1 };
  const set = (patch: Partial<NonNullable<CreationDraftValue['image_settings']>>) => onChange({ image_settings: { ...settings, ...patch } });
  const supports = (key: string) => !capabilities?.known || capabilities.parameters.includes(key);
  return <><Popover trigger="click" placement="top" content={<div className="standard-image-options"><label>画面比例<Select aria-label="画面比例" allowClear placeholder="模型默认" value={settings.aspect} disabled={disabled || !supports('aspect')} options={['16:9', '9:16', '1:1', '4:3', '3:4'].map(value => ({ value, label: value }))} onChange={aspect => set({ aspect })}/></label><label>分辨率<Input aria-label="分辨率" allowClear value={settings.resolution ?? ''} disabled={disabled || !supports('resolution')} placeholder="模型默认" onChange={event => set({ resolution: event.target.value })}/></label></div>}><Button type="text" disabled={disabled} aria-label="比例与分辨率">{settings.aspect || '比例'} · {settings.resolution || '默认'}</Button></Popover><Select className="standard-image-count" aria-label="生成张数" value={settings.count} disabled={disabled || !supports('count')} options={[1, 2, 4].map(value => ({ value, label: `${value} 张` }))} onChange={count => set({ count })}/></>;
}

export function StandardAssetGeneration({ asset, scope, draft, readOnly, disabled, historyOpen, onCloseHistory, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange, onVersionConflict, candidateHistory }: {
  asset: LibraryAssetRead; scope: Extract<AssetScope, { kind: 'episode' }>; draft: PrivateDraft;
  readOnly: boolean; disabled: boolean; historyOpen: boolean; onCloseHistory: () => void;
  onSaveBeforeGenerate: () => Promise<LibraryAssetRead | null>; onCandidatesChanged: () => void;
  onSubmissionBusyChange: (busy: boolean) => void; onVersionConflict: (cause: unknown) => void;
  candidateHistory?: ReactNode;
}) {
  const generation = useAssetImageGeneration(asset, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange, onVersionConflict, draft.flush);
  const { tasks, historyLoaded, historyLoading, refreshError, submitError, submitting, actionTaskId } = generation;
  const [references, setReferences] = useState({ blocked: true, sharedIds: [] as string[] });
  const [capabilities, setCapabilities] = useState<AiModelCapabilitiesDto | null>(null);
  const [capabilityError, setCapabilityError] = useState('');
  const [capabilityLoading, setCapabilityLoading] = useState(false);
  const [capabilityRevision, setCapabilityRevision] = useState(0);
  const configId = draft.value.model_config_id ?? undefined;
  useEffect(() => {
    const controller = new AbortController(); setCapabilities(null); setCapabilityError('');
    if (!configId) { setCapabilityLoading(false); return; }
    setCapabilityLoading(true);
    aiModelConfigs.capabilities(configId, controller.signal).then(value => { if (!controller.signal.aborted) setCapabilities(value); })
      .catch(cause => { if (!controller.signal.aborted) setCapabilityError(`模型能力读取失败：${errorMessage(cause)}`); })
      .finally(() => { if (!controller.signal.aborted) setCapabilityLoading(false); });
    return () => controller.abort();
  }, [configId, capabilityRevision]);
  const onReferenceState = useCallback((value: { blocked: boolean; sharedIds: string[] }) => setReferences(value), []);
  const selectModel = useCallback((id: string | undefined) => { if (draft.loaded && !readOnly) draft.edit({ model_config_id: id ?? null }); }, [draft.loaded, draft.edit, readOnly]);
  const privateBlocked = !draft.loaded || ['error', 'conflict', 'loading'].includes(draft.status);
  const blocked = disabled || submitting || privateBlocked;
  const reason = assetImageGenerationBlockReason(asset, configId)
    ?? assetPrivateInputBlockReason(draft.value.input_text, references.sharedIds, draft.value.reference_media_ids, !!capabilities?.reference_images);
  return <>
    <PrivateAssetReferences assetId={asset.id} projectId={scope.projectId} episodeId={scope.episodeId} privateIds={draft.value.reference_media_ids} saved={draft.saved} disabled={readOnly || blocked} onChange={ids => draft.edit({ reference_media_ids: ids })} onState={onReferenceState} onBusyChange={onSubmissionBusyChange}/>
    {!readOnly && <GenerationComposer prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })}
      model={<ConfigSelect kind="image" value={configId} onChange={selectModel} onResolvedChange={selectModel} disabled={blocked} label="生图模型"/>}
      parameters={<AssetGenerationParameters value={draft.value} disabled={blocked} onChange={draft.edit} capabilities={capabilities}/>}
      sendDisabled={blocked || generation.uncertain || references.blocked || capabilityLoading || !!capabilityError || !!reason} busy={submitting} disabled={!draft.loaded}
      onSend={() => void generation.submit({ configId, supplement: '', count: draft.value.image_settings?.count ?? 1, scope })}/>} 
    {!readOnly && reason && <p className="standard-generation-block" role="status">{reason}</p>}
    {capabilityError && <Alert type="error" message={capabilityError} action={<Button size="small" onClick={() => setCapabilityRevision(value => value + 1)}>重试读取能力</Button>}/>}
    {draft.message && <Alert type={draft.status === 'conflict' ? 'warning' : draft.status === 'error' ? 'error' : 'info'} message={draft.message} action={draft.status === 'error' ? <Button size="small" onClick={() => void draft.retry()}>核对并重试保存</Button> : draft.status === 'conflict' ? <Button size="small" onClick={async () => { if (await confirmAction('明确放弃本机生成设置并载入服务端版本？')) void draft.reload(); }}>载入服务端设置</Button> : undefined}/>}
    {(submitError || refreshError) && <Alert type="warning" showIcon message={submitError || refreshError} action={generation.uncertain ? <Button size="small" loading={generation.reconciling} onClick={() => void generation.reconcileSubmission()}>核对原生成请求</Button> : refreshError ? <Button size="small" onClick={() => void generation.load()}>重新读取生成记录</Button> : undefined}/>}
    {historyOpen && <Dialog title={`${asset.name} · 生成记录`} className="asset-generation-history-dialog" onClose={onCloseHistory}>
      {candidateHistory}
      <div className="asset-generation-history-heading"><Button loading={historyLoading} onClick={() => void generation.load()}>刷新</Button></div>
      {historyLoading && !historyLoaded ? <Skeleton active paragraph={{ rows: 4 }}/> : !tasks.length && !refreshError ? <p className="episode-help">暂无生成记录。</p> : <div className="asset-generation-tasks">{tasks.map(task => <article key={task.generation_id} className="asset-generation-task">
        <div><Tag className={'generation-status status-' + task.status}>{taskLabel(task)}</Tag><span>{dateLabel(task.created_at)}</span><small>任务 {task.generation_id}</small></div>
        {task.error && <p className="asset-generation-task-error">{task.error.message}</p>}
        <div className="asset-generation-task-actions"><Link to={`/tasks/image?task=${task.generation_id}`}>查看任务详情</Link>
          {task.can_cancel && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('模型调用可能已经发生。仍要请求取消任务吗？')) void generation.perform(task, 'cancel'); }}>请求取消</Button>}
          {task.can_resume && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('恢复将继续查询或保存既有调用。确定恢复？')) void generation.perform(task, 'resume'); }}>安全恢复</Button>}
          {task.can_retry && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('将使用原任务的旧输入重新生成，可能再次产生费用。确定继续？')) void generation.perform(task, 'retry'); }}>按旧输入重新生成</Button>}
          {(task.status === 'running' || task.status === 'queued') && <span>{statusLabels[task.status]}</span>}
        </div>
      </article>)}</div>}
      {generation.hasMore && <Button loading={generation.loadingMore} disabled={historyLoading} onClick={() => void generation.loadMore()}>加载更多生成记录</Button>}
      <div className="production-dialog-footer"><Button onClick={onCloseHistory}>关闭</Button></div>
    </Dialog>}
  </>;
}
