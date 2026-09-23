import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Input, InputNumber, Segmented, Select, Spin } from 'antd';
import { ApiError, errorMessage } from '../../../api/http';
import { storyboardApi, type ShotRead, type StoryboardPage } from '../../../api/modules/storyboard';
import { assetLibraries, type LibraryAssetRead } from '../../../api/modules/assets';
import { generations } from '../../../api/modules/generations';
import { aiModelConfigs } from '../../../api/modules/ai-model-configs';
import { AI_CONFIGS_CHANGED } from '../../../features/ai-config/config-events';
import type { GenerationSummary } from '../../../api/types/generations';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import type { WritingSession } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import { scriptShotsRequest } from '../../../features/projects/workflow-contract';
import { hasUnsettledStoryboard, mergeStoryboardReload, shouldPollStoryboardTasks } from '../../../features/projects/storyboard-session';
import { attemptStorage, clearAttempt, requestAttempt } from '../../../features/generations/attempt';
import { taskLabel } from '../../../features/generations/presentation';
import { StoryboardResultPreview } from '../../../features/projects/StoryboardResultPreview';
import { Dialog } from '../../../components/ui/Dialog';
import { LazyLoadMore } from '../../../components/ui/LazyLoadMore';
import { ShotImageCandidates } from '../../../features/projects/ShotImageCandidates';
import { prepareShotOperation, type ImageCapabilities, type PreparedShot } from '../../../features/projects/shot-image-workflow';

type Api = ReturnType<typeof storyboardApi>;

