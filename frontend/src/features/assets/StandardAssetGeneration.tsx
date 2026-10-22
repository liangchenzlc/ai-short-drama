import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Button, Popover, Select, Skeleton, Tag } from 'antd';
import type { LibraryAssetRead, AssetScope } from '../../api/modules/assets';
import type { CreationDraftValue } from '../../api/modules/creation-drafts';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { errorMessage } from '../../api/http';
import { ConfigSelect } from '../generations/ConfigSelect';
import { dateLabel, statusLabels, taskLabel } from '../generations/presentation';
import { GenerationComposer } from '../projects/GenerationComposer';
import type { useCreationDraft } from '../projects/useCreationDraft';
import { useAssetImageGeneration } from './useAssetImageGeneration';
import { assetImageGenerationBlockReason, assetPrivateInputBlockReason } from './asset-image-generation';
import { ChevronDown, SlidersHorizontal, X } from 'lucide-react';
import { PrivateAssetReferences } from './PrivateAssetReferences';
import { AssetPromptInput } from './AssetPromptInput';
import { remapAssetPromptReferences, type AssetPromptReference } from './asset-prompt-references';

type PrivateDraft = ReturnType<typeof useCreationDraft>;
export const emptyAssetGeneration: CreationDraftValue = { model_config_id: null, input_text: '', image_settings: { count: 1 }, video_settings: null, reference_media_ids: [] };

export function AssetGenerationParameters({ value, disabled, onChange, capabilities, countPlacement = 'inline' }: {
  value: CreationDraftValue; disabled: boolean; onChange: (value: Partial<CreationDraftValue>) => void; capabilities?: AiModelCapabilitiesDto | null;
  countPlacement?: 'inline' | 'options';
}) {
  const settings = value.image_settings ?? { count: 1 };
  const set = (patch: Partial<NonNullable<CreationDraftValue['image_settings']>>) => onChange({ image_settings: { ...settings, ...patch } });
  const supports = (key: string) => !capabilities?.known || capabilities.parameters.includes(key);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const optionsId = useId();
  const trigger = useRef<HTMLElement | null>(null);
  const options = useRef<HTMLDivElement | null>(null);
  const closeOptions = useCallback(() => {
    setOptionsOpen(false);
    requestAnimationFrame(() => { if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true }); });
  }, []);
  useEffect(() => {
    if (!optionsOpen) return;
    const frame = requestAnimationFrame(() => {
      const selected = options.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]:not(:disabled)');
      (selected ?? options.current?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus({ preventScroll: true });
    });
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); closeOptions();
    };
    document.addEventListener('keydown', escape, true);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', escape, true); };
  }, [optionsOpen, closeOptions]);
  const ratios = ['9:16', '2:3', '3:4', '1:1', '4:3', '3:2', '16:9'];
  const aspectUnavailableReason = (aspect: string) => ['2:3', '3:2'].includes(aspect)
    ? `当前暂不支持 ${aspect} 比例`
    : !supports('aspect') ? '当前模型不支持比例设置' : undefined;
  return <><Popover trigger="click" placement="top" arrow={false} destroyOnHidden open={optionsOpen} onOpenChange={setOptionsOpen} overlayClassName={`standard-image-options-popover standard-creation-popup${countPlacement === 'options' ? ' is-asset-options' : ''}`} content={<div id={optionsId} ref={options} className="standard-image-options" role="dialog" aria-label="生成选项">
    <header className="standard-output-options-heading"><span>{countPlacement === 'options' ? '生成选项' : '比例与分辨率'}</span><Button type="text" aria-label="关闭生成选项" title="关闭生成选项" icon={<X size={16}/>} onClick={closeOptions}/></header>
    <fieldset><legend>比例</legend><div className="standard-image-ratios">{ratios.map(aspect => {
      const [x, y] = aspect.split(':').map(Number);
      const unavailableReason = aspectUnavailableReason(aspect);
      return <button key={aspect} type="button" aria-label={`画面比例 ${aspect}`} aria-pressed={settings.aspect === aspect} title={unavailableReason} disabled={disabled || !!unavailableReason} onClick={() => set({ aspect })}><svg width="21" height="21" viewBox="0 0 24 24" aria-hidden="true"><rect x={12 - (x >= y ? 9 : 9 * x / y)} y={12 - (x >= y ? 9 * y / x : 9)} width={x >= y ? 18 : 18 * x / y} height={x >= y ? 18 * y / x : 18} rx="1.5" fill="none" stroke="currentColor"/></svg><span>{aspect}</span></button>;
    })}</div><Button type="text" size="small" className="standard-image-default standard-image-aspect-default" aria-label="使用模型默认比例" aria-pressed={settings.aspect == null} disabled={disabled || !supports('aspect')} onClick={() => set({ aspect: null })}>模型默认</Button><p className="standard-image-options-help">{supports('aspect') ? '2:3、3:2 比例暂不可用。' : '当前模型不支持比例设置。'}</p></fieldset>
    <fieldset><legend>分辨率</legend><div className="standard-image-resolutions">{['1K', '2K', '4K'].map(resolution => <button key={resolution} type="button" aria-pressed={settings.resolution === resolution} title={!supports('resolution') ? '当前模型不支持分辨率设置' : undefined} disabled={disabled || !supports('resolution')} onClick={() => set({ resolution })}>{resolution}</button>)}</div><Button type="text" size="small" className="standard-image-default standard-image-resolution-default" aria-label="使用模型默认分辨率" aria-pressed={settings.resolution == null} disabled={disabled || !supports('resolution')} onClick={() => set({ resolution: null })}>模型默认</Button>{!supports('resolution') && <p className="standard-image-options-help">当前模型不支持分辨率设置。</p>}</fieldset>
    {countPlacement === 'options' && <fieldset><legend>选择张数</legend><div className="standard-image-resolutions">{([1, 2, 4] as const).map(count => <button key={count} type="button" aria-label={`生成 ${count} 张图片`} aria-pressed={settings.count === count} title={!supports('count') ? '当前模型不支持张数设置' : undefined} disabled={disabled || !supports('count')} onClick={() => set({ count })}>{count} 张</button>)}</div>{!supports('count') && <p className="standard-image-options-help">当前模型不支持张数设置。</p>}</fieldset>}
  </div>}><Button className="standard-image-output" type="text" disabled={disabled} aria-label="比例与分辨率" title={countPlacement === 'options' ? '设置比例、分辨率与张数' : '设置比例与分辨率'} aria-haspopup="dialog" aria-expanded={optionsOpen} aria-controls={optionsId} onFocus={event => { trigger.current = event.currentTarget; }} onClick={event => { trigger.current = event.currentTarget; }} icon={<SlidersHorizontal size={11}/>}><span>{settings.aspect || '比例'}</span><span className="standard-image-output-separator" aria-hidden="true"/><span>{settings.resolution || '默认'}</span>{countPlacement === 'options' && <><span className="standard-image-output-separator" aria-hidden="true"/><span>{settings.count}张</span></>}</Button></Popover>{countPlacement === 'inline' && <Select classNames={{ popup: { root: 'standard-creation-popup' } }} className="standard-image-count" aria-label="生成张数" value={settings.count} disabled={disabled || !supports('count')} suffixIcon={<ChevronDown size={9}/>} options={[1, 2, 4].map(value => ({ value, label: `${value} 张` }))} onChange={count => set({ count })}/>}</>;
}

