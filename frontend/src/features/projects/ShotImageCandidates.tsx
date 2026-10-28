import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button } from 'antd';
import { StudioFeedback as Alert, feedbackType } from '../../components/ui/StudioFeedback';
import { generations } from '../../api/modules/generations';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { assetLibraries, type AssetRead } from '../../api/modules/assets';
import { ApiError, errorMessage } from '../../api/http';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { ShotRead } from '../../api/modules/storyboard';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { Dialog } from '../../components/ui/Dialog';
import { generationReferences, type ReferenceImage } from '../../api/modules/generation-references';
import { TaskDetail } from '../generations/TaskDetail';
import { shotImageRequest } from './workflow-contract';
import { imageGenerationBlockReason, summarizeImageReferenceCount, summarizeShotReferences, type ImageCapabilities, type PreparedShot } from './shot-image-workflow';
import { useShotImageGeneration } from './useShotImageGeneration';
import { GenerationComposer } from './GenerationComposer';
import { useCreationDraft } from './useCreationDraft';
import { CreationDraftFeedback } from './CreationDraftFeedback';
import { PrivateReferenceImages } from './PrivateReferenceImages';
import { EpisodeModelSelect } from './EpisodeModelSelect';
import type { NavigationBarrier } from './writing-navigation';
import { creationScope, clearRecovery, readRecovery, saveRecovery } from './creation-recovery';
import { isPendingShotGeneration, type PendingShotGeneration } from './shot-generation-submission';
import { AI_CONFIGS_CHANGED } from '../ai-config/config-events';
import { ShotGenerationSettings } from './ShotGenerationSettings';
import { AssetGenerationParameters } from '../assets/StandardAssetGeneration';
import { useImageGroup } from '../image-tools/useImageGroup';
import { ImageGroupPreview } from '../image-tools/ImageGroupPreview';
import { ImageGroupHistory } from '../image-tools/ImageGroupHistory';
import { AssetPromptInput } from '../assets/AssetPromptInput';
import { combineAssetPromptReferences, remapAssetPromptReferences, type AssetPromptReference } from '../assets/asset-prompt-references';