async function loadLoadedShots(api: Api, signal: AbortSignal, count: number): Promise<StoryboardPage> {
  const first = await api.shots(signal, false, 0);
  const items = [...first.items];
  while (items.length < Math.min(count, first.total)) {
    const next = await api.shots(signal, false, items.length);
    if (next.storyboard_version !== first.storyboard_version) throw new Error('分镜列表已变化，请重新加载。');
    if (!next.items.length) break;
    items.push(...next.items);
  }
  return { ...first, items };
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
  const [candidate, setCandidate] = useState<GenerationSummary | null>(null);
  const [instructions, setInstructions] = useState('');
  const [durationMode, setDurationMode] = useState('3000');
  const [averageShotDurationMs, setAverageShotDurationMs] = useState(3000);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState('');
  const moreLock = useRef(false);
  const [tasksTotal, setTasksTotal] = useState(0);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksError, setTasksError] = useState('');
  const taskMoreLock = useRef(false);
  const [storyboardRevision, setStoryboardRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const dirty = useRef(new Set<string>());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const saving = useRef(new Map<string, Promise<boolean>>());
  const shotLocks = useRef(new Map<string, symbol>());
  const [lockedShots, setLockedShots] = useState<ReadonlySet<string>>(new Set());
  const mounted = useRef(false);
  const contextEpoch = useRef(0);
  const preparationEpoch = useRef(0);
  const contextKey = `${projectId}:${episodeId}`;
  const currentContext = useRef(contextKey);
  const currentAccess = useRef({ readOnly, busy, loading, loadError });
  const [resolvedImageModel, setResolvedImageModel] = useState<{ selection: string; id: string | undefined }>({ selection: value.models.storyboardImage, id: undefined });
  const [resolvingImageModel, setResolvingImageModel] = useState(true);
  const [capabilityRevision, setCapabilityRevision] = useState(0);
  const capabilityRequest = useRef<AbortController | null>(null);
  const [capabilityResult, setCapabilityResult] = useState<{ modelId: string; revision: number; capabilities: ImageCapabilities | null } | null>(null);
  const imageModelId = resolvedImageModel.selection === value.models.storyboardImage ? resolvedImageModel.id : undefined;
  const capabilitiesReady = !resolvingImageModel && capabilityResult?.modelId === imageModelId && capabilityResult?.revision === capabilityRevision;
  const imageCapabilities = capabilitiesReady ? capabilityResult.capabilities : null;
  const capabilitiesLoading = resolvingImageModel || (!!imageModelId && !capabilitiesReady);

  const onResolvedImageModel = useCallback((id: string | undefined) => {
    capabilityRequest.current?.abort();
    setCapabilityResult(null);
    setResolvedImageModel({ selection: value.models.storyboardImage, id });
    setResolvingImageModel(false);
    setCapabilityRevision((revision) => revision + 1);
  }, [value.models.storyboardImage]);

  const refreshCapabilities = useCallback(() => {
    preparationEpoch.current++;
    capabilityRequest.current?.abort();
    setCapabilityResult(null);
    setCapabilityRevision((revision) => revision + 1);
  }, []);

  useEffect(() => {
    const refresh = () => {
      refreshCapabilities();
      setResolvingImageModel(true);
    };
    window.addEventListener(AI_CONFIGS_CHANGED, refresh);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener(AI_CONFIGS_CHANGED, refresh);
      window.removeEventListener('focus', refresh);
    };
  }, [refreshCapabilities]);

  useEffect(() => {
    if (!imageModelId || resolvingImageModel) return;
    const controller = new AbortController();
    capabilityRequest.current = controller;
    aiModelConfigs.capabilities(imageModelId, controller.signal)
      .then((capabilities) => {
        if (!controller.signal.aborted) setCapabilityResult({ modelId: imageModelId, revision: capabilityRevision, capabilities });
      }).catch(() => {
        if (!controller.signal.aborted) setCapabilityResult({ modelId: imageModelId, revision: capabilityRevision, capabilities: null });
      });
    return () => controller.abort();
  }, [imageModelId, resolvingImageModel, capabilityRevision]);

  useLayoutEffect(() => {
    mounted.current = true;
    currentContext.current = contextKey;
    setPage(null);
    setAssets([]); setTasks([]); setTasksTotal(0); setSelectedShotId(null); setCandidate(null); setMessage(''); setBusy(false);
    setLockedShots(new Set());
    return () => {
      mounted.current = false;
      contextEpoch.current++;
      preparationEpoch.current++;
      for (const timer of timers.current.values()) clearTimeout(timer);
      timers.current.clear(); dirty.current.clear(); saving.current.clear(); shotLocks.current.clear();
    };
  }, [contextKey]);

  useLayoutEffect(() => {
    currentAccess.current = { readOnly, busy, loading, loadError };
  });

  useLayoutEffect(() => {
    preparationEpoch.current++;
  }, [readOnly, value.aspect, value.models.storyboardImage, imageModelId]);

  function isCurrentContext(epoch: number) {
    return mounted.current && currentContext.current === contextKey && contextEpoch.current === epoch;
  }

  function setPage(next: StoryboardPage | null) {
    pageRef.current = next;
    setPageState(next);
  }

  function unsettledIds() {
    return new Set([...dirty.current, ...saving.current.keys(), ...timers.current.keys(), ...shotLocks.current.keys()]);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError(''); setMoreError('');
    Promise.all([
      loadLoadedShots(api, controller.signal, pageRef.current?.items.length ?? 20),
      loadAllAssets(projectId, episodeId, controller.signal),
    ]).then(([remote, library]) => {
      if (controller.signal.aborted) return;
      setPage(mergeStoryboardReload(remote, pageRef.current, unsettledIds()));
      setAssets(library);
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [projectId, episodeId, storyboardRevision]);

  useEffect(() => {
    const controller = new AbortController();
    generations.list({ service_type: 'text', project_id: projectId, episode_id: episodeId, source_scene: 'script_shots', offset: 0, limit: 20 }, controller.signal)
      .then((page) => { if (!controller.signal.aborted) { setTasks(current => [...page.items, ...current.filter(task => !page.items.some(next => next.generation_id === task.generation_id))]); setTasksTotal(page.total); } })
      .catch((cause) => { if (!controller.signal.aborted) setMessage(errorMessage(cause)); });
    return () => controller.abort();
  }, [projectId, episodeId, scriptId, taskRevision]);

  useEffect(() => {
    if (!shouldPollStoryboardTasks(tasks)) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [tasks]);

  function updateLocal(id: string, patch: Partial<Pick<ShotRead, 'script' | 'duration_ms' | 'asset_ids' | 'image_settings'>>) {
    const current = pageRef.current;
    if (!current || !isCurrentContext(contextEpoch.current) || currentAccess.current.readOnly || shotLocks.current.has(id) || current.items.find((shot) => shot.id === id)?.deleted_at) return;
    setPage({ ...current, items: current.items.map((shot) => shot.id === id ? { ...shot, ...patch } : shot) });
    dirty.current.add(id);
    clearTimeout(timers.current.get(id));
    timers.current.set(id, setTimeout(() => void saveShot(id), 1000));
  }

  function saveShot(id: string): Promise<boolean> {
    const epoch = contextEpoch.current;
    if (!isCurrentContext(epoch) || currentAccess.current.readOnly) return Promise.resolve(false);
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    const active = saving.current.get(id);
    if (active) return active.then((ok) => isCurrentContext(epoch) && ok ? (dirty.current.has(id) ? saveShot(id) : true) : false);
    const shot = pageRef.current?.items.find((item) => item.id === id);
    if (!shot || shot.deleted_at) return Promise.resolve(false);
    if (!dirty.current.has(id)) return Promise.resolve(true);
    dirty.current.delete(id);
    const operation = api.update(id, {
      row_version: shot.row_version,
      script: shot.script,
      duration_ms: shot.duration_ms,
      asset_ids: shot.asset_ids,
      image_settings: shot.image_settings,
    }).then((result) => {
      if (!isCurrentContext(epoch)) return false;
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
      if (!isCurrentContext(epoch)) return false;
      dirty.current.add(id);
      setMessage(cause instanceof ApiError && cause.status === 409
        ? '分镜已被其他窗口修改。你的输入仍保留，请保留当前页面，复制未保存正文后再刷新核对。'
        : errorMessage(cause));
      return false;
    }).finally(() => { if (saving.current.get(id) === operation) saving.current.delete(id); });
    saving.current.set(id, operation);
    return operation;
  }

  async function prepareShot(id: string): Promise<PreparedShot | null> {
    const epoch = contextEpoch.current;
    const preparation = preparationEpoch.current;
    const access = currentAccess.current;
    const shot = pageRef.current?.items.find((item) => item.id === id);
    if (!isCurrentContext(epoch) || access.readOnly || access.busy || access.loading || access.loadError || !shot || shot.deleted_at || shotLocks.current.has(id)) return null;
    const token = Symbol(id);
    shotLocks.current.set(id, token);
    setLockedShots(new Set(shotLocks.current.keys()));
    const release = () => {
      if (shotLocks.current.get(id) !== token) return;
      shotLocks.current.delete(id);
      if (isCurrentContext(epoch)) setLockedShots(new Set(shotLocks.current.keys()));
    };
    let storyboardVersion: string | undefined;
    try {
      return await prepareShotOperation({
        save: () => saveShot(id),
        local: () => pageRef.current?.items.find((item) => item.id === id),
        read: async () => {
          const result = await api.shot(id);
          storyboardVersion = result.storyboard_version;
          return result.shot;
        },
        valid: () => {
          if (!isCurrentContext(epoch)) return false;
          const current = pageRef.current?.items.find((item) => item.id === id);
          const valid = preparationEpoch.current === preparation && !currentAccess.current.readOnly && !!current && !current.deleted_at
            && !dirty.current.has(id) && !saving.current.has(id) && !timers.current.has(id);
          if (!valid) setMessage('分镜或本集设置已变化，请核对后重新操作。未保存的输入仍保留。');
          return valid;
        },
        accept: (remote) => {
          const current = pageRef.current;
          if (current) setPage({ ...current, storyboard_version: storyboardVersion ?? current.storyboard_version, items: current.items.map((item) => item.id === id ? remote : item) });
        },
        changed: () => setMessage('分镜上下文、图片或设置已变化，已刷新当前分镜，请核对后重新操作。'),
        release,
      });
    } catch (cause) {
      if (isCurrentContext(epoch)) setMessage(errorMessage(cause));
      return null;
    }
  }

  async function flushAll() {
    const epoch = contextEpoch.current;
    for (const timer of timers.current.values()) clearTimeout(timer);
    timers.current.clear();
    while (dirty.current.size || saving.current.size) {
      const ids = [...new Set([...dirty.current, ...saving.current.keys()])];
      const results = await Promise.all(ids.map(saveShot));
      if (!isCurrentContext(epoch) || results.some((ok) => !ok)) return false;
    }
    return isCurrentContext(epoch);
  }

  useEffect(() => {
    const barrier: NavigationBarrier = {
      hasUnsettled: () => shotLocks.current.size > 0 || hasUnsettledStoryboard(dirty.current, saving.current, timers.current),
      flush: async () => shotLocks.current.size === 0 && await flushAll() && shotLocks.current.size === 0,
    };
    registerBarrier(barrier);
    return () => registerBarrier(null);
  });

  async function add() {
    const current = pageRef.current;
    if (!current || readOnly || busy || shotLocks.current.size || !await flushAll() || shotLocks.current.size) return;
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
    if (readOnly || busy || shotLocks.current.size || !await flushAll() || shotLocks.current.size) return;
    const current = pageRef.current;
    if (!current) return;
    setBusy(true);
    try {
      await api.move(id, current.storyboard_version, direction);
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  async function loadMoreShots() {
    const current = pageRef.current;
    if (!current || loading || moreLock.current || current.items.length >= current.total) return;
    const epoch = contextEpoch.current;
    moreLock.current = true; setMoreLoading(true); setMoreError('');
    try {
      const next = await api.shots(undefined, false, current.items.length);
      if (!isCurrentContext(epoch)) return;
      const latest = pageRef.current!;
      if (next.storyboard_version !== latest.storyboard_version) throw new Error('分镜列表已变化，请重新加载分镜列表后继续。');
      setPage({ ...latest, total: next.total, items: [...latest.items, ...next.items.filter(item => !latest.items.some(own => own.id === item.id))] });
    } catch (cause) { if (isCurrentContext(epoch)) setMoreError(errorMessage(cause)); }
    finally { moreLock.current = false; if (isCurrentContext(epoch)) setMoreLoading(false); }
  }

  async function loadMoreTasks() {
    if (taskMoreLock.current || tasks.length >= tasksTotal) return;
    const epoch = contextEpoch.current;
    taskMoreLock.current = true; setTasksLoading(true); setTasksError('');
    try {
      const page = await generations.list({ service_type: 'text', project_id: projectId, episode_id: episodeId, source_scene: 'script_shots', offset: tasks.length, limit: 20 });
      if (!isCurrentContext(epoch)) return;
      setTasks(current => [...current, ...page.items.filter(item => !current.some(own => own.generation_id === item.generation_id))]); setTasksTotal(page.total);
    } catch (cause) { if (isCurrentContext(epoch)) setTasksError(errorMessage(cause)); }
    finally { taskMoreLock.current = false; if (isCurrentContext(epoch)) setTasksLoading(false); }
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

  async function applyResult(mode: 'append' | 'replace') {
    if (readOnly || loading || loadError || !pageRef.current) {
      setMessage('分镜列表尚未就绪，请先重新加载分镜列表后再采用。');
      return;
    }
    if (!candidate || busy || shotLocks.current.size || !await flushAll() || shotLocks.current.size) return;
    const current = pageRef.current;
    if (!current) return;
    if (mode === 'replace' && !window.confirm(`替换会移出当前 ${current.total} 个分镜，旧媒体与历史会保留。确定继续？`)) return;
    setBusy(true); setMessage('');
    try {
      await api.apply(candidate.generation_id, {
        mode,
        content_version: writingSession.getSnapshot().contentVersion,
        storyboard_version: current.storyboard_version,
        confirm_replace: mode === 'replace',
      });
      setCandidate(null); setHistoryOpen(false); setSelectedShotId(null);
      setStoryboardRevision((revision) => revision + 1);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { setBusy(false); }
  }

  return <div className="storyboard-workspace">
    <div className="episode-stage-heading storyboard-heading"><div><h2>分镜制作</h2><p>先编排镜头，再逐镜生成画面。修改自动保存。</p></div><Button disabled={readOnly || busy || loading || !!loadError || !page} onClick={() => void add()}>新增分镜</Button></div>
    {message && <Alert type={message.includes('其他窗口') ? 'warning' : 'info'} showIcon message={message}/>}
    {loadError && <Alert type="error" showIcon message={`分镜列表加载失败：${loadError}`} action={<Button loading={loading} onClick={() => setStoryboardRevision(revision => revision + 1)}>重新加载分镜列表</Button>}/>}
    <div className="creation-workspace storyboard-columns">
      <aside className="writing-assistant" aria-label="分镜生成设置">
        <h3>AI 分镜制作</h3>
        <label className="writing-control"><span>文本模型</span><EpisodeModelSelect kind="text" label="分镜模型" value={value.models.storyboardText} disabled={readOnly || busy} onChange={id => onChange({ ...value, models: { ...value.models, storyboardText: id } })}/></label>
        <label className="writing-control"><span>分镜生图模型</span><EpisodeModelSelect kind="image" label="分镜生图模型" value={value.models.storyboardImage} disabled={readOnly || busy || lockedShots.size > 0} onResolvedChange={onResolvedImageModel} onChange={id => onChange({ ...value, models: { ...value.models, storyboardImage: id } })}/></label>
        <div className="writing-control"><span>平均镜头时长</span><Segmented block aria-label="平均镜头时长预设" value={durationMode} disabled={readOnly || busy} options={[{ label: '2 秒', value: '2000' }, { label: '3 秒', value: '3000' }, { label: '5 秒', value: '5000' }, { label: '自定', value: 'custom' }]} onChange={next => { const mode = String(next); setDurationMode(mode); if (mode !== 'custom') setAverageShotDurationMs(Number(mode)); }}/>
        {durationMode === 'custom' && <InputNumber aria-label="自定义平均镜头时长" min={1} max={10} step={0.5} precision={1} value={averageShotDurationMs / 1000} addonAfter="秒" disabled={readOnly || busy} onChange={seconds => { if (seconds !== null) setAverageShotDurationMs(Math.round(seconds * 1000)); }}/>}</div>
        <label className="writing-control"><span>分镜要求描述</span><Input.TextArea rows={4} maxLength={4000} value={instructions} onChange={event => setInstructions(event.target.value)} aria-label="分镜要求描述" disabled={readOnly || busy} placeholder="例如：更多近景，突出人物情绪"/></label>
        <Button block type="primary" loading={busy} disabled={readOnly || !confirmed || !scriptId} onClick={() => void generateStoryboard()}>生成分镜脚本</Button>
        <Button block onClick={() => { setHistoryOpen(true); setCandidate(null); setTaskRevision(revision => revision + 1); }}>生成记录</Button>
        {!confirmed && <p className="episode-help">请先在小说改编中确认当前剧本。</p>}
        {shouldPollStoryboardTasks(tasks) && <p role="status" className="episode-help">分镜正在生成，完成后从生成记录中预览采用。</p>}
      </aside>
      <section className="creation-editor storyboard-editor" aria-label="分镜创作区域">
        <div className="storyboard-editor-heading"><h3>本集分镜</h3><span>{page ? `共 ${page.total} 镜` : '正在载入'}</span></div>
        {moreError && <Button onClick={() => setStoryboardRevision(revision => revision + 1)}>重新加载分镜列表</Button>}
        {loading && !page ? <Spin/> : <div className="storyboard-list lazy-scroll">
          {page && !page.items.length && !loadError && <div className="studio-empty"><h3>还没有镜头</h3><p>确认剧本后生成分镜，或手动新增第一个镜头。</p><Button disabled={readOnly || busy || loading} onClick={() => void add()}>新增第一个分镜</Button></div>}
          {page?.items.map(shot => <article className={`storyboard-item${selectedShotId === shot.id ? ' is-selected' : ''}`} key={shot.id}>
            <button type="button" className="storyboard-summary" aria-expanded={selectedShotId === shot.id} disabled={lockedShots.size > 0} onClick={() => setSelectedShotId(selectedShotId === shot.id ? null : shot.id)}><strong>分镜 {String(shot.position).padStart(2, '0')}</strong><span className="storyboard-summary-script">{shot.script || '空分镜，点击编写'}</span></button>
            {selectedShotId === shot.id && <div className="storyboard-expanded">
              <label>分镜脚本<Input.TextArea rows={5} value={shot.script} disabled={readOnly || lockedShots.has(shot.id)} onChange={event => updateLocal(shot.id, { script: event.target.value })}/></label>
              <div className="shot-asset-fields">{(['character', 'scene', 'prop'] as const).map(kind => <label key={kind}>{({ character: '角色', scene: '场景', prop: '道具' })[kind]}<Select mode="multiple" aria-label={`关联${({ character: '角色', scene: '场景', prop: '道具' })[kind]}`} value={shot.asset_ids.filter(id => assets.find(asset => asset.id === id)?.kind === kind)} disabled={readOnly || lockedShots.has(shot.id)} options={assets.filter(asset => asset.kind === kind).map(asset => ({ value: asset.id, label: asset.name }))} onChange={ids => updateLocal(shot.id, { asset_ids: [...shot.asset_ids.filter(id => assets.find(asset => asset.id === id)?.kind !== kind), ...ids] })}/></label>)}</div>
              <div className="shot-edit-actions"><label>镜头时长<InputNumber aria-label={`分镜 ${shot.position} 时长`} min={1} max={10} step={0.5} precision={1} value={shot.duration_ms / 1000} addonAfter="秒" disabled={readOnly || lockedShots.has(shot.id)} onChange={seconds => { if (seconds !== null) updateLocal(shot.id, { duration_ms: Math.round(seconds * 1000) }); }}/></label><Button disabled={readOnly || busy || lockedShots.size > 0 || shot.position === 1} onClick={() => void reorder(shot.id, -1)}>上移</Button><Button disabled={readOnly || busy || lockedShots.size > 0 || shot.position === page.total} onClick={() => void reorder(shot.id, 1)}>下移</Button><span role="status">{dirty.current.has(shot.id) ? '等待保存' : saving.current.has(shot.id) ? '保存中' : '已保存'}</span></div>
              <ShotImageCandidates shot={shot} disabled={readOnly || busy || lockedShots.has(shot.id)} modelId={imageModelId} capabilities={imageCapabilities} capabilitiesLoading={capabilitiesLoading} onRefreshCapabilities={refreshCapabilities} episodeAspect={value.aspect} prepareShot={() => prepareShot(shot.id)} onChanged={() => setStoryboardRevision(revision => revision + 1)} settings={<div className="generation-form-grid">
                <label>图片清晰度<Select aria-label="图片清晰度" value={shot.image_settings.resolution} disabled={readOnly || lockedShots.has(shot.id)} options={['1K', '2K', '4K'].map(value => ({ value, label: value }))} onChange={resolution => updateLocal(shot.id, { image_settings: { ...shot.image_settings, resolution } })}/></label>
                <label>图片比例<Select aria-label="图片比例" value={shot.image_settings.aspect} disabled={readOnly || lockedShots.has(shot.id)} options={['inherit', '16:9', '9:16', '1:1', '4:3', '3:4'].map(value => ({ value, label: value === 'inherit' ? '跟随本集画幅' : value }))} onChange={aspect => updateLocal(shot.id, { image_settings: { ...shot.image_settings, aspect } })}/></label>
                <label>图片布局<Select aria-label="图片布局" value={shot.image_settings.layout} disabled={readOnly || lockedShots.has(shot.id)} options={Object.entries({ single: '单图', four: '四宫格', five: '五宫格', nine: '九宫格' }).map(([value, label]) => ({ value, label }))} onChange={layout => updateLocal(shot.id, { image_settings: { ...shot.image_settings, layout } })}/></label>
              </div>}/>
            </div>}
          </article>)}
          <LazyLoadMore hasMore={!!page && page.items.length < page.total} loading={loading || moreLoading} error={moreError} onLoad={() => void loadMoreShots()}/>
        </div>}
      </section>
    </div>
    {historyOpen && <Dialog title="分镜生成记录" className="storyboard-history-dialog" canClose={!busy} onClose={() => { setHistoryOpen(false); setCandidate(null); }}>
      {candidate ? <><Button type="link" onClick={() => setCandidate(null)}>返回生成记录</Button><StoryboardResultPreview key={candidate.generation_id} projectId={projectId} episodeId={episodeId} generationId={candidate.generation_id} busy={busy} disabled={readOnly || loading || !!loadError || !page} error={message} onApply={mode => void applyResult(mode)}/></> : <div className="lazy-scroll storyboard-history-scroll">
        {!tasks.length && <p className="episode-help">暂无分镜生成记录，确认剧本后开始生成。</p>}
        {tasks.map(task => <div key={task.generation_id} className="resource-import-row"><div><strong>{taskLabel(task)}</strong><p>{new Date(task.created_at).toLocaleString('zh-CN')}</p><small>任务 {task.generation_id}</small>{task.error && <p role="alert">{task.error.message}</p>}</div><Button disabled={task.status !== 'succeeded'} onClick={() => { setMessage(''); setCandidate(task); }}>查看分镜</Button></div>)}
        <LazyLoadMore hasMore={tasks.length < tasksTotal} loading={tasksLoading} error={tasksError} onLoad={() => void loadMoreTasks()}/>
      </div>}
    </Dialog>}
  </div>;
}
