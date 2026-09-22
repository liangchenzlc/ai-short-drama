import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, InputNumber, Segmented, Select, Spin } from 'antd';
import { ApiError, errorMessage } from '../../../api/http';
import { storyboardApi, type ShotRead, type StoryboardPage } from '../../../api/modules/storyboard';
import { assetLibraries, type LibraryAssetRead } from '../../../api/modules/assets';
import { generations } from '../../../api/modules/generations';
import type { GenerationDetail, GenerationSummary } from '../../../api/types/generations';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import type { WritingSession } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import { moveShot, scriptShotsRequest } from '../../../features/projects/workflow-contract';
import { hasUnsettledStoryboard, mergeStoryboardReload, shouldPollStoryboardTasks } from '../../../features/projects/storyboard-session';
import { attemptStorage, clearAttempt, requestAttempt } from '../../../features/generations/attempt';
import { taskLabel } from '../../../features/generations/presentation';
import { StoryboardResultPreview } from '../../../features/projects/StoryboardResultPreview';
import { ShotImageCandidates } from '../../../features/projects/ShotImageCandidates';

export {
  confirmShotText, confirmStoryboard, reorderStoryboardShots, addStoryboardShot,
  removeStoryboardShot, createStoryboardGrids, bindStoryboardGridCell,
  confirmStoryboardGridCell, adoptGridCellAsFirstFrame,
} from './storyboard-legacy';

type Api = ReturnType<typeof storyboardApi>;

async function loadAllShots(api: Api, signal: AbortSignal, includeArchived: boolean): Promise<StoryboardPage> {
  let offset = 0;
  let first: StoryboardPage | null = null;
  const items: ShotRead[] = [];
  while (first === null || offset < first.total) {
    const page = await api.shots(signal, includeArchived, offset);
    if (!first) first = page;
    if (page.storyboard_version !== first.storyboard_version) {
      if (offset === 0) throw new Error('storyboard changed during read');
      return loadAllShots(api, signal, includeArchived);
    }
    items.push(...page.items);
    offset += page.items.length;
    if (!page.items.length) break;
  }
  return { ...(first as StoryboardPage), items, offset: 0, limit: items.length || 100 };
}

