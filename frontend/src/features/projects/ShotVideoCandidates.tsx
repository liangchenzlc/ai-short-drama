import { confirmAction } from '../../components/ui/confirm';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, InputNumber, Select, Spin } from 'antd';
import { ApiError, errorMessage } from '../../api/http';
import type { ShotRead } from '../../api/modules/storyboard';
import { aiModelConfigs } from '../../api/modules/ai-model-configs';
import { generations } from '../../api/modules/generations';
import { mediaLibrary } from '../../api/modules/media-library';
import type { AiModelCapabilitiesDto } from '../../api/types/ai-model-configs';
import type { MediaAsset } from '../../api/types/generations';
import { GenerationComposer } from './GenerationComposer';
import { useCreationDraft } from './useCreationDraft';
import { CreationDraftFeedback } from './CreationDraftFeedback';
import type { NavigationBarrier } from './writing-navigation';
import { Dialog } from '../../components/ui/Dialog';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { AI_CONFIGS_CHANGED } from '../ai-config/config-events';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { TaskDetail } from '../generations/TaskDetail';
import { taskLabel } from '../generations/presentation';
import { EpisodeModelSelect } from './EpisodeModelSelect';
import { creationScope, clearRecovery, readRecovery, saveRecovery } from './creation-recovery';
import { isPendingShotGeneration, type PendingShotGeneration } from './shot-generation-submission';
import { ShotGenerationSettings } from './ShotGenerationSettings';
import type { PreparedShot } from './shot-image-workflow';
import { useShotImageGeneration } from './useShotImageGeneration';
import { videoBlockReason, shotVideoRequest, shotVideoApplyRequest } from './shot-video-workflow';