export function ShotImageCandidates({ shot, disabled, modelId, capabilities, onRefreshCapabilities, onChanged, prepareShot, projectId, modelSelection, onModelChange, onResolvedModel, registerBarrier, information, settingsOpen = true, active, onCloseSettings, historyOpen, onHistoryOpenChange, availableReferences, availableLoading, availableError, onRefreshAvailable }: {
  projectId: string; modelSelection: string; onModelChange: (id: string) => void;
  onResolvedModel: (id: string | undefined) => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void; information?: ReactNode; settingsOpen?: boolean;
  active: boolean; onCloseSettings: () => void; onOpenSettings: () => void;
  historyOpen: boolean; onHistoryOpenChange: (open: boolean) => void;
  availableReferences: ReferenceImage[]; availableLoading: boolean; availableError: string; onRefreshAvailable: () => void;
  shot: ShotRead; disabled: boolean; modelId: string | undefined; capabilities: ImageCapabilities | null;
  capabilitiesLoading: boolean; onRefreshCapabilities: () => void; onChanged: () => void;
  prepareShot: (purpose?: 'generation' | 'adoption') => Promise<PreparedShot | null>; settings?: ReactNode;
}) {
  const [modelFeedback, setModelFeedback] = useState<HTMLDivElement | null>(null);
  const expanded = true;
  const draft = useCreationDraft({ projectId, objectId: shot.id, purpose: 'storyboard_image', readOnly: disabled, registerBarrier, initialValue: { model_config_id: modelId ?? null, image_settings: { aspect: shot.image_settings.aspect, resolution: shot.image_settings.resolution, count: 1 } } });
  const [selectedModelId, setSelectedModelId] = useState<string>();
  const actualModelId = selectedModelId;
  const resolved = useRef({ onResolvedModel, draft }); resolved.current = { onResolvedModel, draft };
  const handleResolved = useCallback((id: string | undefined) => {
    setSelectedModelId(id); resolved.current.onResolvedModel(id);
  }, []);
  useEffect(() => {
    if (actualModelId && draft.loaded && !draft.value.model_config_id) draft.edit({ model_config_id: actualModelId });
  }, [actualModelId, draft.loaded, draft.value.model_config_id, draft.session]);
  const [privateCapabilities, setPrivateCapabilities] = useState<AiModelCapabilitiesDto | null>(null);
  const [privateCapsLoading, setPrivateCapsLoading] = useState(false);
  const [privateCapsError, setPrivateCapsError] = useState('');
  const [privateCapsRevision, setPrivateCapsRevision] = useState(0);
  useEffect(() => { const controller = new AbortController(); setPrivateCapsLoading(!!actualModelId); setPrivateCapabilities(null); setPrivateCapsError('');
    if (actualModelId) void aiModelConfigs.capabilities(actualModelId, controller.signal).then(result => { if (!controller.signal.aborted) setPrivateCapabilities(result); }).catch(cause => { if (!controller.signal.aborted) setPrivateCapsError(`图片模型能力读取失败：${errorMessage(cause)}`); }).finally(() => { if (!controller.signal.aborted) setPrivateCapsLoading(false); });
    return () => controller.abort();
  }, [actualModelId, privateCapsRevision]);
  useEffect(() => {
    const refresh = () => setPrivateCapsRevision(value => value + 1);
    window.addEventListener('focus', refresh); window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    return () => { window.removeEventListener('focus', refresh); window.removeEventListener(AI_CONFIGS_CHANGED, refresh); };
  }, []);
  const setHistoryOpen = onHistoryOpenChange;
  const [uploaded, setUploaded] = useState<ReferenceImage[] | null>(null);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [referenceImages, setReferenceImages] = useState<AssetPromptReference[]>([]);
  const history = useShotImageGeneration({ shotId: shot.id, enabled: true });

  const [taskId, setTaskId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toolBusy, setToolBusy] = useState(false);
  const images = useImageGroup('shot', shot.id, onChanged);
  const [message, setMessage] = useState('');
  const attemptScope = creationScope(`shot-image-submission:${projectId}:${shot.id}`);
  const currentScope = useRef(attemptScope); currentScope.current = attemptScope;
  const [uncertain, setUncertain] = useState<PendingShotGeneration | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [assets, setAssets] = useState<AssetRead[]>([]);
  const [referencesLoading, setReferencesLoading] = useState(false);
  const [referencesError, setReferencesError] = useState('');
  const [referencesRevision, setReferencesRevision] = useState(0);
  const mutation = useRef(false);
  const alive = useRef(true);
  const referenceKey = shot.asset_ids.join(',');

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    setUncertain(null); setRecoveryBlocked(false);
    try {
      const pending = readRecovery(attemptScope, isPendingShotGeneration);
      if (pending && (pending.body.source?.scene !== 'shot_image' || pending.body.source.shot_id !== shot.id)) throw new Error('生成记录与镜头不一致。');
      setUncertain(pending);
    } catch (cause) { setRecoveryBlocked(true); setMessage(errorMessage(cause)); }
  }, [attemptScope, shot.id]);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setReferencesLoading(true); setReferencesError('');
    Promise.all([Promise.all(shot.asset_ids.map((id) => assetLibraries.detail(id, controller.signal))), generationReferences('shot', shot.id).list(controller.signal)])
      .then(([next, manual]) => { if (!controller.signal.aborted) { setAssets(next); setUploaded(manual.items); } })
      .catch(() => { if (!controller.signal.aborted) setReferencesError('参考素材暂时无法刷新，显示的内容可能不是最新版本。'); })
      .finally(() => { if (!controller.signal.aborted) setReferencesLoading(false); });
    return () => controller.abort();
  }, [expanded, referenceKey, shot.context_hash, referencesRevision]);

  const references = summarizeShotReferences(shot.asset_ids, assets);
  const referenceCount = summarizeImageReferenceCount(references.referenceMediaIds, [...(uploaded ?? []).map(item => item.media_id), ...draft.value.reference_media_ids]);
  const blockReason = privateCapsError || imageGenerationBlockReason(actualModelId, privateCapabilities ?? capabilities, referenceCount.totalCount, privateCapsLoading)
    || (!draft.loaded ? '正在载入个人生成设置…' : draft.status === 'error' || draft.status === 'conflict' ? '请先处理个人生成设置保存提示。' : '')
    || (referencesLoading ? '正在核对参考素材…' : referencesError)
    || (uploaded === null ? '正在核对手动参考图…' : '')
    || (references.missingAssetIds.length ? '关联素材尚未加载完整，请刷新核对。' : '');
  const sendReason = uncertain ? '上次图片请求仍待核对，已保留原请求。' : recoveryBlocked ? '生成恢复记录无法读取，请从保存详情处理恢复记录。'
    : disabled ? '镜头存在未解决的编辑或保存状态，请先核对。' : toolBusy ? '图片操作正在进行，请稍候。' : referenceBusy ? '参考图片正在上传，请稍候。' : blockReason;
  const blockerActions = uncertain ? <Button size="small" loading={busy} onClick={() => void resolveUnknown()}>核对原图片请求</Button>
    : draft.status === 'error' ? <Button size="small" onClick={() => void draft.retry()}>重试保存设置</Button>
    : draft.status === 'conflict' ? <Button size="small" onClick={() => void draft.reload()}>核对服务端设置</Button>
    : privateCapsError || referencesError || references.missingAssetIds.length ? <Button size="small" onClick={() => { setReferencesRevision(value => value + 1); setPrivateCapsRevision(value => value + 1); onRefreshCapabilities(); }}>重新核对</Button> : undefined;

  async function generate() {
    if (mutation.current || disabled || toolBusy || referenceBusy || blockReason || uncertain || recoveryBlocked) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    let submitted = false;
    try {
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const savedDraft = await draft.flush();
      if (!savedDraft || !prepared.isCurrent() || !alive.current) return;
      const current = prepared.shot;
      const [latestAssets, latestManual] = await Promise.all([
        Promise.all(current.asset_ids.map((id) => assetLibraries.detail(id))),
        generationReferences('shot', current.id).list(),
      ]);
      if (!alive.current || !prepared.isCurrent()) return;
      setAssets(latestAssets);
      setUploaded(latestManual.items);
      const latest = summarizeShotReferences(current.asset_ids, latestAssets);
      const reason = imageGenerationBlockReason(actualModelId, privateCapabilities ?? capabilities, summarizeImageReferenceCount(latest.referenceMediaIds, [...latestManual.items.map(item => item.media_id), ...savedDraft.reference_media_ids]).totalCount, privateCapsLoading);
      if (reason) { setMessage(reason); return; }
      if (latest.referenceMediaIds.join(',') !== references.referenceMediaIds.join(',')
        || latestManual.items.map(item => item.media_id).join(',') !== (uploaded ?? []).map(item => item.media_id).join(',')) {
        setMessage('参考图片已变化，请核对后重新点击生成。'); return;
      }
      const request = shotImageRequest(current, '', [], savedDraft.image_settings?.count ?? 1);
      const body = { ...request, config_id: savedDraft.model_config_id ?? actualModelId, source: { ...request.source, draft_id: savedDraft.id, draft_version: savedDraft.draft_version }, parameters: { ...request.parameters, ...(savedDraft.image_settings ?? {}), aspect: savedDraft.image_settings?.aspect ?? undefined, resolution: savedDraft.image_settings?.resolution ?? undefined } };
      const scope = `shot-image:${shot.id}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      if (!alive.current || !prepared.isCurrent()) return;
      const pending: PendingShotGeneration = { format: 1, key, body };
      saveRecovery(attemptScope, pending);
      setUncertain(pending); submitted = true;
      const task = await generations.generateImage(body, key);
      clearRecovery(attemptScope);
      if (!alive.current || currentScope.current !== attemptScope) return;
      clearAttempt(scope, attemptStorage());
      setUncertain(null); setMessage(`图片任务 ${task.generation_id} 已提交。`);
      void history.refresh(); void images.refresh();
    } catch (cause) {
      const rejected = cause instanceof ApiError && !!cause.status && cause.status < 500 && cause.status !== 408;
      if (submitted && rejected) clearRecovery(attemptScope);
      if (alive.current && currentScope.current === attemptScope) {
        if (submitted && rejected) setUncertain(null);
        setMessage(cause instanceof ApiError && ['generation_unsupported_parameters', 'generation_unsupported_image_size', 'reference_limit_exceeded'].includes(cause.code)
          ? '当前模型不支持所选画幅、分辨率、张数或参考图数量。请核对模型与图片设置；不会自动丢弃参考图或更换参数。'
          : errorMessage(cause));
      }
    } finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  async function resolveUnknown() {
    if (!uncertain || mutation.current) return;
    mutation.current = true; setBusy(true);
    try {
      const receipt = await generations.lookup('image', uncertain.key);
      if (!alive.current || currentScope.current !== attemptScope) return;
      if (!receipt) { setMessage('暂未查到原图片任务，请稍后再次核对。当前不会重新提交。'); return; }
      clearRecovery(attemptScope); clearAttempt(`shot-image:${shot.id}`, attemptStorage());
      setUncertain(null); setMessage(`已找到原图片任务 ${receipt.generation_id}。`);
      void history.refresh(); void images.refresh();
    } catch (cause) { if (alive.current && currentScope.current === attemptScope) setMessage(`原图片请求核对失败：${errorMessage(cause)}`); }
    finally { mutation.current = false; if (alive.current) setBusy(false); }
  }

  const beforeImageChange = useCallback(async () => {
    const prepared = await prepareShot('adoption');
    try { return !!prepared?.isCurrent(); } finally { prepared?.release(); }
  }, [prepareShot]);
  const readonlyReferences = useMemo(() => [
    ...assets.filter(asset => shot.asset_ids.includes(asset.id) && asset.state === 'confirmed' && asset.media_id)
      .sort((left, right) => left.id.length - right.id.length || left.id.localeCompare(right.id))
      .map(asset => ({ media_id: asset.media_id!, name: asset.name, url: asset.image?.url ?? null })),
    ...(uploaded ?? []),
  ], [assets, shot.asset_ids, uploaded]);
  const addReference = useCallback(async (mediaId: string) => {
    if (!draft.loaded) throw new Error('个人生成设置仍在载入');
    const value = draft.session.getSnapshot().value;
    const reference_media_ids = [...new Set([...value.reference_media_ids, mediaId])];
    if (reference_media_ids.length > 16) throw new Error('私人参考图最多 16 张');
    const known = new Map(referenceImages.map(item => [item.media_id, item]));
    const resolve = (ids: string[]) => combineAssetPromptReferences(readonlyReferences, ids.map(id => known.get(id) ?? { media_id: id, url: null, name: '个人参考' }));
    if (resolve(reference_media_ids).length > 16) throw new Error('最多使用 16 张参考图片，请先移除一张个人参考图');
    draft.edit({ reference_media_ids, input_text: remapAssetPromptReferences(value.input_text, resolve(value.reference_media_ids), resolve(reference_media_ids)) });
    if (!await draft.flush()) throw new Error('私人参考图未能保存，请核对生成设置');
  }, [draft.loaded, draft.session, draft.edit, draft.flush, readonlyReferences, referenceImages]);
  return <section className={`shot-image-candidates standard-shot-media${settingsOpen ? '' : ' is-settings-collapsed'}`}>
    <div className="standard-object-preview" aria-label="分镜图片预览">
      <ImageGroupPreview presentation="asset-creation" group={images.group} loading={images.loading} error={images.error} label="分镜图"
        disabled={disabled || busy} imageModelId={actualModelId} onRefresh={() => void images.refresh()}
        onBeforeChange={beforeImageChange} onChanged={onChanged} onAddReference={addReference} onBusyChange={setToolBusy}/>
    </div>
    <ShotGenerationSettings id="storyboard-image-settings" open={settingsOpen} active={active} onClose={onCloseSettings}>
      {information}
      <PrivateReferenceImages draft={draft} disabled={disabled || busy} onBusy={setReferenceBusy} readonlyReferences={readonlyReferences}
        referencesReady={!referencesLoading && !referencesError && uploaded !== null && !references.missingAssetIds.length} onImagesChange={setReferenceImages}
        availableReferences={availableReferences} availableLoading={availableLoading} availableError={availableError} onRefreshAvailable={onRefreshAvailable}/>
      {references.unreadyAssetIds.length > 0 && <Alert quiet persistent type="warning" message={`${references.unreadyAssetIds.length} 项关联素材尚无主图，本次仅使用文字设定。`}/>}
      <GenerationComposer feedbackRef={setModelFeedback} prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })} busy={busy} disabled={disabled || !draft.loaded}
        promptInput={id => <AssetPromptInput id={id} value={draft.value.input_text} onChange={input_text => draft.edit({ input_text })} references={referenceImages} disabled={disabled || busy || !draft.loaded}/>}
        sendDisabled={disabled || toolBusy || referenceBusy || !!blockReason || !!uncertain || recoveryBlocked} sendTitle={sendReason || undefined} blocker={sendReason && !busy && !sendReason.startsWith('正在') ? { message: sendReason, actions: blockerActions } : undefined} onSend={() => void generate()}
        model={<EpisodeModelSelect presentation="standard-creation" optionLayout="compact" feedbackContainer={modelFeedback} kind="image" label="分镜生图模型" value={draft.value.model_config_id ?? modelSelection} disabled={disabled || busy || toolBusy || !draft.loaded}
          onResolvedChange={handleResolved} onChange={id => { draft.edit({ model_config_id: id || null }); onModelChange(id); }}/>} parameters={<AssetGenerationParameters countPlacement="options" value={draft.value} disabled={disabled || busy || !draft.loaded || privateCapsLoading} onChange={draft.edit} capabilities={privateCapabilities}/>}/>
      <CreationDraftFeedback draft={draft}/>
      {blockReason && !blockReason.startsWith('正在') && actualModelId && <Alert quiet={!active || (!privateCapsError && !referencesError)} type={privateCapsError ? 'error' : 'warning'} message={blockReason} action={<Button size="small" onClick={() => { setReferencesRevision(revision => revision + 1); setPrivateCapsRevision(value => value + 1); onRefreshCapabilities(); }}>重新核对</Button>}/>}
      {uncertain && <Alert type="warning" showIcon message="上次图片请求仍待核对，已保留原请求。" action={<Button loading={busy} onClick={() => void resolveUnknown()}>核对原图片请求</Button>}/>}
      {message && !historyOpen && <Alert quiet={!active} type={feedbackType(message)} message={message}/>}
    </ShotGenerationSettings>    {historyOpen && <Dialog title="分镜图片生成记录" className="storyboard-history-dialog standard-creation-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}><div className="shot-image-history-body">
      <ImageGroupHistory kind="shot" targetId={shot.id} disabled={disabled || busy || toolBusy} onBeforeChange={beforeImageChange} onChanged={() => { onChanged(); void images.refresh(); }}/>
      {history.error && <Alert type="error" showIcon message={history.error}/>}
      {message && <Alert type={feedbackType(message)} message={message}/>}
      <details className="shot-task-records"><summary>任务记录 <span>{history.tasks.length} 条</span></summary>      {history.tasks.length > 0 && <div className="writing-task-history"><h4>任务记录</h4>{history.tasks.map((task) => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span><Button onClick={() => { setHistoryOpen(false); setTaskId(task.generation_id); }}>查看任务</Button></div>)}</div>}
      {history.hasMoreTasks && <Button onClick={() => void history.loadMoreTasks()}>加载更多记录</Button>}
</details>
    </div><div className="production-dialog-footer"><span>整组图片按生成顺序保留</span><Button disabled={busy} onClick={() => setHistoryOpen(false)}>关闭</Button></div></Dialog>}
    {taskId && <TaskDetail presentation="standard-creation" id={taskId} onClose={() => setTaskId(null)} onChanged={() => { void history.refresh(); }} onCreated={(task) => { setTaskId(task.generation_id); void history.refresh(); }}/ >}
  </section>;
}