export function StandardAssetGeneration({ asset, scope, draft, readOnly, disabled, historyOpen, onCloseHistory, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange, onVersionConflict, candidateHistory, historyActions }: {
  asset: LibraryAssetRead; scope: Extract<AssetScope, { kind: 'project' }>; draft: PrivateDraft;
  readOnly: boolean; disabled: boolean; historyOpen: boolean; onCloseHistory: () => void;
  onSaveBeforeGenerate: () => Promise<LibraryAssetRead | null>; onCandidatesChanged: () => void;
  onSubmissionBusyChange: (busy: boolean) => void; onVersionConflict: (cause: unknown) => void;
  candidateHistory?: ReactNode; historyActions?: ReactNode;
}) {
  const [modelFeedback, setModelFeedback] = useState<HTMLDivElement | null>(null);
  const generation = useAssetImageGeneration(asset, onSaveBeforeGenerate, onCandidatesChanged, onSubmissionBusyChange, onVersionConflict, draft.flush);
  const { tasks, historyLoaded, historyLoading, refreshError, submitError, submitting, actionTaskId } = generation;
  const [references, setReferences] = useState({ blocked: true, sharedIds: [] as string[] });
  const [referenceImages, setReferenceImages] = useState<AssetPromptReference[]>([]);
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
    <PrivateAssetReferences assetId={asset.id} projectId={scope.projectId} privateIds={draft.value.reference_media_ids} saved={draft.saved} disabled={readOnly || blocked} onChange={(ids, mapping) => draft.edit({ reference_media_ids: ids, input_text: remapAssetPromptReferences(draft.session.getSnapshot().value.input_text, mapping.before, mapping.after) })} onState={onReferenceState} onImagesChange={setReferenceImages} onBusyChange={onSubmissionBusyChange}/>
    {!readOnly && <GenerationComposer feedbackRef={setModelFeedback} prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })}
      promptInput={id => <AssetPromptInput id={id} value={draft.value.input_text} onChange={input_text => draft.edit({ input_text })} references={referenceImages} disabled={!draft.loaded}/>}
      model={<ConfigSelect presentation="standard-creation" optionLayout="compact" feedbackContainer={modelFeedback} kind="image" value={configId} onChange={selectModel} onResolvedChange={selectModel} disabled={blocked} label="生图模型"/>}
      parameters={<AssetGenerationParameters countPlacement="options" value={draft.value} disabled={blocked} onChange={draft.edit} capabilities={capabilities}/>}
      sendTitle={reason || undefined} sendDisabled={blocked || generation.uncertain || references.blocked || capabilityLoading || !!capabilityError || !!reason} busy={submitting} disabled={!draft.loaded}
      onSend={() => void generation.submit({ configId, supplement: draft.value.input_text, count: draft.value.image_settings?.count ?? 1, scope })}/>}
    {capabilityError && <StudioFeedback type="error" title="素材生图模型" noticeKey={`asset-capabilities:${asset.id}`} message={capabilityError} actions={<Button size="small" onClick={() => setCapabilityRevision(value => value + 1)}>重试读取能力</Button>}/>}
    {draft.message && <StudioFeedback type={draft.status === 'conflict' ? 'warning' : draft.status === 'error' ? 'error' : 'info'} quiet={draft.status !== 'conflict' && draft.status !== 'error'} title={`生成设置 · ${asset.name}`} noticeKey={`asset-generation-draft:${asset.id}`} message={draft.message} actions={draft.status === 'error' ? <Button size="small" onClick={() => void draft.retry()}>核对并重试保存</Button> : draft.status === 'conflict' ? <Button size="small" onClick={() => void draft.reload()}>载入服务端设置</Button> : undefined}/>}
    {(submitError || refreshError) && <StudioFeedback type={generation.uncertain ? 'warning' : 'error'} title={`图片生成 · ${asset.name}`} noticeKey={`asset-generation:${asset.id}`} message={submitError || refreshError} actions={generation.uncertain ? <Button size="small" loading={generation.reconciling} onClick={() => void generation.reconcileSubmission()}>核对原生成请求</Button> : refreshError ? <Button size="small" onClick={() => void generation.load()}>重新读取生成记录</Button> : undefined}/>}
    {historyOpen && <Dialog title={`${asset.name} · 生成记录`} className="asset-generation-history-dialog standard-creation-dialog" onClose={onCloseHistory}>
      {candidateHistory}
      {refreshError && <Alert type="error" message={refreshError} action={<Button size="small" onClick={() => void generation.load()}>重新读取生成记录</Button>}/>}
      <div className="asset-generation-history-heading"><Button loading={historyLoading} onClick={() => void generation.load()}>刷新</Button></div>
      {historyLoading && !historyLoaded ? <div role="status"><Skeleton active paragraph={{ rows: 4 }}/><p className="episode-help">正在读取生成记录…</p></div> : !tasks.length && !refreshError ? <p className="episode-help">暂无生成记录。</p> : <div className="asset-generation-tasks">{tasks.map(task => <article key={task.generation_id} className="asset-generation-task">
        <div><Tag className={'generation-status status-' + task.status}>{taskLabel(task)}</Tag><span>{dateLabel(task.created_at)}</span><small>任务 {task.generation_id}</small></div>
        {task.error && <p className="asset-generation-task-error">{task.error.message}</p>}
        <div className="asset-generation-task-actions"><Link to={`/tasks/image?task=${task.generation_id}`}>查看任务详情</Link>
          {task.can_cancel && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('模型调用可能已经发生。仍要请求取消任务吗？', { className: 'standard-creation-dialog' })) void generation.perform(task, 'cancel'); }}>请求取消</Button>}
          {task.can_resume && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('恢复将继续查询或保存既有调用。确定恢复？', { className: 'standard-creation-dialog' })) void generation.perform(task, 'resume'); }}>安全恢复</Button>}
          {task.can_retry && !readOnly && <Button type="link" loading={actionTaskId === task.generation_id} disabled={!!actionTaskId} onClick={async () => { if (await confirmAction('将使用原任务的旧输入重新生成，可能再次产生费用。确定继续？', { className: 'standard-creation-dialog' })) void generation.perform(task, 'retry'); }}>按旧输入重新生成</Button>}
          {(task.status === 'running' || task.status === 'queued') && <span>{statusLabels[task.status]}</span>}
        </div>
      </article>)}</div>}
      {generation.hasMore && <Button loading={generation.loadingMore} disabled={historyLoading} onClick={() => void generation.loadMore()}>加载更多生成记录</Button>}
      <div className="production-dialog-footer">{historyActions}<Button onClick={onCloseHistory}>关闭</Button></div>
    </Dialog>}
  </>;
}