async function loadAllAssets(projectId: string, episodeId: string, signal: AbortSignal) {
  const scope = { kind: 'episode' as const, projectId, episodeId };
  const items: LibraryAssetRead[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await assetLibraries.list(scope, { offset, limit: 100 }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

async function loadAllTasks(projectId: string, episodeId: string, scriptId: string | null, signal?: AbortSignal) {
  const items: GenerationSummary[] = [];
  let offset = 0;
  let total = 1;
  while (offset < total) {
    const page = await generations.list({
      service_type: 'text', project_id: projectId, episode_id: episodeId,
      source_scene: 'script_shots', ...(scriptId ? { source_id: scriptId } : {}),
      offset, limit: 100,
    }, signal);
    items.push(...page.items); total = page.total; offset += page.items.length;
    if (!page.items.length) break;
  }
  return items;
}

export function StoryboardStage({
  value, readOnly, onChange, projectId, episodeId, scriptId, confirmed,
  writingSession, registerBarrier,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string;
  episodeId: string;
  contentVersion: string;
  scriptId: string | null;
  confirmed: boolean;
  writingSession: WritingSession;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
}) {
  const api = storyboardApi(projectId, episodeId);
  const [page, setPageState] = useState<StoryboardPage | null>(null);
  const pageRef = useRef<StoryboardPage | null>(null);
  const [assets, setAssets] = useState<LibraryAssetRead[]>([]);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [candidate, setCandidate] = useState<GenerationDetail | null>(null);
  const [instructions, setInstructions] = useState('');
  const [durationMode, setDurationMode] = useState('3000');
  const [averageShotDurationMs, setAverageShotDurationMs] = useState(3000);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [includeArchived, setIncludeArchived] = useState(false);
  const [storyboardRevision, setStoryboardRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const dirty = useRef(new Set<string>());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const saving = useRef(new Map<string, Promise<boolean>>());

  function setPage(next: StoryboardPage | null) {
    pageRef.current = next;
    setPageState(next);
  }

  function unsettledIds() {
    return new Set([...dirty.current, ...saving.current.keys(), ...timers.current.keys()]);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setMessage('');
    Promise.all([
      loadAllShots(api, controller.signal, includeArchived),
      loadAllAssets(projectId, episodeId, controller.signal),
    ]).then(([remote, library]) => {
      if (controller.signal.aborted) return;
      setPage(mergeStoryboardReload(remote, pageRef.current, unsettledIds()));
      setAssets(library);
    }).catch((cause) => {
      if (!controller.signal.aborted) setMessage(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [projectId, episodeId, includeArchived, storyboardRevision]);

  useEffect(() => {
    const controller = new AbortController();
    loadAllTasks(projectId, episodeId, scriptId, controller.signal)
      .then((items) => { if (!controller.signal.aborted) setTasks(items); })
      .catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); });
    return () => controller.abort();
  }, [projectId, episodeId, scriptId, taskRevision]);

  useEffect(() => {
    if (!shouldPollStoryboardTasks(tasks)) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [tasks]);

  useEffect(() => () => {
    for (const timer of timers.current.values()) clearTimeout(timer);
    timers.current.clear();
  }, []);

  function updateLocal(id: string, patch: Partial<Pick<ShotRead, 'script' | 'duration_ms' | 'asset_ids' | 'image_settings'>>) {
    const current = pageRef.current;
    if (!current || readOnly) return;
    setPage({ ...current, items: current.items.map((shot) => shot.id === id ? { ...shot, ...patch } : shot) });
    dirty.current.add(id);
    clearTimeout(timers.current.get(id));
    timers.current.set(id, setTimeout(() => void saveShot(id), 1000));
  }

  function saveShot(id: string): Promise<boolean> {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    const active = saving.current.get(id);
    if (active) return active.then((ok) => ok && dirty.current.has(id) ? saveShot(id) : ok);
    const shot = pageRef.current?.items.find((item) => item.id === id);
    if (!shot || !dirty.current.has(id)) return Promise.resolve(true);
    dirty.current.delete(id);
    const operation = api.update(id, {
      row_version: shot.row_version,
      script: shot.script,
      duration_ms: shot.duration_ms,
      asset_ids: shot.asset_ids,
      image_settings: shot.image_settings,
    }).then((result) => {
      const current = pageRef.current;
      if (!current) return false;
      const newer = dirty.current.has(id) || timers.current.has(id);
      setPage({
        ...current,
        storyboard_version: result.storyboard_version,
        items: current.items.map((item) => item.id !== id ? item : newer
          ? { ...item, row_version: result.shot.row_version, context_hash: result.shot.context_hash, image: result.shot.image }
          : result.shot),
      });
      return true;
    }).catch((cause) => {
      dirty.current.add(id);
      setMessage(cause instanceof ApiError && cause.status === 409
        ? '分镜已被其他窗口修改。你的输入仍保留，请下载草稿或刷新后手动合并。'
        : errorMessage(cause));
      return false;
    }).finally(() => saving.current.delete(id));
    saving.current.set(id, operation);
    return operation;
  }

  async function flushAll() {
    for (const timer of timers.current.values()) clearTimeout(timer);
    timers.current.clear();
    while (dirty.current.size || saving.current.size) {
      const ids = [...new Set([...dirty.current, ...saving.current.keys()])];
      const results = await Promise.all(ids.map(saveShot));
      if (results.some((ok) => !ok)) return false;
    }
    return true;
  }

  useEffect(() => {
    const barrier: NavigationBarrier = {
      hasUnsettled: () => hasUnsettledStoryboard(dirty.current, saving.current, timers.current),
      flush: flushAll,
    };
    registerBarrier(barrier);
    return () => registerBarrier(null);
  });

  async function add() {
    const current = pageRef.current;
    if (!current || busy || !await flushAll()) return;
    setBusy(true); setMessage('');
    const scope = `new-shot:${projectId}:${episodeId}`;
    try {
      const body = { storyboard_version: pageRef.current!.storyboard_version };
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      await api.create(body.storyboard_version, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function reorder(id: string, direction: -1 | 1) {
    if (busy || !await flushAll()) return;
    const current = pageRef.current;
    if (!current) return;
    const active = current.items.filter((shot) => !shot.deleted_at);
    const next = moveShot(active, id, direction);
    if (next === active) return;
    setBusy(true);
    try {
      await api.order(current.storyboard_version, next.map((shot) => shot.id));
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function archive(id: string) {
    if (busy || !window.confirm('归档此分镜？已采用媒体和历史生成记录会保留。') || !await flushAll()) return;
    const latest = pageRef.current?.items.find((shot) => shot.id === id);
    if (!latest) return;
    setBusy(true);
    try {
      await api.remove(latest.id, latest.row_version);
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function toggleArchived(checked: boolean) {
    if (!await flushAll()) {
      setMessage('尚有未保存分镜，无法切换历史列表。');
      return;
    }
    setIncludeArchived(checked);
  }

  async function generateStoryboard() {
    if (busy || !confirmed || !scriptId) return;
    setBusy(true); setMessage('');
    try {
      if (!await writingSession.flush()) { setMessage('剧本未保存，未发起生成。'); return; }
      const latest = writingSession.getSnapshot();
      if (!latest.confirmed || !latest.scriptId) { setMessage('请先确认当前编辑剧本。'); return; }
      const body = {
        ...(value.models.storyboardText ? { config_id: value.models.storyboardText } : {}),
        ...scriptShotsRequest(projectId, episodeId, latest.scriptId, latest.contentVersion, instructions, averageShotDurationMs),
      };
      const scope = `script-shots:${projectId}:${episodeId}`;
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      const task = await generations.generateText(body, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      setMessage(`分镜生成任务 ${task.generation_id} 已提交。`);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function previewTask(task: GenerationSummary) {
    setBusy(true); setMessage('');
    try { setCandidate(await generations.detail(task.generation_id)); }
    catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function applyResult(mode: 'append' | 'replace') {
    if (!candidate || busy || !await flushAll()) return;
    const current = pageRef.current;
    if (!current) return;
    if (mode === 'replace' && !window.confirm(`替换会归档当前 ${current.items.filter((shot) => !shot.deleted_at).length} 个活动分镜，旧媒体与历史会保留。确定继续？`)) return;
    setBusy(true); setMessage('');
    try {
      await api.apply(candidate.generation_id, {
        mode,
        content_version: writingSession.getSnapshot().contentVersion,
        storyboard_version: current.storyboard_version,
        confirm_replace: mode === 'replace',
      });
      setCandidate(null);
      setStoryboardRevision((revision) => revision + 1);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(pageRef.current, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = `episode-${episodeId}-storyboard-draft.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const active = page?.items.filter((shot) => !shot.deleted_at) ?? [];
  return <div className="storyboard-workspace">
    <div className="episode-stage-heading storyboard-heading">
      <div><h2>分镜制作</h2><p>把剧本安排成镜头。修改自动保存，生成结果预览后再采用。</p></div>
      <div><Button onClick={download}>下载草稿</Button><Button disabled={readOnly || busy} onClick={() => void add()}>新增分镜</Button></div>
    </div>
    {message && <Alert type={message.includes('其他窗口') ? 'warning' : 'info'} showIcon message={message}/>}
    <Checkbox checked={includeArchived} onChange={(event) => void toggleArchived(event.target.checked)}>显示归档历史</Checkbox>
    <section className="generation-section storyboard-generation">
      <h3>剧本生成分镜</h3>
      <div className="storyboard-models"><label><span>分镜文字模型</span><EpisodeModelSelect kind="text" label="分镜模型" value={value.models.storyboardText} disabled={readOnly || busy} onChange={(id) => onChange({ ...value, models: { ...value.models, storyboardText: id } })}/></label>
      <label><span>分镜生图模型</span><EpisodeModelSelect kind="image" label="分镜生图模型" value={value.models.storyboardImage} disabled={readOnly || busy} onChange={(id) => onChange({ ...value, models: { ...value.models, storyboardImage: id } })}/></label></div>
      <div className="storyboard-duration-control">
        <div><strong>平均镜头时长</strong><small>系统会按动作、对白和情绪节拍分配实际时长，不会把每个镜头强制设成一样长。</small></div>
        <Segmented aria-label="平均镜头时长预设" value={durationMode} disabled={readOnly || busy} options={[{ label: '2 秒', value: '2000' }, { label: '3 秒', value: '3000' }, { label: '5 秒', value: '5000' }, { label: '自定义', value: 'custom' }]} onChange={(next) => { const mode = String(next); setDurationMode(mode); if (mode !== 'custom') setAverageShotDurationMs(Number(mode)); }}/>
        {durationMode === 'custom' && <label>自定义时长<InputNumber aria-label="自定义平均镜头时长" min={1} max={10} step={0.5} precision={1} value={averageShotDurationMs / 1000} addonAfter="秒" disabled={readOnly || busy} onChange={(seconds) => { if (seconds !== null) setAverageShotDurationMs(Math.round(seconds * 1000)); }}/></label>}
      </div>
      <Input.TextArea rows={2} maxLength={4000} value={instructions} onChange={(event) => setInstructions(event.target.value)} aria-label="补充分镜要求" disabled={readOnly || busy} placeholder="补充分镜要求（选填），例如：更多近景，突出人物情绪"/>
      <div className="dialog-actions"><Button type="primary" loading={busy} disabled={readOnly || !confirmed || !scriptId} onClick={() => void generateStoryboard()}>生成分镜脚本</Button><Button onClick={() => setTaskRevision((revision) => revision + 1)}>刷新任务</Button></div>
      {!confirmed && <p className="episode-help">请先确认当前剧本。</p>}
      <div className="storyboard-task-list">{tasks.map((task) => <div key={task.generation_id} className="resource-import-row"><span>{taskLabel(task)} · {task.generation_id}{task.error ? ` · ${task.error.message}` : ''}</span><Button disabled={task.status !== 'succeeded'} onClick={() => void previewTask(task)}>预览结果</Button></div>)}</div>
      {candidate && <StoryboardResultPreview task={candidate} busy={busy} error={message} assetNames={Object.fromEntries(assets.map((asset) => [asset.id, asset.name]))} onApply={(mode) => void applyResult(mode)}/>}
    </section>
    {loading && !page ? <Spin/> : <div className="storyboard-list">
      {!page?.items.length && <div className="studio-empty"><h3>还没有镜头</h3><p>确认剧本后可让 AI 拆分镜头，也可以手动新增分镜。</p><Button disabled={readOnly || busy} onClick={() => void add()}>新增第一个分镜</Button></div>}
      {page?.items.map((shot) => <details className={`storyboard-item ${shot.deleted_at ? 'is-archived' : ''}`} key={shot.id} open={!shot.deleted_at}>
        <summary className="storyboard-summary"><strong>{shot.deleted_at ? '已归档' : `分镜 ${shot.position}`}</strong><span className="storyboard-summary-duration">{Number((shot.duration_ms / 1000).toFixed(1))} 秒</span><span className="storyboard-summary-script">{shot.script || '空分镜'}</span></summary>
        <div className="storyboard-expanded">
          <label>分镜脚本<Input.TextArea rows={4} value={shot.script} disabled={readOnly || !!shot.deleted_at} onChange={(event) => updateLocal(shot.id, { script: event.target.value })}/></label>
          {shot.source_excerpt && <details className="storyboard-source-excerpt"><summary>查看原文依据</summary><blockquote>{shot.source_excerpt}</blockquote></details>}
          <label>关联素材<Select mode="multiple" style={{ width: '100%' }} value={shot.asset_ids} disabled={readOnly || !!shot.deleted_at} options={assets.map((asset) => ({ value: asset.id, label: `${asset.name}（${({ character: '角色', scene: '场景', prop: '道具' })[asset.kind]}）` }))} onChange={(asset_ids) => updateLocal(shot.id, { asset_ids })}/></label>
          <div className="generation-form-grid">
            <label>镜头时长<InputNumber aria-label={`分镜 ${shot.position} 时长`} min={1} max={10} step={0.5} precision={1} value={shot.duration_ms / 1000} addonAfter="秒" disabled={readOnly || !!shot.deleted_at} onChange={(seconds) => { if (seconds !== null) updateLocal(shot.id, { duration_ms: Math.round(seconds * 1000) }); }}/></label>
            <label>画面布局<Select aria-label="画面布局" value={shot.image_settings.layout} disabled={readOnly || !!shot.deleted_at} options={Object.entries({ single: '单图', four: '四宫格', five: '五宫格', nine: '九宫格' }).map(([value, label]) => ({ value, label }))} onChange={(layout) => updateLocal(shot.id, { image_settings: { ...shot.image_settings, layout } })}/></label>
            <label>画幅比例<Select aria-label="画幅比例" value={shot.image_settings.aspect} disabled={readOnly || !!shot.deleted_at} options={['inherit', '16:9', '9:16', '1:1', '4:3', '3:4'].map((option) => ({ value: option, label: option === 'inherit' ? '跟随本集画幅' : option }))} onChange={(aspect) => updateLocal(shot.id, { image_settings: { ...shot.image_settings, aspect } })}/></label>
            <label>图片清晰度<Select aria-label="图片清晰度" value={shot.image_settings.resolution} disabled={readOnly || !!shot.deleted_at} options={['1K', '2K', '4K'].map((option) => ({ value: option, label: option }))} onChange={(resolution) => updateLocal(shot.id, { image_settings: { ...shot.image_settings, resolution } })}/></label>
          </div>
          {!shot.deleted_at && <div className="dialog-actions">
            <Button disabled={readOnly || busy || shot.position === 1} onClick={() => void reorder(shot.id, -1)}>上移</Button>
            <Button disabled={readOnly || busy || shot.position === active.length} onClick={() => void reorder(shot.id, 1)}>下移</Button>
            <Button danger disabled={readOnly || busy} onClick={() => void archive(shot.id)}>归档</Button>
            <span>{dirty.current.has(shot.id) ? '等待保存' : saving.current.has(shot.id) ? '保存中' : `已保存 v${shot.row_version}`}</span>
          </div>}
          {!shot.deleted_at && <ShotImageCandidates
            shot={shot}
            disabled={readOnly || busy}
            modelId={value.models.storyboardImage}
            episodeAspect={value.aspect}
            flush={() => saveShot(shot.id)}
            getShot={() => pageRef.current?.items.find((item) => item.id === shot.id)}
            onChanged={() => setStoryboardRevision((revision) => revision + 1)}
          />}
        </div>
      </details>)}
    </div>}
  </div>;
}
