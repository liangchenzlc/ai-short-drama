import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, InputNumber, Select, Spin } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import type { ShotRead } from '../../api/modules/storyboard';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { generations } from '../../api/modules/generations';
import { mediaLibrary } from '../../api/modules/media-library';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { MediaAsset } from '../../api/types/generations';
import { Dialog } from '../../components/ui/Dialog';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { AI_CONFIGS_CHANGED } from '../ai-config/config-events';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { TaskDetail } from '../generations/TaskDetail';
import { taskLabel } from '../generations/presentation';
import { EpisodeModelSelect } from './EpisodeModelSelect';
import { pendingShotAttempt, startShotAttempt, finishShotAttempt } from './shot-image-attempt';
import type { PreparedShot } from './shot-image-workflow';
import { useShotImageGeneration } from './useShotImageGeneration';
import { videoBlockReason, shotVideoRequest, shotVideoApplyRequest } from './shot-video-workflow';

export function ShotVideoCandidates({ shot, disabled, model, onModelChange, onEdit, prepareShot, onChanged }: {
  shot: ShotRead; disabled: boolean; model: string; onModelChange: (id: string) => void;
  onEdit: (patch: Partial<Pick<ShotRead, 'video_prompt' | 'video_settings'>>) => void;
  prepareShot: () => Promise<PreparedShot | null>; onChanged: () => void;
}) {
  const [modelId, setModelId] = useState<string>();
  const onResolved = useCallback((id: string | undefined) => setModelId(id), []);
  const [capabilities, setCapabilities] = useState<AiModelCapabilitiesDto | null>(null);
  const [capLoading, setCapLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const scope = `shot-video:${shot.id}`;
  const [uncertain, setUncertain] = useState(() => !!pendingShotAttempt(scope, attemptStorage()));
  const history = useShotImageGeneration({ shotId: shot.id, enabled: true, kind: 'video' });
  const mutation = useRef(false);
  const alive = useRef(true);
  const currentModel = useRef(modelId);
  currentModel.current = modelId;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setPreviewFailed(false); }, [shot.video?.url]);
  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    window.addEventListener('focus', refresh); window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    return () => { window.removeEventListener('focus', refresh); window.removeEventListener(AI_CONFIGS_CHANGED, refresh); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setCapabilities(null); setCapLoading(!!modelId);
    if (modelId) void aiModelConfigs.capabilities(modelId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setCapabilities(result); })
      .catch(cause => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setCapLoading(false); });
    return () => controller.abort();
  }, [modelId, revision]);

  const reason = videoBlockReason(shot, modelId, capabilities, capLoading);
  const durations = capabilities?.video_input?.duration_seconds;
  async function generate() {
    if (mutation.current || disabled || reason || uncertain || !modelId) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    let owner: string | undefined;
    try {
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const latestCaps = await aiModelConfigs.capabilities(modelId);
      if (!alive.current || !prepared.isCurrent() || currentModel.current !== modelId) return;
      setCapabilities(latestCaps);
      const block = videoBlockReason(prepared.shot, modelId, latestCaps, false);
      if (block) { setMessage(block); return; }
      const body = shotVideoRequest(prepared.shot, modelId);
      const key = await requestAttempt(scope, body, attemptStorage());
      if (!alive.current || !prepared.isCurrent()) return;
      owner = crypto.randomUUID(); startShotAttempt(scope, owner, attemptStorage());
      const task = await generations.generateVideo(body, key);
      if (finishShotAttempt(scope, owner, attemptStorage())) clearAttempt(scope, attemptStorage());
      if (!alive.current) return;
      setUncertain(false); setHistoryOpen(true); setMessage(`视频任务 ${task.generation_id} 已提交。`);
      void history.refresh();
    } catch (cause) {
      const rejected = cause instanceof ApiError && !!cause.status && cause.status < 500 && cause.status !== 408;
      if (owner && rejected) finishShotAttempt(scope, owner, attemptStorage());
      if (alive.current) { setUncertain(!!pendingShotAttempt(scope, attemptStorage())); setMessage(errorMessage(cause)); }
    } finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  async function apply(asset: MediaAsset) {
    if (disabled || mutation.current) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    try {
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const body = shotVideoApplyRequest(prepared.shot);
      try { await mediaLibrary.apply(asset.asset_id, body); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'stale_generation_source') throw cause;
        if (!window.confirm('此视频使用的分镜、提示词或参考图与当前内容不同。已核对视频，仍要采用吗？')) return;
        if (!prepared.isCurrent() || !alive.current) return;
        await mediaLibrary.apply(asset.asset_id, { ...body, acknowledge_stale_source: true });
      }
      if (!alive.current) return;
      prepared.release(); setHistoryOpen(false); setMessage('视频已采用。'); onChanged(); void history.refresh();
    } catch (cause) { if (alive.current) setMessage(errorMessage(cause)); }
    finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  function acknowledgeUnknown() {
    if (!window.confirm('请先查看生成记录核对上次请求是否已受理。解除保护后再次生成可能重复计费，确定继续？')) return;
    const owner = pendingShotAttempt(scope, attemptStorage());
    if (owner) finishShotAttempt(scope, owner, attemptStorage());
    setUncertain(false);
  }

  return <section className="shot-video-workspace" aria-label="分镜视频制作">
    <div className="shot-video-preview-row">
      <div className="shot-video-reference"><strong>全能参考图</strong>{shot.image?.url
        ? <PreviewImage src={shot.image.url} alt="当前采用的分镜参考图，支持单图或宫格" triggerClassName="shot-current-preview"/>
        : <p className="episode-help">采用分镜图后自动关联为全能参考图。</p>}
        <p className="episode-help">支持单图、四宫格、五宫格等分镜参考。按提示词组织动作和运镜，视频画幅沿用分集设置。</p></div>
      {shot.video && <div className="shot-video-current"><strong>当前采用视频 · {shot.video.duration_ms / 1000} 秒</strong>
        {shot.video.url && !previewFailed ? <video src={shot.video.url} controls playsInline preload="metadata" aria-label="当前采用分镜视频" onError={() => setPreviewFailed(true)}/>
          : <Button onClick={onChanged}>刷新视频预览</Button>}
        {shot.video.is_stale && <Alert type="warning" showIcon message="参考图或创作内容已变化，请核对当前视频。"/>}</div>}
    </div>
    <label className="writing-control"><span>视频提示词</span><Input.TextArea aria-label="视频提示词" autoSize={{ minRows: 5, maxRows: 12 }} maxLength={16000}
      value={shot.video_prompt || shot.video_default_prompt} disabled={disabled || busy} onChange={event => onEdit({ video_prompt: event.target.value })}/></label>
    <div className="shot-video-prompt-actions">
      <Button type="link" disabled={disabled || busy || !shot.video_prompt} onClick={() => { if (window.confirm('恢复默认内容将替换本镜已编辑的视频提示词，确定继续？')) onEdit({ video_prompt: '' }); }}>恢复分镜默认内容</Button></div>
    <div className="shot-generation-toolbar">
      <label>视频模型<EpisodeModelSelect kind="video" value={model} label="视频模型" disabled={disabled || busy} onResolvedChange={onResolved} onChange={id => { setModelId(undefined); setCapabilities(null); onModelChange(id); }}/></label>
      <label>视频清晰度<Select aria-label="视频清晰度" value={shot.video_settings.resolution} disabled={disabled || busy}
        options={['480p', '720p', '1080p'].map(value => ({ value, label: value, disabled: !!capabilities?.video_input?.resolutions && !capabilities.video_input.resolutions.includes(value) }))}
        onChange={resolution => onEdit({ video_settings: { ...shot.video_settings, resolution } })}/></label>
      <label>视频时长<InputNumber aria-label="视频时长" suffix="秒" precision={0} step={1}
        min={durations?.length ? Math.min(...durations) : 1} max={durations?.length ? Math.max(...durations) : 3600}
        value={(shot.video_settings.duration_ms ?? shot.duration_ms) / 1000} disabled={disabled || busy}
        onChange={seconds => { if (seconds !== null) onEdit({ video_settings: { ...shot.video_settings, duration_ms: seconds * 1000 } }); }}/></label>
      <Button type="primary" loading={busy} disabled={disabled || !!reason || uncertain} onClick={() => void generate()}>生成视频</Button>
      <Button disabled={busy} onClick={() => setHistoryOpen(true)}>视频生成记录</Button>
    </div>
    {reason && <Alert type="warning" showIcon message={reason} action={<Button size="small" onClick={() => setRevision(value => value + 1)}>重新核对</Button>}/>}
    {uncertain && <Alert type="warning" showIcon message="上次视频请求的受理结果尚未确认，请先查看生成记录。" action={<Button disabled={busy} onClick={acknowledgeUnknown}>已核对记录</Button>}/>}
    {message && <Alert type="info" showIcon message={message}/>}
    {history.tasks.some(task => task.status === 'queued' || task.status === 'running') && <p role="status" className="episode-help">视频正在生成，离开页面后任务仍会继续。</p>}
    {historyOpen && <Dialog title="分镜视频生成记录" className="storyboard-history-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}>
      <div className="shot-image-history-body">
        <div className="candidate-history-toolbar"><span>播放核对后采用，生成不会自动替换当前视频。</span><Button loading={history.loading} onClick={() => void history.refresh()}>刷新候选</Button></div>
        {message && <Alert type="info" showIcon message={message}/>}{history.error && <Alert type="error" showIcon message={history.error}/>}
        {history.loading && !history.candidates.length ? <Spin/> : !history.candidates.length ? <p className="episode-help">暂无视频候选，生成完成后会显示在这里。</p> :
          <div className="shot-video-candidates">{history.candidates.map(asset => <article key={asset.asset_id}>
            <CandidateVideo asset={asset} onRefresh={() => void history.refresh()}/>
            <div className="candidate-history-toolbar"><span>{asset.name}{asset.duration_ms ? ` · ${asset.duration_ms / 1000} 秒` : ''}</span>
              <Button type="primary" disabled={disabled || busy || shot.video?.media_id === asset.media_id} onClick={() => void apply(asset)}>{shot.video?.media_id === asset.media_id ? '当前采用' : '确认采用'}</Button></div>
          </article>)}</div>}
        {history.hasMoreCandidates && <Button onClick={() => void history.loadMoreCandidates()}>加载更多视频</Button>}
        <details className="shot-task-records" open={!history.candidates.length}><summary>任务记录 · {history.tasks.length} 条</summary>
          {history.tasks.map(task => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)}{task.error ? ` · ${task.error.message}` : ''}</span><Button onClick={() => { setHistoryOpen(false); setTaskId(task.generation_id); }}>查看任务</Button></div>)}
          {history.hasMoreTasks && <Button onClick={() => void history.loadMoreTasks()}>加载更多任务</Button>}
        </details>
      </div>
      <div className="production-dialog-footer"><span>历史视频保留生成时的参考图和提示词。</span><Button disabled={busy} onClick={() => setHistoryOpen(false)}>关闭</Button></div>
    </Dialog>}
    {taskId && <TaskDetail id={taskId} onClose={() => setTaskId(null)} onChanged={() => { void history.refresh(); }} onCreated={task => { setTaskId(task.generation_id); void history.refresh(); }}/ >}
  </section>;
}

function CandidateVideo({ asset, onRefresh }: { asset: MediaAsset; onRefresh: () => void }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [asset.url]);
  return asset.url && !failed ? <video src={asset.url} controls playsInline preload="metadata" aria-label={`候选视频 ${asset.name}`} onError={() => setFailed(true)}/>
    : <div className="studio-empty"><p>视频暂时无法播放，请刷新预览链接。</p><Button onClick={onRefresh}>刷新预览</Button></div>;
}
