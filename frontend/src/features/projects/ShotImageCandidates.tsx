import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, InputNumber, Spin } from 'antd';
import { generations } from '../../api/modules/generations';
import { mediaLibrary } from '../../api/modules/media-library';
import { assetLibraries, type AssetRead } from '../../api/modules/assets';
import { ApiError, errorMessage } from '../../api/http';
import type { GenerationDetail, MediaAsset } from '../../api/types/generations';
import type { ShotRead } from '../../api/modules/storyboard';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { TaskDetail } from '../generations/TaskDetail';
import { ImagePreview, PreviewImage } from '../../components/ui/ImagePreview';
import { shotImageApplyRequest, shotImageRequest } from './workflow-contract';
import { ShotReferenceAssets } from './ShotReferenceAssets';
import { imageGenerationBlockReason, summarizeShotReferences, type ImageCapabilities, type PreparedShot } from './shot-image-workflow';
import { useShotImageGeneration } from './useShotImageGeneration';
import { pendingShotAttempt, startShotAttempt, finishShotAttempt } from './shot-image-attempt';

export function ShotImageCandidates({ shot, disabled, modelId, capabilities, capabilitiesLoading, onRefreshCapabilities, onChanged, prepareShot, episodeAspect }: {
  shot: ShotRead; disabled: boolean; modelId: string | undefined; capabilities: ImageCapabilities | null;
  capabilitiesLoading: boolean; onRefreshCapabilities: () => void; onChanged: () => void;
  prepareShot: () => Promise<PreparedShot | null>; episodeAspect: '16:9' | '9:16';
}) {
  const [expanded, setExpanded] = useState(false);
  const history = useShotImageGeneration({ shotId: shot.id, enabled: expanded });
  const [prompt, setPrompt] = useState('');
  const [count, setCount] = useState(1);
  const [preview, setPreview] = useState<MediaAsset | null>(null);
  const [previewDetail, setPreviewDetail] = useState<GenerationDetail | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [uncertain, setUncertain] = useState(() => pendingShotAttempt(shot.id, attemptStorage()) !== null);
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
    if (!expanded) return;
    const controller = new AbortController();
    setReferencesLoading(true); setReferencesError('');
    Promise.all(shot.asset_ids.map((id) => assetLibraries.detail(id, controller.signal)))
      .then((next) => { if (!controller.signal.aborted) setAssets(next); })
      .catch(() => { if (!controller.signal.aborted) setReferencesError('参考素材暂时无法刷新，显示的内容可能不是最新版本。'); })
      .finally(() => { if (!controller.signal.aborted) setReferencesLoading(false); });
    return () => controller.abort();
  }, [expanded, referenceKey, shot.context_hash, referencesRevision]);

  const references = summarizeShotReferences(shot.asset_ids, assets);
  const blockReason = imageGenerationBlockReason(modelId, capabilities, references.referenceMediaIds.length, capabilitiesLoading)
    || (referencesLoading ? '正在核对参考素材…' : referencesError)
    || (references.missingAssetIds.length ? '关联素材尚未加载完整，请刷新核对。' : '');

  async function generate() {
    if (mutation.current || disabled || blockReason || uncertain || !shot.script.trim()) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    let owner: string | undefined;
    try {
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const current = prepared.shot;
      const latestAssets = await Promise.all(current.asset_ids.map((id) => assetLibraries.detail(id)));
      if (!alive.current || !prepared.isCurrent()) return;
      setAssets(latestAssets);
      const latest = summarizeShotReferences(current.asset_ids, latestAssets);
      const reason = imageGenerationBlockReason(modelId, capabilities, latest.referenceMediaIds.length, capabilitiesLoading);
      if (reason) { setMessage(reason); return; }
      if (latest.referenceMediaIds.join(',') !== references.referenceMediaIds.join(',')) {
        setMessage('参考图片已变化，请核对后重新点击生成。'); return;
      }
      const body = { config_id: modelId, ...shotImageRequest(current, prompt, [], count, episodeAspect) };
      const scope = `shot-image:${shot.id}`;
      const key = await requestAttempt(scope, body, attemptStorage());
      if (!alive.current || !prepared.isCurrent()) return;
      owner = crypto.randomUUID();
      startShotAttempt(shot.id, owner, attemptStorage());
      const task = await generations.generateImage(body, key);
      if (finishShotAttempt(shot.id, owner, attemptStorage())) clearAttempt(scope, attemptStorage());
      if (!alive.current) return;
      setUncertain(false); setMessage(`图片任务 ${task.generation_id} 已提交。`);
      void history.refresh();
    } catch (cause) {
      const rejected = cause instanceof ApiError && !!cause.status && cause.status < 500 && cause.status !== 408;
      if (owner && rejected) finishShotAttempt(shot.id, owner, attemptStorage());
      if (alive.current) {
        setUncertain(pendingShotAttempt(shot.id, attemptStorage()) !== null);
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
      if (!window.confirm(`确认采用这张图片？原生成参数：${description}。采用不会改写下次生成设置，旧图会保留在回收记录。`)) return;
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const body = shotImageApplyRequest(prepared.shot, false);
      try { await mediaLibrary.apply(asset.asset_id, body); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'stale_generation_source') throw cause;
        if (!alive.current || !window.confirm('图片基于较早的创作上下文生成。核对预览后仍要采用吗？')) return;
        if (!prepared.isCurrent()) return;
        await mediaLibrary.apply(asset.asset_id, { ...body, acknowledge_stale_source: true });
      }
      if (!alive.current) return;
      setMessage('图片已采用。'); onChanged(); void history.refresh();
    } catch (cause) { if (alive.current) setMessage(errorMessage(cause)); }
    finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  function acknowledgeUnknown() {
    if (!window.confirm('请先查看生成记录确认上次请求是否已创建任务。清除此保护后再次生成可能重复计费，确定已核对并继续？')) return;
    const owner = pendingShotAttempt(shot.id, attemptStorage());
    if (owner) finishShotAttempt(shot.id, owner, attemptStorage());
    setUncertain(false);
  }

  return <section className="shot-image-candidates">
    <div className="dialog-actions shot-image-heading"><h4>分镜图片</h4><Button aria-expanded={expanded} disabled={busy} onClick={() => setExpanded(!expanded)}>{expanded ? '收起图片操作' : '展开图片操作'}</Button></div>
    {shot.image && <div><strong>当前采用</strong>{shot.image.url && <PreviewImage triggerClassName="shot-current-preview" src={shot.image.url} alt="当前采用分镜图"/>}{shot.image.is_stale && <Alert type="warning" message="创作内容已变化，请重新核对当前图片。"/>}</div>}
    {expanded && <>
      <ShotReferenceAssets assetIds={shot.asset_ids} assets={assets} loading={referencesLoading} error={referencesError} onRefresh={() => setReferencesRevision((revision) => revision + 1)}/>
      <div className="shot-image-controls"><label>补充画面要求 <small>选填</small><Input.TextArea value={prompt} disabled={disabled || busy} maxLength={4000} rows={3} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：逆光、雨夜街道，突出人物眼神"/></label><label>生成张数<InputNumber aria-label="生成张数" disabled={disabled || busy} min={1} max={4} value={count} onChange={(next) => setCount(next ?? 1)}/><small>每次 1–4 张</small></label></div>
      {blockReason && <Alert type="warning" showIcon message={blockReason} action={<Button size="small" onClick={onRefreshCapabilities}>刷新模型能力</Button>}/>}
      {uncertain && <Alert type="warning" showIcon message="上次生成请求的受理结果尚未确认，请先查看记录，避免重复计费。" action={<Button disabled={busy} onClick={acknowledgeUnknown}>已核对记录</Button>}/>}
      <div className="dialog-actions"><Button type="primary" loading={busy} disabled={disabled || !!blockReason || uncertain || !shot.script.trim()} onClick={() => void generate()}>生成图片</Button><Button loading={history.loading} onClick={() => { void history.refresh(); setReferencesRevision((revision) => revision + 1); onChanged(); }}>刷新状态与候选</Button></div>
      {message && <Alert type="info" showIcon message={message}/>}{history.error && <Alert type="warning" showIcon message={history.error}/>}
      {history.tasks.length > 0 && <details className="writing-task-history"><summary>生成记录（已加载 {history.tasks.length}）</summary>{history.tasks.map((task) => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span><Button onClick={() => setTaskId(task.generation_id)}>查看任务</Button></div>)}</details>}
      {history.hasMoreTasks && <Button onClick={() => void history.loadMoreTasks()}>加载更多记录</Button>}
      {!shot.script.trim() && <p className="episode-help">先填写本镜脚本，再生成图片。</p>}
      {history.loading && !history.candidates.length ? <Spin/> : <div className="image-candidate-grid">{history.candidates.map((asset) => <article className="image-candidate" key={asset.asset_id}>
        {asset.url ? <button type="button" className="asset-library-preview" aria-label={`预览${asset.name}`} aria-haspopup="dialog" onClick={() => void openPreview(asset)}><img src={asset.url} alt={asset.name}/></button> : <p>预览链接不可用，请刷新候选</p>}
        <Button disabled={disabled || busy || shot.image?.media_id === asset.media_id} onClick={() => void apply(asset)}>{shot.image?.media_id === asset.media_id ? '当前采用' : '确认采用'}</Button>
      </article>)}</div>}
      {history.hasMoreCandidates && <Button onClick={() => void history.loadMoreCandidates()}>加载更多候选</Button>}
      {!history.loading && !history.candidates.length && <p className="episode-help">生成的图片会作为候选保留，预览后再确认采用。</p>}
    </>}
    {taskId && <TaskDetail id={taskId} onClose={() => setTaskId(null)} onChanged={() => { void history.refresh(); }} onCreated={(task) => { setTaskId(task.generation_id); void history.refresh(); }}/ >}
    {preview?.url && <ImagePreview title="分镜图片预览" src={preview.url} alt={preview.name} canClose={!busy} onClose={() => { previewSequence.current++; setPreview(null); }} footer={<>
      <span>{previewDetail ? `原生成参数：${previewDetail.source?.scene === 'shot_image' ? previewDetail.source.layout : '未知'} · ${previewDetail.parameters.aspect ?? '未知画幅'} · ${previewDetail.parameters.resolution ?? '未知分辨率'}` : '正在读取原生成参数…'}</span>
      <Button disabled={busy} onClick={() => setPreview(null)}>关闭</Button><Button type="primary" loading={busy} disabled={disabled || busy || !previewDetail || shot.image?.media_id === preview.media_id} onClick={() => void apply(preview)}>确认采用</Button>
    </>}/>}
  </section>;
}