export function ShotVideoCandidates({ shot, disabled, model, onModelChange, prepareShot, onChanged, projectId, episodeId, episodeAspect, registerBarrier, information, settingsOpen = true, active, onCloseSettings }: {
  projectId: string; episodeId: string; episodeAspect: '16:9' | '9:16'; registerBarrier: (barrier: NavigationBarrier | null) => void; information?: ReactNode; settingsOpen?: boolean;
  active: boolean; onCloseSettings: () => void;
  shot: ShotRead; disabled: boolean; model: string; onModelChange: (id: string) => void;
  prepareShot: () => Promise<PreparedShot | null>; onChanged: () => void;
}) {
  const draft = useCreationDraft({ projectId, episodeId, objectId: shot.id, purpose: 'storyboard_video', readOnly: disabled, registerBarrier, initialValue: { model_config_id: model || null, input_text: shot.video_prompt || '', video_settings: { aspect: episodeAspect, resolution: shot.video_settings.resolution, duration_ms: shot.video_settings.duration_ms ?? shot.duration_ms } } });
  const selectedSettings = draft.value.video_settings ?? { aspect: episodeAspect, resolution: shot.video_settings.resolution, duration_ms: shot.video_settings.duration_ms ?? shot.duration_ms };
  const [showCurrent, setShowCurrent] = useState(false);
  const [modelId, setModelId] = useState<string>();
  const onResolved = useCallback((id: string | undefined) => {
    setModelId(id);
  }, []);
  useEffect(() => {
    if (modelId && draft.loaded && !draft.value.model_config_id) draft.edit({ model_config_id: modelId });
  }, [modelId, draft.loaded, draft.value.model_config_id, draft.session]);
  const [capabilities, setCapabilities] = useState<AiModelCapabilitiesDto | null>(null);
  const [capLoading, setCapLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const scope = `shot-video:${shot.id}`;
  const attemptScope = creationScope(`shot-video-submission:${projectId}:${episodeId}:${shot.id}`);
  const currentScope = useRef(attemptScope); currentScope.current = attemptScope;
  const [uncertain, setUncertain] = useState<PendingShotGeneration | null>(null);
  const [recoveryBlocked, setRecoveryBlocked] = useState(false);
  const history = useShotImageGeneration({ shotId: shot.id, enabled: true, kind: 'video' });
  const mutation = useRef(false);
  const alive = useRef(true);
  const currentModel = useRef(modelId);
  currentModel.current = modelId;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    setUncertain(null); setRecoveryBlocked(false);
    try {
      const pending = readRecovery(attemptScope, isPendingShotGeneration);
      if (pending && (pending.body.source?.scene !== 'shot_video' || pending.body.source.shot_id !== shot.id)) throw new Error('生成记录与镜头不一致。');
      setUncertain(pending);
    } catch (cause) { setRecoveryBlocked(true); setMessage(errorMessage(cause)); }
  }, [attemptScope, shot.id]);
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

  const effectiveShot = { ...shot, video_settings: { ...shot.video_settings, resolution: selectedSettings.resolution as ShotRead['video_settings']['resolution'] ?? shot.video_settings.resolution, duration_ms: selectedSettings.duration_ms } };
  const reason = !draft.loaded ? '正在载入个人视频设置…' : draft.status === 'error' || draft.status === 'conflict' ? '请先处理个人生成设置保存提示。' : !shot.title?.trim() ? '请先填写镜头名称。' : videoBlockReason(effectiveShot, modelId, capabilities, capLoading);
  const durations = capabilities?.video_input?.duration_seconds;
  async function generate() {
    if (mutation.current || disabled || reason || uncertain || recoveryBlocked || !modelId) return;
    mutation.current = true; setBusy(true); setMessage('');
    let prepared: PreparedShot | null = null;
    let submitted = false;
    try {
      prepared = await prepareShot();
      if (!prepared || !alive.current || !prepared.isCurrent()) return;
      const savedDraft = await draft.flush();
      if (!savedDraft || !prepared.isCurrent() || !alive.current) return;
      const latestCaps = await aiModelConfigs.capabilities(modelId);
      if (!alive.current || !prepared.isCurrent() || currentModel.current !== modelId) return;
      setCapabilities(latestCaps);
      const block = videoBlockReason({ ...prepared.shot, video_settings: { ...prepared.shot.video_settings, ...savedDraft.video_settings, resolution: (savedDraft.video_settings?.resolution ?? prepared.shot.video_settings.resolution) as ShotRead['video_settings']['resolution'] } }, modelId, latestCaps, false);
      if (block) { setMessage(block); return; }
      const request = shotVideoRequest(prepared.shot, modelId);
      const body = { ...request, source: { ...request.source!, draft_id: savedDraft.id, draft_version: savedDraft.draft_version }, parameters: { ...request.parameters, ...savedDraft.video_settings, duration_ms: savedDraft.video_settings?.duration_ms ?? request.parameters.duration_ms } };
      const key = await requestAttempt(scope, body, attemptStorage());
      if (!alive.current || !prepared.isCurrent()) return;
      const pending: PendingShotGeneration = { format: 1, key, body };
      saveRecovery(attemptScope, pending); setUncertain(pending); submitted = true;
      const task = await generations.generateVideo(body, key);
      clearRecovery(attemptScope);
      if (!alive.current || currentScope.current !== attemptScope) return;
      clearAttempt(scope, attemptStorage());
      setUncertain(null); setShowCurrent(false); setMessage(`视频任务 ${task.generation_id} 已提交。`);
      void history.refresh();
    } catch (cause) {
      const rejected = cause instanceof ApiError && !!cause.status && cause.status < 500 && cause.status !== 408;
      if (submitted && rejected) clearRecovery(attemptScope);
      if (alive.current && currentScope.current === attemptScope) { if (submitted && rejected) setUncertain(null); setMessage(errorMessage(cause)); }
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
        if (!await confirmAction('此视频使用的分镜、提示词、参考图、对白或音色与当前内容不同。已核对视频，仍要采用吗？')) return;
        if (!prepared.isCurrent() || !alive.current) return;
        await mediaLibrary.apply(asset.asset_id, { ...body, acknowledge_stale_source: true });
      }
      if (!alive.current) return;
      prepared.release(); setHistoryOpen(false); setShowCurrent(true); setMessage('视频已采用。'); onChanged(); void history.refresh();
    } catch (cause) { if (alive.current) setMessage(errorMessage(cause)); }
    finally { prepared?.release(); mutation.current = false; if (alive.current) setBusy(false); }
  }

  async function resolveUnknown() {
    if (!uncertain || mutation.current) return;
    mutation.current = true; setBusy(true);
    try {
      const receipt = await generations.lookup('video', uncertain.key);
      if (!alive.current || currentScope.current !== attemptScope) return;
      if (!receipt) { setMessage('暂未查到原视频任务，请稍后再次核对。当前不会重新提交。'); return; }
      clearRecovery(attemptScope); clearAttempt(scope, attemptStorage());
      setUncertain(null); setShowCurrent(false); setMessage(`已找到原视频任务 ${receipt.generation_id}。`);
      void history.refresh();
    } catch (cause) { if (alive.current && currentScope.current === attemptScope) setMessage(`原视频请求核对失败：${errorMessage(cause)}`); }
    finally { mutation.current = false; if (alive.current) setBusy(false); }
  }

  const latestCandidate = showCurrent ? null : history.candidates.find(item => item.media_id !== shot.video?.media_id) ?? null;
  return <section className="shot-video-workspace standard-shot-media">
    <div className="standard-object-preview" aria-label="分镜视频预览">
      {latestCandidate ? <><CandidateVideo asset={latestCandidate} onRefresh={() => void history.refresh()}/>
        <div className="standard-candidate-actions"><span>未采用</span><Button disabled={disabled || busy || latestCandidate.native_quality?.technical_pass === false} onClick={() => void apply(latestCandidate)}>采用此结果</Button><Button onClick={() => setShowCurrent(true)}>查看当前作品</Button></div></>
        : shot.video?.url && !previewFailed ? <video src={shot.video.url} controls playsInline preload="metadata" aria-label="当前采用分镜视频" onError={() => setPreviewFailed(true)}/>
          : <div className="studio-empty"><h3>待制作分镜视频</h3><p>{shot.image ? '填写镜头名称与脚本后生成视频。' : '请先采用一张分镜图。'}</p></div>}
      {shot.video?.is_stale && <Alert type="warning" showIcon message="参考图或创作内容已变化，请核对当前视频。"/>}
      {previewFailed && <Button onClick={onChanged}>刷新视频预览</Button>}
    </div>
    <ShotGenerationSettings open={settingsOpen} active={active} onClose={onCloseSettings}>
      {information}
      <section className="standard-private-references"><div className="standard-reference-heading"><h4>当前采用分镜参考图</h4></div>
        {shot.image?.url ? <PreviewImage src={shot.image.url} alt="视频生成采用的分镜参考图" triggerClassName="standard-video-reference"/> : <p className="episode-help">采用分镜图后可生成视频。</p>}
      </section>
      <GenerationComposer purpose="video" prompt={draft.value.input_text} onPrompt={input_text => draft.edit({ input_text })} maxLength={16000} busy={busy} disabled={disabled || !draft.loaded}
        sendDisabled={disabled || !!reason || !!uncertain || recoveryBlocked} onSend={() => void generate()}
        model={<EpisodeModelSelect kind="video" value={draft.value.model_config_id ?? model} label="视频模型" disabled={disabled || busy || !draft.loaded} onResolvedChange={onResolved}
          onChange={id => { setModelId(undefined); setCapabilities(null); draft.edit({ model_config_id: id || null }); onModelChange(id); }}/>} parameters={<>
          <Select aria-label="视频比例与清晰度" value={`${selectedSettings.aspect ?? episodeAspect}/${selectedSettings.resolution}`} disabled={disabled || busy}
            options={['16:9','9:16','1:1'].flatMap(aspect => ['480p','720p','1080p'].map(resolution => ({ value: `${aspect}/${resolution}`, label: `${aspect} · ${resolution}`, disabled: !!capabilities?.video_input?.resolutions && !capabilities.video_input.resolutions.includes(resolution) })))}
            onChange={next => { const [aspect, resolution] = next.split('/'); draft.edit({ video_settings: { ...selectedSettings, aspect, resolution } }); }}/>
          <InputNumber aria-label="视频时长" suffix="秒" precision={0} step={1} min={durations?.length ? Math.min(...durations) : 1} max={durations?.length ? Math.max(...durations) : 3600}
            value={(selectedSettings.duration_ms ?? shot.duration_ms) / 1000} disabled={disabled || busy}
            onChange={seconds => { if (seconds !== null) draft.edit({ video_settings: { ...selectedSettings, duration_ms: seconds * 1000 } }); }}/>
        </>}/>
      <CreationDraftFeedback draft={draft}/>
      <Button type="text" disabled={busy} onClick={() => setHistoryOpen(true)}>视频生成记录</Button>    {reason && <Alert type="warning" showIcon message={reason} action={<Button size="small" onClick={() => setRevision(value => value + 1)}>重新核对</Button>}/>}
    {uncertain && <Alert type="warning" showIcon message="上次视频请求仍待核对，已保留原请求。" action={<Button loading={busy} onClick={() => void resolveUnknown()}>核对原视频请求</Button>}/>}
    {message && <Alert type="info" showIcon message={message}/>}
    {history.tasks.some(task => task.status === 'queued' || task.status === 'running') && <p role="status" className="episode-help">视频正在生成，离开页面后任务仍会继续。</p>}
    </ShotGenerationSettings>
    {historyOpen && <Dialog title="分镜视频生成记录" className="storyboard-history-dialog" canClose={!busy} onClose={() => setHistoryOpen(false)}>
      <div className="shot-image-history-body">
        <div className="candidate-history-toolbar"><span>播放核对后采用，生成不会自动替换当前视频。</span><Button loading={history.loading} onClick={() => void history.refresh()}>刷新候选</Button></div>
        {message && <Alert type="info" showIcon message={message}/>}{history.error && <Alert type="error" showIcon message={history.error}/>}
        {history.loading && !history.candidates.length ? <Spin/> : !history.candidates.length ? <p className="episode-help">暂无视频候选，生成完成后会显示在这里。</p> :
          <div className="shot-video-candidates">{history.candidates.map(asset => <article key={asset.asset_id}>
            <CandidateVideo asset={asset} onRefresh={() => void history.refresh()}/>{asset.native_quality && <Alert type={asset.native_quality.technical_pass ? 'info' : 'error'} message={asset.native_quality.technical_pass ? '音轨检查通过。采用前请试听核对音色、台词、说话顺序与口型。' : '未检测到有效对白音轨，此候选不能采用。'}/>}
            <div className="candidate-history-toolbar"><span>{asset.name}{asset.duration_ms ? ` · ${asset.duration_ms / 1000} 秒` : ''}</span>
              <Button type="primary" disabled={disabled || busy || asset.native_quality?.technical_pass === false || shot.video?.media_id === asset.media_id} onClick={() => void apply(asset)}>{shot.video?.media_id === asset.media_id ? '当前采用' : '确认采用'}</Button></div>
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
