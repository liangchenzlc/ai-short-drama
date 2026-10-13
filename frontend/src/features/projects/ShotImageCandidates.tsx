import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Select, Spin } from 'antd';
import { generations } from '../../api/modules/generations';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { mediaLibrary } from '../../api/modules/media-library';
import { assetLibraries, type AssetRead } from '../../api/modules/assets';
import { ApiError, errorMessage } from '../../api/http';
import type { GenerationDetail, MediaAsset } from '../../api/types/generations';
import type { ShotRead } from '../../api/modules/storyboard';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { Dialog } from '../../components/ui/Dialog';
import { generationReferences, type ReferenceImage } from '../../api/modules/generation-references';
import { TaskDetail } from '../generations/TaskDetail';
import { ImagePreview, PreviewImage } from '../../components/ui/ImagePreview';
import { shotImageApplyRequest, shotImageRequest } from './workflow-contract';
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

export function ShotImageCandidates({ shot, disabled, modelId, capabilities, onRefreshCapabilities, onChanged, prepareShot, episodeAspect, projectId, episodeId, modelSelection, onModelChange, onResolvedModel, registerBarrier, information, settingsOpen = true, active, onCloseSettings }: {
  projectId: string; episodeId: string; modelSelection: string; onModelChange: (id: string) => void;
  onResolvedModel: (id: string | undefined) => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void; information?: ReactNode; settingsOpen?: boolean;
  active: boolean; onCloseSettings: () => void;
  shot: ShotRead; disabled: boolean; modelId: string | undefined; capabilities: ImageCapabilities | null;
  capabilitiesLoading: boolean; onRefreshCapabilities: () => void; onChanged: () => void;
  prepareShot: (purpose?: 'generation' | 'adoption') => Promise<PreparedShot | null>; episodeAspect: '16:9' | '9:16'; settings?: ReactNode;
}) {
  const expanded = true;
  const draft = useCreationDraft({ projectId, episodeId, objectId: shot.id, purpose: 'storyboard_image', readOnly: disabled, registerBarrier, initialValue: { model_config_id: modelId ?? null, image_settings: { aspect: shot.image_settings.aspect === 'inherit' ? episodeAspect : shot.image_settings.aspect, resolution: shot.image_settings.resolution, count: 1 } } });
  const [selectedModelId, setSelectedModelId] = useState<string>();
  const actualModelId = selectedModelId;
  const resolved = useRef({ onResolvedModel, draft }); resolved.current = { onResolvedModel, draft };
  const handleResolved = useCallback((id: string | undefined) => {
    setSelectedModelId(id); resolved.current.onResolvedModel(id);
  }, []);
  useEffect(() => {
    if (actualModelId && draft.loaded && !draft.value.model_config_id) draft.edit({ model_config_id: actualModelId });
  }, [actualModelId, draft.loaded, draft.value.model_config_id, draft.session]);
  const [privateCapabilities, setPrivateCapabilities] = useState<ImageCapabilities | null>(null);
  const [privateCapsLoading, setPrivateCapsLoading] = useState(false);
  const [privateCapsError, setPrivateCapsError] = useState('');
  const [privateCapsRevision, setPrivateCapsRevision] = useState(0);
  const selectedSettings = draft.value.image_settings ?? { aspect: episodeAspect, resolution: shot.image_settings.resolution, count: 1 };
  useEffect(() => { const controller = new AbortController(); setPrivateCapsLoading(!!actualModelId); setPrivateCapabilities(null); setPrivateCapsError('');
    if (actualModelId) void aiModelConfigs.capabilities(actualModelId, controller.signal).then(result => { if (!controller.signal.aborted) setPrivateCapabilities(result); }).catch(cause => { if (!controller.signal.aborted) setPrivateCapsError(`图片模型能力读取失败：${errorMessage(cause)}`); }).finally(() => { if (!controller.signal.aborted) setPrivateCapsLoading(false); });
    return () => controller.abort();
  }, [actualModelId, privateCapsRevision]);
  useEffect(() => {
    const refresh = () => setPrivateCapsRevision(value => value + 1);
    window.addEventListener('focus', refresh); window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    return () => { window.removeEventListener('focus', refresh); window.removeEventListener(AI_CONFIGS_CHANGED, refresh); };
  }, []);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [uploaded, setUploaded] = useState<ReferenceImage[] | null>(null);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const history = useShotImageGeneration({ shotId: shot.id, enabled: true });

  const [showCurrent, setShowCurrent] = useState(false);
  const [preview, setPreview] = useState<MediaAsset | null>(null);
  const [previewDetail, setPreviewDetail] = useState<GenerationDetail | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const attemptScope = creationScope(`shot-image-submission:${projectId}:${episodeId}:${shot.id}`);
  const currentScope = useRef(attemptScope); currentScope.current = attemptScope;
  const [uncertain, setUncertain] = useState<PendingShotGeneration | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const [assets, setAssets] = useState<AssetRead[]>([]);
  const [referencesLoading, setReferencesLoading] = useState(false);
  const [referencesError, setReferencesError] = useState('');
  const [referencesRevision, setReferencesRevision] = useState(0);
  const mutation = useRef(false);
  const alive = useRef(true);
  const previewSequence = useRef(0);
  const referenceKey = shot.asset_ids.join(',');

  useEffect(() => { alive.current = true; return () => { alive.current = false; previewSequence.current++; }; }, []);
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

  async function generate() {
    if (mutation.current || disabled || referenceBusy || blockReason || uncertain || recoveryBlocked || !shot.script.trim() || !shot.title?.trim()) return;
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
      if (latest.referenceMediaIds.join(',') !== references.referenceMediaIds.join(',')) {
        setMessage('参考图片已变化，请核对后重新点击生成。'); return;
      }
      const request = shotImageRequest(current, '', [], savedDraft.image_settings?.count ?? 1, episodeAspect);
      const body = { ...request, config_id: savedDraft.model_config_id ?? actualModelId, source: { ...request.source, draft_id: savedDraft.id, draft_version: savedDraft.draft_version }, parameters: { ...request.parameters, ...(savedDraft.image_settings ?? {}) } };
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
      setShowCurrent(false); setUncertain(null); setMessage(`图片任务 ${task.generation_id} 已提交。`);
      void history.refresh();
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

  async function openPreview(asset: MediaAsset) {
    const sequence = ++previewSequence.current;
    setPreview(asset); setPreviewDetail(null); setMessage('');
    try {
      const detail = await generations.detail(asset.generation_id);
      if (alive.current && sequence === previewSequence.current) setPreviewDetail(detail);
    } catch (cause) { if (alive.current && sequence === previewSequence.current) setMessage(errorMessage(cause)); }
  }

  async function apply(asset: MediaAsset) {
    if (mutation.current || disabled) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    try {
      const detail = await generations.detail(asset.generation_id);
      if (!alive.current) return;
      const source = detail.source;
      if (source?.scene !== 'shot_image' || source.shot_id !== shot.id || !source.layout || !detail.parameters.aspect || !detail.parameters.resolution) {
        setMessage('候选来源或生成参数不完整，无法直接采用；请查看任务详情。'); return;
      }
      const description = `${source.layout} · ${detail.parameters.aspect} · ${detail.parameters.resolution}`;
      if (!await confirmAction(`确认采用这张图片？原生成参数：${description}。采用不会改写下次生成设置，旧图会保留在回收记录。`)) return;
      prepared = await prepareShot('adoption');
      if (!alive.current) return;
      if (!prepared || !prepared.isCurrent()) {
        setMessage('分镜尚未保存成功或内容已变化，本次未采用图片。请关闭预览核对分镜提示后重试。'); return;
      }
      const body = shotImageApplyRequest(prepared.shot, false);
      try { await mediaLibrary.apply(asset.asset_id, body); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'stale_generation_source') throw cause;
        if (!alive.current || !await confirmAction('图片基于较早的创作上下文生成。核对预览后仍要采用吗？')) return;
        if (!prepared.isCurrent()) {
          setMessage('分镜状态已变化，本次未采用图片，请核对后重试。'); return;
        }
        await mediaLibrary.apply(asset.asset_id, { ...body, acknowledge_stale_source: true });
      }
      if (!alive.current) return;
      prepared.release();
      previewSequence.current++;
      setPreview(null); setHistoryOpen(false); setShowCurrent(true);
      setMessage('图片已采用。'); onChanged(); void history.refresh();
    } catch (cause) { if (alive.current) setMessage(errorMessage(cause)); }
    finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  async function resolveUnknown() {
    if (!uncertain || mutation.current) return;
    mutation.current = true; setBusy(true);
    try {
      const receipt = await generations.lookup('image', uncertain.key);
      if (!alive.current || currentScope.current !== attemptScope) return;
      if (!receipt) { setMessage('暂未查到原图片任务，请稍后再次核对。当前不会重新提交。'); return; }
      clearRecovery(attemptScope); clearAttempt(`shot-image:${shot.id}`, attemptStorage());
      setUncertain(null); setShowCurrent(false); setMessage(`已找到原图片任务 ${receipt.generation_id}。`);
      void history.refresh();
    } catch (cause) { if (alive.current && currentScope.current === attemptScope) setMessage(`原图片请求核对失败：${errorMessage(cause)}`); }
    finally { mutation.current = false; if (alive.current) setBusy(false); }
  }

  const latestCandidate = showCurrent ? null : preview ?? history.candidates.find(item => item.media_id !== shot.image?.media_id) ?? null;
  return <section className="shot-image-candidates standard-shot-media">
    <div className="standard-object-preview" aria-label="分镜图片预览">
      {latestCandidate?.url ? <><PreviewImage triggerClassName="shot-current-preview" src={latestCandidate.url} alt="未采用分镜图片结果"/><div className="standard-candidate-actions"><span>未采用</span><Button disabled={disabled || busy} onClick={() => void apply(latestCandidate)}>采用此结果</Button><Button onClick={() => { setPreview(null); setShowCurrent(true); }}>查看当前作品</Button></div></>
        : shot.image?.url ? <PreviewImage triggerClassName="shot-current-preview" src={shot.image.url} alt="当前采用分镜图"/>
          : <div className="studio-empty"><h3>待制作分镜图</h3><p>填写镜头名称与脚本后生成图片。</p></div>}
      {shot.image?.is_stale && <Alert type="warning" message="创作内容已变化，请核对当前图片。"/>}
    </div>
    <ShotGenerationSettings open={settingsOpen} active={active} onClose={onCloseSettings}>
      {information}
      <PrivateReferenceImages draft={draft} disabled={disabled || busy} onBusy={setReferenceBusy}/>
      <p className="episode-help">去重合计 {referenceCount.totalCount}/16 张参考图片</p>
      {references.unreadyAssetIds.length > 0 && <p className="episode-help">{references.unreadyAssetIds.length} 项关联素材尚未确认图片，本次仅使用文字设定。</p>}
      <GenerationComposer prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })} busy={busy} disabled={disabled || !draft.loaded}
        sendDisabled={disabled || referenceBusy || !!blockReason || !!uncertain || recoveryBlocked || !shot.title?.trim() || !shot.script.trim()} onSend={() => void generate()}
        model={<EpisodeModelSelect kind="image" label="分镜生图模型" value={draft.value.model_config_id ?? modelSelection} disabled={disabled || busy || !draft.loaded}
          onResolvedChange={handleResolved} onChange={id => { draft.edit({ model_config_id: id || null }); onModelChange(id); }}/>} parameters={<>
          <Select aria-label="分镜图片比例与分辨率" value={`${selectedSettings.aspect}/${selectedSettings.resolution}`} disabled={disabled || busy}
            options={['16:9','9:16','1:1','4:3','3:4'].flatMap(aspect => ['1K','2K','4K'].map(resolution => ({ value: `${aspect}/${resolution}`, label: `${aspect} · ${resolution}` })))}
            onChange={next => { const [aspect, resolution] = next.split('/'); draft.edit({ image_settings: { ...selectedSettings, aspect, resolution } }); }}/>
          <Select aria-label="图片数量" value={selectedSettings.count} disabled={disabled || busy} options={[1,2,4].map(value => ({ value, label: `${value} 张` }))}
            onChange={count => draft.edit({ image_settings: { ...selectedSettings, count } })}/>
        </>}/>
      <Button type="text" disabled={busy || referenceBusy} onClick={() => setHistoryOpen(true)}>图片生成记录</Button>
      <CreationDraftFeedback draft={draft}/>
      {blockReason && <Alert type="warning" showIcon message={blockReason} action={<Button size="small" onClick={() => { setReferencesRevision(revision => revision + 1); setPrivateCapsRevision(value => value + 1); onRefreshCapabilities(); }}>重新核对</Button>}/>}
      {uncertain && <Alert type="warning" showIcon message="上次图片请求仍待核对，已保留原请求。" action={<Button loading={busy} onClick={() => void resolveUnknown()}>核对原图片请求</Button>}/>}
      {message && <Alert type="info" showIcon message={message}/>}{history.error && <Alert type="warning" showIcon message={history.error}/>}
    </ShotGenerationSettings>    {historyOpen && <Dialog title="分镜图片生成记录" className="storyboard-history-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}><div className="shot-image-history-body">
      <div className="candidate-history-toolbar"><span>候选图片需确认采用后才会替换当前画面。</span><Button loading={history.loading} onClick={() => void history.refresh()}>刷新候选</Button></div>
      {history.error && <Alert type="error" showIcon message={history.error}/>}
      {message && <Alert type="info" showIcon message={message}/>}
      {!shot.script.trim() && <p className="episode-help">先填写本镜脚本，再生成图片。</p>}
      {history.loading && !history.candidates.length ? <Spin/> : <div className="image-candidate-grid">{history.candidates.map((asset) => <article className="image-candidate" key={asset.asset_id}>
        {asset.url ? <button type="button" className="asset-library-preview" aria-label={`预览${asset.name}`} aria-haspopup="dialog" onClick={() => void openPreview(asset)}><img src={asset.url} alt={asset.name}/></button> : <p>预览链接不可用，请刷新候选</p>}
        <Button disabled={disabled || busy || shot.image?.media_id === asset.media_id} onClick={() => void apply(asset)}>{shot.image?.media_id === asset.media_id ? '当前采用' : '确认采用'}</Button>
      </article>)}</div>}
      {history.hasMoreCandidates && <Button onClick={() => void history.loadMoreCandidates()}>加载更多候选</Button>}
      {!history.loading && !history.error && !history.candidates.length && <p className="episode-help">生成的图片会作为候选保留，预览后再确认采用。</p>}
      <details className="shot-task-records"><summary>任务记录 <span>{history.tasks.length} 条</span></summary>      {history.tasks.length > 0 && <div className="writing-task-history"><h4>任务记录</h4>{history.tasks.map((task) => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span><Button onClick={() => { setHistoryOpen(false); setTaskId(task.generation_id); }}>查看任务</Button></div>)}</div>}
      {history.hasMoreTasks && <Button onClick={() => void history.loadMoreTasks()}>加载更多记录</Button>}
</details>
    </div><div className="production-dialog-footer"><span>候选保留原生成参数</span><Button disabled={busy} onClick={() => setHistoryOpen(false)}>关闭</Button></div></Dialog>}
    {taskId && <TaskDetail id={taskId} onClose={() => setTaskId(null)} onChanged={() => { void history.refresh(); }} onCreated={(task) => { setTaskId(task.generation_id); void history.refresh(); }}/ >}
    {preview?.url && <ImagePreview title="分镜图片预览" src={preview.url} alt={preview.name} canClose={!busy} onClose={() => { previewSequence.current++; setPreview(null); }} footer={<>
      {message && <Alert type="info" showIcon message={message}/>}
      <span>{previewDetail ? `原生成参数：${previewDetail.source?.scene === 'shot_image' ? previewDetail.source.layout : '未知'} · ${previewDetail.parameters.aspect ?? '未知画幅'} · ${previewDetail.parameters.resolution ?? '未知分辨率'}` : '正在读取原生成参数…'}</span>
      <Button disabled={busy} onClick={() => setPreview(null)}>关闭</Button><Button type="primary" loading={busy} disabled={disabled || busy || !previewDetail || shot.image?.media_id === preview.media_id} onClick={() => void apply(preview)}>确认采用</Button>
    </>}/>}
  </section>;
}
