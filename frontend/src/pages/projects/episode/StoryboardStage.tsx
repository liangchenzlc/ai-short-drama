import { CreationSlot, useEpisodeCreationControls } from '../../../features/projects/EpisodeCreationWorkspace';
import { confirmAction } from '../../../components/ui/confirm';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Alert, Button, Dropdown, Input, InputNumber, Segmented, Select } from 'antd';
import { Icon } from '../../../components/ui/Icon';
import { ApiError, errorMessage } from '../../../api/http';
import { storyboardApi, type ShotRead, type StoryboardPage } from '../../../api/modules/storyboard';
import { assetLibraries, type LibraryAssetRead } from '../../../api/modules/assets';
import { generations } from '../../../api/modules/generations';
import { aiModelConfigs } from '../../../api/modules/ai-model-configs';
import { AI_CONFIGS_CHANGED } from '../../../features/ai-config/config-events';
import type { GenerationReceipt, GenerationSummary } from '../../../api/types/generations';
import { EpisodeModelSelect } from '../../../features/projects/EpisodeModelSelect';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import type { WritingSession } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import { scriptShotsRequest } from '../../../features/projects/workflow-contract';
import { hasUnsettledStoryboard, mergeStoryboardReload, shouldPollStoryboardTasks } from '../../../features/projects/storyboard-session';
import { attemptStorage, clearAttempt, requestAttempt } from '../../../features/generations/attempt';
import { taskLabel } from '../../../features/generations/presentation';
import { TaskDetail } from '../../../features/generations/TaskDetail';
import { StoryboardResultPreview } from '../../../features/projects/StoryboardResultPreview';
import { Dialog } from '../../../components/ui/Dialog';
import { LazyLoadMore } from '../../../components/ui/LazyLoadMore';
import { ShotAssetPicker } from '../../../features/projects/ShotAssetPicker';
import { ShotVideoCandidates } from '../../../features/projects/ShotVideoCandidates';
import { NativeDialoguePanel } from '../../../features/projects/NativeVoicePanel';
import { StoryboardShotCard } from '../../../features/projects/StoryboardShotCard';
import { ShotImageCandidates } from '../../../features/projects/ShotImageCandidates';
import { BatchLauncher, useBatchSelection } from '../../../features/generations/BatchGeneration';
import { prepareShotOperation, type ImageCapabilities, type PreparedShot } from '../../../features/projects/shot-image-workflow';
import '../../../features/projects/workflow-refinement.css';
import '../../../features/projects/storyboard-layout.css';

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
  writingSession, registerBarrier, refreshToken = 0,
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
  refreshToken?: number;
}) {
  const api = storyboardApi(projectId, episodeId);
  const { revealPanel, subject, selectSubject } = useEpisodeCreationControls();
  const batchSelection = useBatchSelection(`${projectId}:${episodeId}`);
  const [page, setPageState] = useState<StoryboardPage | null>(null);
  const pageRef = useRef<StoryboardPage | null>(null);
  const extraShots = useRef(new Map<string, ShotRead>());
  const [, setDetailRevision] = useState(0);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detailRetry, setDetailRetry] = useState(0);
  const [unavailableDraftId, setUnavailableDraftId] = useState<string | null>(null);
  const [assets, setAssets] = useState<LibraryAssetRead[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(true);
  const [assetsError, setAssetsError] = useState('');
  const [assetsRevision, setAssetsRevision] = useState(0);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [candidate, setCandidate] = useState<GenerationSummary | null>(null);
  const [taskDetailId, setTaskDetailId] = useState<string | null>(null);
  const shotMutation = useRef<symbol | null>(null);
  const [instructions, setInstructions] = useState('');
  const [durationMode, setDurationMode] = useState('3000');
  const [averageShotDurationMs, setAverageShotDurationMs] = useState(3000);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submittedTask, setSubmittedTask] = useState<GenerationReceipt | null>(null);
  const submissionLock = useRef(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mediaTab, setMediaTab] = useState<string>('image');
  const [editorTab, setEditorTab] = useState('info');
  const [batchMode, setBatchMode] = useState(false);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const selectedShotRef = useRef<string | null>(null);
  const selectionEpoch = useRef(0);
  const locateShot = useRef<string | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState('');
  const moreLock = useRef(false);
  const [tasksTotal, setTasksTotal] = useState(0);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const [tasksError, setTasksError] = useState('');
  const taskMoreLock = useRef(false);
  const [storyboardRevision, setStoryboardRevision] = useState(0);
  const [taskRevision, setTaskRevision] = useState(0);
  const dialogueBarrier = useRef<NavigationBarrier | null>(null);
  const registerDialogueBarrier = useCallback((barrier: NavigationBarrier | null) => { dialogueBarrier.current = barrier; }, []);
  const leaveDialogue = (next: () => void) => {
    if (!dialogueBarrier.current?.hasUnsettled()) { next(); return; }
    void confirmAction('分镜对白尚未保存，确定放弃并切换？').then(accepted => { if (accepted) next(); });
  };
  const dirty = useRef(new Set<string>());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const saving = useRef(new Map<string, Promise<boolean>>());
  const shotLocks = useRef(new Map<string, symbol>());
  const [lockedShots, setLockedShots] = useState<ReadonlySet<string>>(new Set());
  const mounted = useRef(false);
  const contextEpoch = useRef(0);
  const preparationEpoch = useRef(0);
  const adoptionEpoch = useRef(0);
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
  const generationPending = shouldPollStoryboardTasks(tasks) || !!submittedTask && (submittedTask.status === 'queued' || submittedTask.status === 'running');

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
    extraShots.current.clear(); setDetailError(''); setDetailLoading(false); setUnavailableDraftId(null); locateShot.current = null;
    setAssets([]); setAssetsLoading(true); setAssetsError(''); setTasks([]); setTasksTotal(0); setTasksLoading(true); setTasksLoaded(false); setTasksError(''); selectShotId(subject?.type === 'shot' ? subject.id : null); setCandidate(null); setMessage(''); setBusy(false); setSubmittedTask(null); setSubmitting(false); setSettingsOpen(false); submissionLock.current = false;
    setLockedShots(new Set());
    setTaskDetailId(null); shotMutation.current = null;
    setEditorTab('info'); setBatchMode(false);
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

  useLayoutEffect(() => {
    adoptionEpoch.current++;
  }, [readOnly, value.aspect]);

  function isCurrentContext(epoch: number) {
    return mounted.current && currentContext.current === contextKey && contextEpoch.current === epoch;
  }

  function setPage(next: StoryboardPage | null) {
    pageRef.current = next;
    setPageState(next);
  }

  function getShot(id: string) {
    return pageRef.current?.items.find(shot => shot.id === id) ?? extraShots.current.get(id);
  }

  function setShot(shot: ShotRead, storyboardVersion?: string) {
    const current = pageRef.current;
    if (current?.items.some(item => item.id === shot.id)) {
      setPage({ ...current, storyboard_version: storyboardVersion ?? current.storyboard_version, items: current.items.map(item => item.id === shot.id ? shot : item) });
    } else {
      extraShots.current.set(shot.id, shot);
      setDetailRevision(revision => revision + 1);
      if (current && storyboardVersion) setPage({ ...current, storyboard_version: storyboardVersion });
    }
  }

  function selectShotId(id: string | null) {
    if (selectedShotRef.current !== id) { selectionEpoch.current++; setDetailLoading(false); setDetailError(''); }
    selectedShotRef.current = id;
    setSelectedShotId(id);
  }

  function mergeLoadedPage(remote: StoryboardPage) {
    const unsettled = unsettledIds();
    const items = remote.items.map(item => {
      const extra = extraShots.current.get(item.id);
      extraShots.current.delete(item.id);
      return extra && unsettled.has(item.id) ? extra : item;
    });
    return mergeStoryboardReload({ ...remote, items }, pageRef.current, unsettled);
  }

  function unavailableShot(id: string, archived?: ShotRead) {
    const local = getShot(id);
    if (local && unsettledIds().has(id)) {
      setShot({ ...local, deleted_at: archived?.deleted_at ?? new Date().toISOString() });
      setUnavailableDraftId(id);
      setMessage('当前分镜已归档或删除，未保存的输入仍保留。请下载分镜草稿后核对。');
    } else setMessage('当前分镜已归档或删除，已返回本集分镜规划。');
    selectShotId(null); selectSubject?.(null); setDetailLoading(false);
  }

  function downloadShotDraft() {
    const shot = unavailableDraftId ? getShot(unavailableDraftId) : undefined;
    if (!shot) return;
    const { id, row_version, script, duration_ms, asset_ids, image_settings, video_prompt, video_settings } = shot;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ project_id: projectId, episode_id: episodeId, id, row_version, script, duration_ms, asset_ids, image_settings, video_prompt, video_settings }, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `shot-${id}-draft.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  function unsettledIds() {
    return new Set([...dirty.current, ...saving.current.keys(), ...timers.current.keys(), ...shotLocks.current.keys()]);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError(''); setMoreError('');
    loadLoadedShots(api, controller.signal, pageRef.current?.items.length ?? 20).then(remote => {
      if (controller.signal.aborted) return;
      setPage(mergeLoadedPage(remote));
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [projectId, episodeId, storyboardRevision, refreshToken]);

  useEffect(() => {
    const controller = new AbortController();
    setAssetsLoading(true); setAssetsError('');
    loadAllAssets(projectId, episodeId, controller.signal)
      .then(library => { if (!controller.signal.aborted) setAssets(library); })
      .catch(cause => { if (!controller.signal.aborted) setAssetsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setAssetsLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, assetsRevision, refreshToken]);

  useEffect(() => {
    const controller = new AbortController();
    setTasksLoading(true); setTasksError('');
    generations.list({ service_type: 'text', project_id: projectId, episode_id: episodeId, source_scene: 'script_shots', offset: 0, limit: 20 }, controller.signal)
      .then((page) => { if (!controller.signal.aborted) { setTasks(current => [...page.items, ...current.filter(task => !page.items.some(next => next.generation_id === task.generation_id))]); setTasksTotal(page.total); setTasksLoaded(true); setSubmittedTask(current => current && !page.items.some(task => task.generation_id === current.generation_id) ? current : null); } })
      .catch((cause) => { if (!controller.signal.aborted) setTasksError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setTasksLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, scriptId, taskRevision]);

  useEffect(() => {
    if (!generationPending) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [generationPending, tasks, submittedTask]);

  function updateLocal(id: string, patch: Partial<Pick<ShotRead, 'script' | 'duration_ms' | 'asset_ids' | 'image_settings' | 'video_prompt' | 'video_settings'>>) {
    const shot = getShot(id);
    if (!shot || !isCurrentContext(contextEpoch.current) || currentAccess.current.readOnly || shotMutation.current || shotLocks.current.has(id) || shot.deleted_at) return;
    setShot({ ...shot, ...patch });
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
    const shot = getShot(id);
    if (!shot || shot.deleted_at) return Promise.resolve(false);
    if (!dirty.current.has(id)) return Promise.resolve(true);
    dirty.current.delete(id);
    const operation = api.update(id, {
      row_version: shot.row_version,
      script: shot.script,
      duration_ms: shot.duration_ms,
      asset_ids: shot.asset_ids,
      image_settings: shot.image_settings,
      video_prompt: shot.video_prompt,
      video_settings: shot.video_settings,
    }).then((result) => {
      if (!isCurrentContext(epoch)) return false;
      const listPending = !pageRef.current;
      const newer = dirty.current.has(id) || timers.current.has(id);
      const local = getShot(id);
      if (!local) return false;
      setShot(newer
        ? { ...local, row_version: result.shot.row_version, context_hash: result.shot.context_hash, image: result.shot.image, video: result.shot.video, video_context_hash: result.shot.video_context_hash, video_default_prompt: result.shot.video_default_prompt }
        : result.shot, result.storyboard_version);
      if (listPending) setStoryboardRevision(revision => revision + 1);
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

  async function prepareShot(id: string, purpose: 'generation' | 'adoption' = 'generation'): Promise<PreparedShot | null> {
    const epoch = contextEpoch.current;
    // Adopting an existing image does not depend on the next generation model.
    // A native confirmation can restore window focus and refresh capabilities.
    const operationEpoch = purpose === 'adoption' ? adoptionEpoch : preparationEpoch;
    const preparation = operationEpoch.current;
    const access = currentAccess.current;
    const shot = getShot(id);
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
        local: () => getShot(id),
        read: async () => {
          const result = await api.shot(id);
          storyboardVersion = result.storyboard_version;
          return result.shot;
        },
        valid: () => {
          if (!isCurrentContext(epoch)) return false;
          const current = getShot(id);
          const valid = operationEpoch.current === preparation && !currentAccess.current.readOnly && !!current && !current.deleted_at
            && !dirty.current.has(id) && !saving.current.has(id) && !timers.current.has(id);
          if (!valid) setMessage('分镜或本集设置已变化，请核对后重新操作。未保存的输入仍保留。');
          return valid;
        },
        accept: (remote) => {
          setShot(remote, storyboardVersion);
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
      hasUnsettled: () => !!dialogueBarrier.current?.hasUnsettled() || shotLocks.current.size > 0 || hasUnsettledStoryboard(dirty.current, saving.current, timers.current),
      flush: async () => !dialogueBarrier.current?.hasUnsettled() && shotLocks.current.size === 0 && await flushAll() && shotLocks.current.size === 0,
    };
    registerBarrier(barrier);
    return () => registerBarrier(null);
  });

  async function add() {
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    const current = pageRef.current;
    if (!current || readOnly || busy || shotLocks.current.size || !await flushAll() || shotLocks.current.size) return;
    setBusy(true); setMessage('');
    const scope = `new-shot:${projectId}:${episodeId}`;
    try {
      const body = { storyboard_version: pageRef.current!.storyboard_version };
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      const result = await api.create(body.storyboard_version, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      if (!isCurrentContext(epoch)) return;
      setShot(result.shot, result.storyboard_version);
      if (selectionEpoch.current === selection) {
        selectShotId(result.shot.id); selectSubject?.({ type: 'shot', id: result.shot.id, label: `分镜 ${String(result.shot.position).padStart(2, '0')}` }); revealPanel?.();
        locateShot.current = result.shot.id;
      }
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) { if (isCurrentContext(epoch)) setMessage(errorMessage(cause)); }
    finally { if (isCurrentContext(epoch)) setBusy(false); }
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
      setPage(mergeLoadedPage({ ...latest, total: next.total, items: [...latest.items, ...next.items.filter(item => !latest.items.some(own => own.id === item.id))] }));
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
    if (submissionLock.current || busy || generationPending || readOnly || !confirmed || !scriptId) return;
    submissionLock.current = true;
    setSubmitting(true); setMessage('');
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
      setSubmittedTask(task);
      clearAttempt(scope, attemptStorage());
      setMessage(`分镜生成任务 ${task.generation_id} 已提交。`);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { setMessage(errorMessage(cause)); }
    finally { submissionLock.current = false; setSubmitting(false); }
  }

  async function applyResult(mode: 'append' | 'replace') {
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    if (readOnly || loading || loadError || !pageRef.current) {
      setMessage('分镜列表尚未就绪，请先重新加载分镜列表后再采用。');
      return;
    }
    if (!candidate || busy || shotLocks.current.size || !await flushAll() || shotLocks.current.size) return;
    const current = pageRef.current;
    if (!current) return;
    if (mode === 'replace' && !await confirmAction(`替换会移出当前 ${current.total} 个分镜，旧媒体与历史会保留。确定继续？`)) return;
    setBusy(true); setMessage('');
    try {
      await api.apply(candidate.generation_id, {
        mode,
        content_version: writingSession.getSnapshot().contentVersion,
        storyboard_version: current.storyboard_version,
        confirm_replace: mode === 'replace',
      });
      if (!isCurrentContext(epoch)) return;
      setCandidate(null); setHistoryOpen(false);
      if (mode === 'replace' && selectionEpoch.current === selection) { selectShotId(null); selectSubject?.(null); }
      setStoryboardRevision((revision) => revision + 1);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) { if (isCurrentContext(epoch)) setMessage(errorMessage(cause)); }
    finally { if (isCurrentContext(epoch)) setBusy(false); }
  }

  const selectedShot = selectedShotId ? getShot(selectedShotId) : undefined;
  useLayoutEffect(() => { selectShotId(subject?.type === 'shot' ? subject.id : null); }, [subject?.type, subject?.id]);
  useEffect(() => {
    if (!selectedShotId || selectedShotRef.current !== selectedShotId || unsettledIds().has(selectedShotId)) return;
    const loaded = getShot(selectedShotId);
    if (loaded && !storyboardRevision && !refreshToken && !detailRetry) return;
    const controller = new AbortController();
    const epoch = contextEpoch.current;
    const id = selectedShotId;
    setDetailLoading(true); setDetailError('');
    api.shot(id, controller.signal).then(result => {
      if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
      if (result.shot.deleted_at) { unavailableShot(id, result.shot); return; }
      if (!unsettledIds().has(id)) setShot(result.shot);
    }).catch(cause => {
      if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
      if (cause instanceof ApiError && cause.status === 404) unavailableShot(id);
      else setDetailError(errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted && isCurrentContext(epoch) && selectedShotRef.current === id) setDetailLoading(false); });
    return () => controller.abort();
  }, [contextKey, selectedShotId, storyboardRevision, refreshToken, detailRetry]);
  useEffect(() => {
    if (!locateShot.current || !page || loading || moreLoading) return;
    const id = locateShot.current;
    if (page.items.some(shot => shot.id === id)) {
      document.getElementById(`storyboard-shot-${id}`)?.scrollIntoView({ block: 'nearest' });
      locateShot.current = null;
    } else if (page.items.length < page.total && !moreError) void loadMoreShots();
  }, [page, loading, moreLoading, moreError]);
  useEffect(() => {
    if (selectedShot && subject?.id === selectedShot.id) selectSubject?.({ type: 'shot', id: selectedShot.id, label: `分镜 ${String(selectedShot.position).padStart(2, '0')}`, revision: selectedShot.row_version });
  }, [selectedShot?.id, selectedShot?.position, selectedShot?.row_version, subject?.id, selectSubject]);
  const selectionDisabled = busy || loading || !!loadError || moreLoading || lockedShots.size > 0;
  const saveStatus = selectedShot ? saving.current.has(selectedShot.id) ? '保存中…' : dirty.current.has(selectedShot.id) ? '等待保存' : '已保存' : '';

  function selectShot(id: string) {
    if (selectionDisabled || shotLocks.current.size) return;
    leaveDialogue(() => {
      selectShotId(id);
      const shot = getShot(id);
      if (shot) selectSubject?.({ type: 'shot', id, label: `分镜 ${String(shot.position).padStart(2, '0')}`, revision: shot.row_version });
      revealPanel?.();
    });
  }

  async function selectAdjacent(direction: -1 | 1) {
    if (!selectedShot || selectionDisabled) return;
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    while (pageRef.current && pageRef.current.items.length < pageRef.current.total && !pageRef.current.items.some(shot => shot.position === selectedShot.position + direction)) {
      const count = pageRef.current.items.length;
      await loadMoreShots();
      if (!isCurrentContext(epoch) || selectionEpoch.current !== selection || pageRef.current.items.length === count) return;
    }
    if (!isCurrentContext(epoch) || selectionEpoch.current !== selection) return;
    const current = pageRef.current;
    const next = current?.items.find(shot => shot.position === selectedShot.position + direction);
    if (next) selectShot(next.id);
  }

  function selectEditorTab(tab: string, focus = false) {
    const change = () => {
      setEditorTab(tab);
      if (tab !== 'info') setMediaTab(tab);
      if (focus) document.getElementById('storyboard-' + tab + '-tab')?.focus();
    };
    if (tab !== 'info' && tab !== mediaTab) leaveDialogue(change);
    else change();
  }

  function navigateEditorTabs(event: KeyboardEvent<HTMLButtonElement>) {
    const tabs = ['info', 'image', 'video'];
    const index = tabs.indexOf(editorTab);
    const next = event.key === 'Home' ? 'info' : event.key === 'End' ? 'video'
      : event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length]
        : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length] : null;
    if (next) { event.preventDefault(); selectEditorTab(next, true); }
  }

  async function mutateShot(id: string, action: 'up' | 'down' | 'archive') {
    const access = currentAccess.current;
    if (shotMutation.current || access.readOnly || access.busy || access.loading || access.loadError || shotLocks.current.size || selectedShotRef.current !== id) return;
    if (dialogueBarrier.current?.hasUnsettled()) { setMessage('对白尚未保存，请先在对白窗口保存或明确放弃修改。'); return; }
    const epoch = contextEpoch.current;
    const token = Symbol(id);
    shotMutation.current = token; setBusy(true); setMessage('');
    try {
      if (!await flushAll() || !isCurrentContext(epoch) || selectedShotRef.current !== id || shotLocks.current.size) return;
      const shot = getShot(id);
      const current = pageRef.current;
      if (!shot || shot.deleted_at || !current || currentAccess.current.readOnly) return;
      if (action === 'archive') {
        if (!await confirmAction(`归档分镜 ${String(shot.position).padStart(2, '0')} 会将它移出本集，旧媒体与历史仍保留。确定继续？`)) return;
        if (!isCurrentContext(epoch) || currentAccess.current.readOnly || selectedShotRef.current !== id) return;
        await api.remove(id, shot.row_version);
        if (!isCurrentContext(epoch)) return;
        if (selectedShotRef.current === id) { selectShotId(null); selectSubject?.(null); }
      } else {
        if (action === 'up' && shot.position <= 1 || action === 'down' && shot.position >= current.total) return;
        await api.move(id, current.storyboard_version, action === 'up' ? -1 : 1);
        if (!isCurrentContext(epoch)) return;
      }
      setStoryboardRevision(revision => revision + 1);
    } catch (cause) { if (isCurrentContext(epoch)) setMessage(errorMessage(cause)); }
    finally { if (shotMutation.current === token) shotMutation.current = null; if (isCurrentContext(epoch)) setBusy(false); }
  }

  const shotActions = selectedShot && !readOnly ? <Dropdown trigger={['click']} menu={{ items: [
    { key: 'up', label: '上移', disabled: selectedShot.position <= 1 },
    { key: 'down', label: '下移', disabled: !page || selectedShot.position >= page.total },
    { key: 'archive', label: '归档', danger: true },
  ], onClick: ({ key }) => mutateShot(selectedShot.id, key as 'up' | 'down' | 'archive') }}>
    <Button type="text" aria-label={`分镜 ${String(selectedShot.position).padStart(2, '0')} 更多操作`} disabled={selectionDisabled} icon={<Icon name="more" size={18}/>}/>
  </Dropdown> : null;

  const shotInformation = selectedShot ? <div className="storyboard-shot-information" key={selectedShot.id}>
    <div className="shot-script-panel">
      <div className="shot-script-heading"><strong>分镜脚本</strong><span role="status">{saveStatus}</span></div>
      <Input.TextArea aria-label={'分镜 ' + selectedShot.position + ' 脚本'} autoSize={{ minRows: 5, maxRows: 12 }} value={selectedShot.script} disabled={readOnly || !!shotMutation.current || lockedShots.has(selectedShot.id)} onChange={event => updateLocal(selectedShot.id, { script: event.target.value })}/>
    </div>
    <label className="storyboard-duration-field"><span>镜头时长</span><InputNumber aria-label={'分镜 ' + selectedShot.position + ' 时长'} min={1} max={10} step={0.5} precision={1} value={selectedShot.duration_ms / 1000} suffix="秒" disabled={readOnly || !!shotMutation.current || lockedShots.has(selectedShot.id)} onChange={seconds => { if (seconds !== null) updateLocal(selectedShot.id, { duration_ms: Math.round(seconds * 1000) }); }}/></label>
    <ShotAssetPicker assets={assets} assetIds={selectedShot.asset_ids} disabled={readOnly || busy || assetsLoading || !!assetsError || lockedShots.has(selectedShot.id)} onChange={asset_ids => updateLocal(selectedShot.id, { asset_ids })}/>
  </div> : null;

  return <div className="storyboard-workspace">
    <div className="episode-stage-heading storyboard-heading">
      <div><h2>分镜制作</h2><div className="storyboard-stage-meta"><span>{page ? '本集共 ' + page.total + ' 镜' : loading ? '正在载入分镜' : '尚未加载分镜'}</span><span>{confirmed ? '剧本已确认' : '剧本待确认'}</span></div></div>
      <div className="storyboard-heading-actions"><Button disabled={readOnly || busy || loading || !!loadError || !page} onClick={() => void add()}>新增分镜</Button><Button disabled={readOnly || busy || submitting} onClick={() => setSettingsOpen(true)}>提取分镜</Button><Button onClick={() => { setHistoryOpen(true); setCandidate(null); setTasksLoading(true); setTaskRevision(revision => revision + 1); }}>历史记录</Button></div>
    </div>
    {message && <Alert type={message.includes('其他窗口') ? 'warning' : 'info'} showIcon message={message} action={unavailableDraftId ? <Button onClick={downloadShotDraft}>下载分镜草稿</Button> : undefined}/>}
    {detailError && <Alert type="error" showIcon message={'分镜详情加载失败：' + detailError} action={<Button loading={detailLoading} onClick={() => setDetailRetry(revision => revision + 1)}>重新加载分镜详情</Button>}/>}
    {assetsError && <Alert type="error" showIcon message={'关联素材加载失败：' + assetsError} action={<Button loading={assetsLoading} onClick={() => setAssetsRevision(revision => revision + 1)}>重新加载关联素材</Button>}/>}
    {loadError && <Alert type="error" showIcon message={'分镜列表加载失败：' + loadError} action={<Button loading={loading} onClick={() => setStoryboardRevision(revision => revision + 1)}>重新加载分镜列表</Button>}/>}
    <div className="creation-workspace storyboard-columns">
      <CreationSlot stage="storyboard">
        <aside className="storyboard-prompt-panel" aria-label="分镜媒体设置">
          {selectedShot ? <>
            <header className="storyboard-selection-heading">
              <div><h3>分镜 {String(selectedShot.position).padStart(2, '0')}</h3><span role="status">{saveStatus}</span>{shotActions}</div>
              <nav aria-label="切换编辑镜头"><Button type="text" aria-label="上一个分镜" disabled={selectionDisabled || selectedShot.position <= 1} onClick={() => void selectAdjacent(-1)}><Icon name="back" size={16}/></Button><Button type="text" aria-label="下一个分镜" loading={moreLoading} disabled={selectionDisabled || !page || selectedShot.position >= page.total} onClick={() => void selectAdjacent(1)}><Icon name="arrow" size={16}/></Button></nav>
            </header>
            <div className="storyboard-editor-tabs" role="tablist" aria-label="镜头制作内容">
              {([{ key: 'info', label: '镜头信息' }, { key: 'image', label: '分镜图' }, { key: 'video', label: '分镜视频' }]).map(tab => <button key={tab.key} type="button" role="tab" id={'storyboard-' + tab.key + '-tab'} aria-selected={editorTab === tab.key} aria-controls={'storyboard-' + tab.key + '-pane'} tabIndex={editorTab === tab.key ? 0 : -1} disabled={lockedShots.has(selectedShot.id)} onClick={() => selectEditorTab(tab.key)} onKeyDown={navigateEditorTabs}>{tab.label}</button>)}
            </div>
            <div className="storyboard-info-pane" id="storyboard-info-pane" role="tabpanel" aria-labelledby="storyboard-info-tab" hidden={editorTab !== 'info'}>{shotInformation}</div>
          </> : detailLoading ? <div role="status">正在载入分镜详情…</div> : <div className="storyboard-prompt-empty"><strong>选择一个镜头开始制作</strong><p>在左侧卡片中选择镜头，编辑脚本、关联素材或制作图片与视频。</p></div>}
          <label className="writing-control storyboard-image-model" hidden={!selectedShot || editorTab !== 'image'}><span>分镜生图模型</span><EpisodeModelSelect kind="image" label="分镜生图模型" value={value.models.storyboardImage} disabled={readOnly || busy || lockedShots.size > 0} onResolvedChange={onResolvedImageModel} onChange={id => onChange({ ...value, models: { ...value.models, storyboardImage: id } })}/></label>
          {selectedShot && <div className="storyboard-media-pane" id={'storyboard-' + mediaTab + '-pane'} role="tabpanel" aria-labelledby={'storyboard-' + mediaTab + '-tab'} hidden={editorTab !== mediaTab}>
            {mediaTab === 'video' && <NativeDialoguePanel key={selectedShot.id} revision={storyboardRevision} registerBarrier={registerDialogueBarrier} projectId={projectId} episodeId={episodeId} shotId={selectedShot.id} disabled={readOnly || busy || lockedShots.has(selectedShot.id)} prepare={() => saveShot(selectedShot.id)} onChanged={() => setStoryboardRevision(revision => revision + 1)}/>}
            {mediaTab === 'video' ? <ShotVideoCandidates key={selectedShot.id} shot={selectedShot} disabled={readOnly || busy || lockedShots.has(selectedShot.id)} model={value.models.video} onModelChange={id => onChange({ ...value, models: { ...value.models, video: id } })} onEdit={patch => updateLocal(selectedShot.id, patch)} prepareShot={() => prepareShot(selectedShot.id, 'adoption')} onChanged={() => setStoryboardRevision(revision => revision + 1)}/> : <ShotImageCandidates key={selectedShot.id} shot={selectedShot} disabled={readOnly || busy || lockedShots.has(selectedShot.id)} modelId={imageModelId} capabilities={imageCapabilities} capabilitiesLoading={capabilitiesLoading} onRefreshCapabilities={refreshCapabilities} episodeAspect={value.aspect} prepareShot={purpose => prepareShot(selectedShot.id, purpose)} onChanged={() => setStoryboardRevision(revision => revision + 1)} settings={<>
              <label>图片清晰度<Select aria-label="图片清晰度" value={selectedShot.image_settings.resolution} disabled={readOnly || lockedShots.has(selectedShot.id)} options={['1K', '2K', '4K'].map(value => ({ value, label: value }))} onChange={resolution => updateLocal(selectedShot.id, { image_settings: { ...selectedShot.image_settings, resolution } })}/></label>
              <label>图片比例<Select aria-label="图片比例" value={selectedShot.image_settings.aspect} disabled={readOnly || lockedShots.has(selectedShot.id)} options={['inherit', '16:9', '9:16', '1:1', '4:3', '3:4'].map(value => ({ value, label: value === 'inherit' ? '跟随本集画幅' : value }))} onChange={aspect => updateLocal(selectedShot.id, { image_settings: { ...selectedShot.image_settings, aspect } })}/></label>
              <label>图片布局<Select aria-label="图片布局" value={selectedShot.image_settings.layout} disabled={readOnly || lockedShots.has(selectedShot.id)} options={Object.entries({ single: '单图', four: '四宫格', five: '五宫格', nine: '九宫格' }).map(([value, label]) => ({ value, label }))} onChange={layout => updateLocal(selectedShot.id, { image_settings: { ...selectedShot.image_settings, layout } })}/></label>
            </>}/>}
          </div>}
        </aside>
      </CreationSlot>
      <section className="creation-editor storyboard-editor" aria-label="分镜创作区域">
        <div className="storyboard-editor-heading">
          <div><h3>本集分镜</h3>{selectedShot && <span className="storyboard-selected-caption">已选 {String(selectedShot.position).padStart(2, '0')}</span>}</div>
          {batchSelection.enabled && !readOnly && <Button type="text" aria-pressed={batchMode} disabled={selectionDisabled} onClick={() => setBatchMode(current => !current)}>{batchMode ? '退出批量操作' : '批量操作'}</Button>}
        </div>
        {!readOnly && <BatchLauncher scope={{ library: 'episode', project_id: projectId, episode_id: episodeId }} selection={{ ...batchSelection, enabled: batchSelection.enabled && batchMode }} loadedIds={page?.items.map(shot => shot.id) ?? []} disabled={busy || loading || !!loadError || lockedShots.size > 0} beforePreflight={flushAll}/>}
        {moreError && <Button onClick={() => setStoryboardRevision(revision => revision + 1)}>重新加载分镜列表</Button>}
        <div className="storyboard-list lazy-scroll" aria-busy={loading}>
          {loading && !page ? Array.from({ length: 6 }, (_, index) => <div className="storyboard-card-skeleton" key={index} role={index === 0 ? 'status' : undefined} aria-label={index === 0 ? '正在加载分镜' : undefined} aria-hidden={index !== 0}><span/><div><i/><i/><i/></div></div>) : <>
            {page && !page.items.length && !loadError && <div className="studio-empty"><h3>还没有镜头</h3><p>确认剧本后生成分镜，或手动新增第一个镜头。</p><Button disabled={readOnly || busy || loading} onClick={() => void add()}>新增第一个分镜</Button></div>}
            {page?.items.map(shot => <StoryboardShotCard key={shot.id} shot={shot} selected={selectedShotId === shot.id} disabled={selectionDisabled} batch={batchSelection.enabled && batchMode && !readOnly} checked={batchSelection.ids.includes(shot.id)} onSelect={() => selectShot(shot.id)} onCheck={checked => batchSelection.toggle(shot.id, checked)}/>)}
            <LazyLoadMore hasMore={!!page && page.items.length < page.total} loading={loading || moreLoading} error={moreError} onLoad={() => void loadMoreShots()}/>
          </>}
        </div>
      </section>
    </div>
    {settingsOpen && <Dialog title="提取分镜" className="storyboard-settings-dialog" canClose={!submitting} onClose={() => setSettingsOpen(false)}>
      <div className="storyboard-settings-body">
        <p className="episode-help">从当前已确认剧本生成分镜候选，完成后在历史记录中预览并明确采用。</p>
        <label className="writing-control"><span>文本模型</span><EpisodeModelSelect kind="text" label="分镜模型" value={value.models.storyboardText} disabled={readOnly || submitting} onChange={id => onChange({ ...value, models: { ...value.models, storyboardText: id } })}/></label>
        <div className="writing-control"><span>平均镜头时长</span><Segmented block aria-label="平均镜头时长预设" value={durationMode} disabled={readOnly || submitting} options={[{ label: '2 秒', value: '2000' }, { label: '3 秒', value: '3000' }, { label: '5 秒', value: '5000' }, { label: '自定', value: 'custom' }]} onChange={next => { const mode = String(next); setDurationMode(mode); if (mode !== 'custom') setAverageShotDurationMs(Number(mode)); }}/>
          {durationMode === 'custom' && <InputNumber aria-label="自定义平均镜头时长" min={1} max={10} step={0.5} precision={1} value={averageShotDurationMs / 1000} addonAfter="秒" disabled={readOnly || submitting} onChange={seconds => { if (seconds !== null) setAverageShotDurationMs(Math.round(seconds * 1000)); }}/>}</div>
        <label className="writing-control"><span>分镜要求描述</span><Input.TextArea rows={4} maxLength={4000} value={instructions} onChange={event => setInstructions(event.target.value)} aria-label="分镜要求描述" disabled={readOnly || submitting} placeholder="例如：更多近景，突出人物情绪"/></label>
        {!confirmed && <Alert type="info" showIcon message="请先在小说改编中确认当前剧本。"/>}
        {generationPending && <p role="status" className="episode-help">分镜正在生成，可以关闭弹窗继续编辑，完成后从历史记录中预览采用。</p>}
        {message && <Alert type="info" showIcon message={message}/>}
      </div>
      <footer className="storyboard-settings-footer"><Button disabled={submitting} onClick={() => setSettingsOpen(false)}>关闭</Button><Button type="primary" aria-label="生成分镜" aria-busy={submitting || generationPending} loading={submitting || generationPending} disabled={readOnly || busy || generationPending || !confirmed || !scriptId} onClick={() => void generateStoryboard()}>生成分镜</Button></footer>
    </Dialog>}
    {historyOpen && <Dialog title="分镜生成记录" className="storyboard-history-dialog" canClose={!busy} onClose={() => { setHistoryOpen(false); setCandidate(null); }}>
      {candidate ? <><Button type="link" onClick={() => setCandidate(null)}>返回生成记录</Button><StoryboardResultPreview key={candidate.generation_id} projectId={projectId} episodeId={episodeId} generationId={candidate.generation_id} busy={busy} disabled={readOnly || loading || !!loadError || !page} error={message} onApply={mode => void applyResult(mode)}/></> : <div className="lazy-scroll storyboard-history-scroll">
        {tasksLoading && !tasks.length && <p role="status" className="episode-help">正在加载分镜生成记录…</p>}
        {tasksError && <Alert type="error" showIcon message={'分镜生成记录加载失败：' + tasksError} action={<Button loading={tasksLoading} onClick={() => setTaskRevision(revision => revision + 1)}>重新加载生成记录</Button>}/>}
        {tasksLoaded && !tasksLoading && !tasksError && !tasks.length && <p className="episode-help">暂无分镜生成记录，确认剧本后开始生成。</p>}
        {tasks.map(task => <div key={task.generation_id} className="resource-import-row"><div><strong>{taskLabel(task)}</strong><p>{new Date(task.created_at).toLocaleString('zh-CN')}</p><small>任务 {task.generation_id}</small>{task.error && <p role="alert">{task.error.message}</p>}</div>{task.status === 'succeeded' ? <Button onClick={() => { setMessage(''); setCandidate(task); }}>查看分镜</Button> : <Button onClick={() => setTaskDetailId(task.generation_id)}>查看任务</Button>}</div>)}
        <LazyLoadMore hasMore={tasks.length < tasksTotal} loading={tasksLoading} error={tasksError} onLoad={() => void loadMoreTasks()}/>
      </div>}
    </Dialog>}
    {taskDetailId && <TaskDetail id={taskDetailId} onClose={() => setTaskDetailId(null)} onChanged={() => setTaskRevision(revision => revision + 1)} onCreated={task => { setTaskDetailId(task.generation_id); setTaskRevision(revision => revision + 1); }}/ >}
  </div>;
}
