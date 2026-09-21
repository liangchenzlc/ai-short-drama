import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, InputNumber, Spin } from 'antd';
import { generations } from '../../api/modules/generations';
import { mediaLibrary } from '../../api/modules/media-library';
import { ApiError, errorMessage } from '../../api/http';
import type { GenerationSummary, MediaAsset } from '../../api/types/generations';
import type { ShotRead } from '../../api/modules/storyboard';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { taskLabel } from '../generations/presentation';
import { Dialog } from '../../components/ui/Dialog';
import { shotImageApplyRequest, shotImageRequest } from './workflow-contract';

async function loadAllCandidates(shotId: string, signal: AbortSignal) {
  const items: MediaAsset[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await mediaLibrary.list({ media_type: 'image', source_scene: 'shot_image', source_id: shotId, offset, limit: 100 }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

async function loadAllTasks(shotId: string, signal: AbortSignal) {
  const items: GenerationSummary[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await generations.list({ service_type: 'image', source_scene: 'shot_image', source_id: shotId, offset, limit: 100 }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

export function ShotImageCandidates({
  shot, disabled, modelId, onChanged, flush, getShot, episodeAspect,
}: {
  shot: ShotRead;
  disabled: boolean;
  modelId: string;
  onChanged: () => void;
  flush: () => Promise<boolean>;
  getShot: () => ShotRead | undefined;
  episodeAspect: '16:9' | '9:16';
}) {
  const [items, setItems] = useState<MediaAsset[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [prompt, setPrompt] = useState('');
  const [count, setCount] = useState(1);
  const [preview, setPreview] = useState<MediaAsset | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [candidateRevision, setCandidateRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const completed = useRef(new Set<string>());

  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    loadAllCandidates(shot.id, controller.signal)
      .then((assets) => { if (!controller.signal.aborted) setItems(assets); })
      .catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [shot.id, candidateRevision]);

  useEffect(() => {
    const controller = new AbortController();
    loadAllTasks(shot.id, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setTasks(next);
      const finished = next.filter((task) => ['succeeded', 'failed', 'cancelled'].includes(task.status) && !completed.current.has(task.generation_id));
      if (finished.length) {
        for (const task of finished) completed.current.add(task.generation_id);
        setCandidateRevision((revision) => revision + 1);
      }
    }).catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); });
    return () => controller.abort();
  }, [shot.id, taskRevision]);

  useEffect(() => {
    if (!tasks.some((task) => task.status === 'queued' || task.status === 'running')) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [tasks]);

  async function generate() {
    if (busy || disabled || !shot.script.trim()) return;
    setBusy(true); setMessage('');
    try {
      if (!await flush()) { setMessage('分镜未保存，未发起生成。'); return; }
      const current = getShot();
      if (!current) return;
      const body = {
        ...(modelId ? { config_id: modelId } : {}),
        ...shotImageRequest(current, prompt, [], count, episodeAspect),
      };
      const scope = `shot-image:${shot.id}`;
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      const task = await generations.generateImage(body, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      setMessage(`图片任务 ${task.generation_id} 已提交。`);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function apply(asset: MediaAsset, acknowledge = false, confirmed = false) {
    if (busy || disabled || (!confirmed && !window.confirm('确认采用这张图片到当前分镜？旧图会进入回收记录。'))) return;
    setBusy(true); setMessage('');
    try {
      if (!await flush()) return;
      const current = getShot();
      if (!current) return;
      await mediaLibrary.apply(asset.asset_id, shotImageApplyRequest(current, acknowledge, episodeAspect));
      setMessage('图片已采用。'); onChanged(); setCandidateRevision((revision) => revision + 1);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'stale_generation_source' && !acknowledge
        && window.confirm('图片基于旧创作上下文生成。核对预览后仍要采用到当前分镜吗？')) {
        setBusy(false); return void apply(asset, true, true);
      }
      setMessage(errorMessage(cause));
    } finally { setBusy(false); }
  }

  return <section className="shot-image-candidates">
    <h4>分镜图片</h4>
    {shot.image && <div><strong>当前采用</strong>{shot.image.url && <img src={shot.image.url} alt="当前采用分镜图"/>}{shot.image.is_stale && <Alert type="warning" message="创作内容已变化，请重新核对当前图片。"/>}</div>}
    <div className="shot-image-controls"><label>补充画面要求 <small>选填</small><Input.TextArea value={prompt} disabled={disabled || busy} maxLength={4000} rows={3} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：逆光、雨夜街道，突出人物眼神"/></label><label>生成张数<InputNumber aria-label="生成张数" disabled={disabled || busy} min={1} max={4} value={count} onChange={(value) => setCount(value ?? 1)}/><small>每次 1–4 张</small></label></div>
    <div className="dialog-actions"><Button type="primary" loading={busy} disabled={disabled || !shot.script.trim()} onClick={() => void generate()}>生成图片</Button><Button loading={loading} onClick={() => { setTaskRevision((revision) => revision + 1); setCandidateRevision((revision) => revision + 1); }}>刷新状态与候选</Button></div>
    {message && <Alert type="info" showIcon message={message}/>}
    {tasks.length > 0 && <details className="writing-task-history"><summary>生成记录（{tasks.length}）{tasks.some(task => task.status === 'queued' || task.status === 'running') ? '，正在处理中' : ''}</summary>{tasks.map((task) => <div className="resource-import-row" key={task.generation_id}><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span></div>)}</details>}
    {!shot.script.trim() && <p className="episode-help">先填写本镜脚本，再生成图片。</p>}
    {loading ? <Spin/> : <div className="image-candidate-grid">{items.map((asset) => <article className="image-candidate" key={asset.asset_id}>
      {asset.url ? <button className="asset-library-preview" onClick={() => setPreview(asset)}><img src={asset.url} alt={asset.name}/></button> : <p>预览链接不可用</p>}
      <Button disabled={disabled || busy || shot.image?.media_asset_id === asset.asset_id} onClick={() => void apply(asset)}>{shot.image?.media_asset_id === asset.asset_id ? '当前采用' : '确认采用'}</Button>
    </article>)}</div>}
    {!loading && !items.length && <p className="episode-help">生成的图片会作为候选保留，预览后再确认采用。</p>}
    {preview && <Dialog title="分镜图片预览" className="media-preview-dialog" canClose={!busy} onClose={() => setPreview(null)}><img className="full-image-preview" src={preview.url ?? ''} alt={preview.name}/><div className="dialog-actions"><Button disabled={busy} onClick={() => setPreview(null)}>关闭</Button><Button type="primary" loading={busy} disabled={disabled || busy || shot.image?.media_asset_id === preview.asset_id} onClick={() => void apply(preview)}>{shot.image?.media_asset_id === preview.asset_id ? '当前采用' : '确认采用'}</Button></div></Dialog>}
  </section>;
}
