import { EpisodeEditorSlot, useEpisodeCreationControls } from '../../../features/projects/EpisodeCreationWorkspace';
import { confirmAction } from '../../../components/ui/confirm';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react';
import { Button, Dropdown, Input, InputNumber, Segmented, Select } from 'antd';
import { StudioFeedback as Alert, feedbackType } from '../../../components/ui/StudioFeedback';
import { Icon } from '../../../components/ui/Icon';
import { Ellipsis, History, PanelRight, Sparkles } from 'lucide-react';
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
import { mergeStoryboardReload, shouldPollStoryboardTasks } from '../../../features/projects/storyboard-session';
import { attemptStorage, clearAttempt, requestAttempt } from '../../../features/generations/attempt';
import { taskLabel } from '../../../features/generations/presentation';
import { TaskDetail } from '../../../features/generations/TaskDetail';
import { StoryboardResultPreview } from '../../../features/projects/StoryboardResultPreview';
import { Dialog } from '../../../components/ui/Dialog';
import { LazyLoadMore } from '../../../components/ui/LazyLoadMore';
import { ShotVideoCandidates } from '../../../features/projects/ShotVideoCandidates';
import { NativeDialoguePanel } from '../../../features/projects/NativeVoicePanel';
import { StoryboardShotCard } from '../../../features/projects/StoryboardShotCard';
import { ShotImageCandidates } from '../../../features/projects/ShotImageCandidates';
import { BatchLauncher, useBatchSelection } from '../../../features/generations/BatchGeneration';
import { prepareShotOperation, type ImageCapabilities, type PreparedShot } from '../../../features/projects/shot-image-workflow';
import { StoryboardEditingSession, shotDraftValue } from '../../../features/projects/storyboard-editing-session';
import { retainStandardSession, releaseStandardSession } from '../../../features/projects/standard-session-registry';
import { useAuth } from '../../../features/auth/AuthSession';
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

async function loadAllAssets(projectId: string, signal: AbortSignal) {
  const scope = { kind: 'project' as const, projectId };
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
  value, readOnly, onChange, projectId, scriptId,
  writingSession, registerBarrier, refreshToken = 0, sourceMedia,
}: {
  value: EpisodeWorkflow;
  readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string;

  contentVersion: string;
  scriptId: string | null;
  confirmed: boolean;
  writingSession: WritingSession;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  refreshToken?: number;
  sourceMedia?: 'image' | 'video';
}) {
  const api = storyboardApi(projectId);
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous';
  const editingKey = `storyboard-edits:${projectId}`;
  const editing = useMemo(() => retainStandardSession(account, editingKey, () => new StoryboardEditingSession(projectId, account), { label: '分镜', projectId }), [account, editingKey, projectId]);
  const editingSnapshot = useSyncExternalStore(editing.subscribe, editing.getSnapshot);
  useEffect(() => () => releaseStandardSession(account, editingKey), [account, editingKey]);
  const { revealPanel, subject, selectSubject } = useEpisodeCreationControls();
  const batchSelection = useBatchSelection(`${projectId}`);
  const [page, setPageState] = useState<StoryboardPage | null>(null);
  const pageRef = useRef<StoryboardPage | null>(null);
  const extraShots = useRef(new Map<string, ShotRead>());
  const removedShotIds = useRef(new Set<string>());
  const storyboardVersionFloor = useRef('0');
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
  const [deletingShotId, setDeletingShotId] = useState<string | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<{ id: string; type: 'error' | 'warning'; message: string; description?: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submittedTask, setSubmittedTask] = useState<GenerationReceipt | null>(null);
  const submissionLock = useRef(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [actionsOrigin, setActionsOrigin] = useState<'main' | 'history' | null>(null);
  const actionsTrigger = useRef<HTMLButtonElement | null>(null);
  const [mediaHistoryOpen, setMediaHistoryOpen] = useState<'image' | 'video' | null>(null);
  const [mediaTab, setMediaTab] = useState<string>(sourceMedia ?? 'image');
  const [editorTab, setEditorTab] = useState<string>(sourceMedia ?? 'image');
  const sourceMediaRequest = useRef(sourceMedia);
  const [search, setSearch] = useState('');
  const [pendingOnly, setPendingOnly] = useState(false);
  const [mediaSettingsOpen, setMediaSettingsOpen] = useState(() => window.innerWidth > 700);
  const privateBarriers = useRef(new Map<string, NavigationBarrier>());
  const registerImageDraft = useCallback((barrier: NavigationBarrier | null) => { if (barrier) privateBarriers.current.set('image', barrier); else privateBarriers.current.delete('image'); }, []);
  const registerVideoDraft = useCallback((barrier: NavigationBarrier | null) => { if (barrier) privateBarriers.current.set('video', barrier); else privateBarriers.current.delete('video'); }, []);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const selectedShotRef = useRef<string | null>(null);
  const initialSelectionPending = useRef(true);
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
    if (dialogueBarrier.current?.hasUnsettled()) void dialogueBarrier.current.flush();
    next();
  };
  const shotLocks = useRef(new Map<string, symbol>());
  const [lockedShots, setLockedShots] = useState<ReadonlySet<string>>(new Set());
  const mounted = useRef(false);
  const contextEpoch = useRef(0);
  const preparationEpoch = useRef(0);
  const adoptionEpoch = useRef(0);
  const contextKey = `${projectId}`;
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
    initialSelectionPending.current = true;
    setPage(null);
    removedShotIds.current.clear(); storyboardVersionFloor.current = '0'; setDeletingShotId(null); setDeleteNotice(null);
    extraShots.current.clear(); setDetailError(''); setDetailLoading(false); setUnavailableDraftId(null); locateShot.current = null;
    setAssets([]); setAssetsLoading(true); setAssetsError(''); setTasks([]); setTasksTotal(0); setTasksLoading(true); setTasksLoaded(false); setTasksError(''); selectShotId(subject?.type === 'shot' ? subject.id : null); setCandidate(null); setMessage(''); setBusy(false); setSubmittedTask(null); setSubmitting(false); setSettingsOpen(false); submissionLock.current = false;
    setLockedShots(new Set());
    setTaskDetailId(null); shotMutation.current = null;
    setEditorTab(sourceMedia ?? 'image'); setMediaTab(sourceMedia ?? 'image'); setBatchMode(false);
    return () => {
      mounted.current = false;
      contextEpoch.current++;
      preparationEpoch.current++;
      shotLocks.current.clear();
    };
  }, [contextKey]);

  useLayoutEffect(() => {
    currentAccess.current = { readOnly, busy, loading, loadError };
  });

  useLayoutEffect(() => {
    preparationEpoch.current++;
  }, [readOnly, value.models.storyboardImage, imageModelId]);

  useLayoutEffect(() => {
    adoptionEpoch.current++;
  }, [readOnly]);

  function isCurrentContext(epoch: number) {
    return mounted.current && currentContext.current === contextKey && contextEpoch.current === epoch;
  }

  function setPage(next: StoryboardPage | null) {
    pageRef.current = next;
    setPageState(next);
  }

  function getShot(id: string) {
    return editing.get(id)?.shot ?? pageRef.current?.items.find(shot => shot.id === id) ?? extraShots.current.get(id);
  }

  function setShot(shot: ShotRead, storyboardVersion?: string) {
    if (removedShotIds.current.has(shot.id)) return;
    if (storyboardVersion && BigInt(storyboardVersion) < BigInt(storyboardVersionFloor.current)) return;
    editing.accept(shot, storyboardVersion);
    const current = pageRef.current;
    const version = storyboardVersion && (!current || BigInt(storyboardVersion) > BigInt(current.storyboard_version))
      ? storyboardVersion : current?.storyboard_version;
    if (current?.items.some(item => item.id === shot.id)) {
      setPage({ ...current, storyboard_version: version ?? current.storyboard_version, items: current.items.map(item => item.id === shot.id ? shot : item) });
    } else {
      extraShots.current.set(shot.id, shot);
      setDetailRevision(revision => revision + 1);
      if (current && version) setPage({ ...current, storyboard_version: version });
    }
  }

  useEffect(() => {
    if (!editingSnapshot.savedRevision) return;
    const listPending = !pageRef.current;
    for (const id of editing.ids()) {
      const resource = editing.get(id);
      if (resource) setShot(resource.shot, resource.storyboard_version);
    }
    if (listPending) setStoryboardRevision(revision => revision + 1);
    window.dispatchEvent(new Event('creation-workflow-updated'));
  }, [editing, editingSnapshot.savedRevision]);

  function selectShotId(id: string | null) {
    if (id) initialSelectionPending.current = false;
    if (selectedShotRef.current !== id) { selectionEpoch.current++; setDetailLoading(false); setDetailError(''); }
    selectedShotRef.current = id;
    setSelectedShotId(id);
  }

  function mergeLoadedPage(remote: StoryboardPage) {
    if (BigInt(remote.storyboard_version) < BigInt(storyboardVersionFloor.current)) return pageRef.current;
    if (pageRef.current && BigInt(remote.storyboard_version) < BigInt(pageRef.current.storyboard_version)) return pageRef.current;
    const unsettled = unsettledIds();
    const items = remote.items.filter(item => !removedShotIds.current.has(item.id)).map(item => {
      const extra = extraShots.current.get(item.id);
      extraShots.current.delete(item.id);
      return extra && unsettled.has(item.id) ? extra : unsettled.has(item.id) ? item : restoreShotDraft(item, remote.storyboard_version);
    });
    return mergeStoryboardReload({ ...remote, items }, pageRef.current, unsettled);
  }

  function restoreShotDraft(remote: ShotRead, storyboardVersion = pageRef.current?.storyboard_version): ShotRead {
    return editing.restore(remote, storyboardVersion);
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
    const url = URL.createObjectURL(new Blob([JSON.stringify({ project_id: projectId, id: shot.id, row_version: shot.row_version, ...shotDraftValue(shot) }, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `shot-${shot.id}-draft.json`; anchor.click(); URL.revokeObjectURL(url);
  }
  function downloadShotRecovery(id: string) {
    try {
      const raw = editing.exportRecovered(id); if (!raw) return;
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `shot-${id}-recovery.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setMessage(errorMessage(cause)); }
  }

  function unsettledIds() {
    return new Set([...editing.unsettledIds(), ...shotLocks.current.keys()]);
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
  }, [projectId, storyboardRevision, refreshToken]);

  useEffect(() => {
    const controller = new AbortController();
    setAssetsLoading(true); setAssetsError('');
    loadAllAssets(projectId, controller.signal)
      .then(library => { if (!controller.signal.aborted) setAssets(library); })
      .catch(cause => { if (!controller.signal.aborted) setAssetsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setAssetsLoading(false); });
    return () => controller.abort();
  }, [projectId, assetsRevision, refreshToken]);

  useEffect(() => {
    const controller = new AbortController();
    setTasksLoading(true); setTasksError('');
    generations.list({ service_type: 'text', project_id: projectId, source_scene: 'script_shots', offset: 0, limit: 20 }, controller.signal)
      .then((page) => { if (!controller.signal.aborted) { setTasks(current => [...page.items, ...current.filter(task => !page.items.some(next => next.generation_id === task.generation_id))]); setTasksTotal(page.total); setTasksLoaded(true); setSubmittedTask(current => current && !page.items.some(task => task.generation_id === current.generation_id) ? current : null); } })
      .catch((cause) => { if (!controller.signal.aborted) setTasksError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setTasksLoading(false); });
    return () => controller.abort();
  }, [projectId, scriptId, taskRevision]);

  useEffect(() => {
    if (!generationPending) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [generationPending, tasks, submittedTask]);

  function updateLocal(id: string, patch: Partial<Pick<ShotRead, 'title' | 'script' | 'duration_ms' | 'asset_ids' | 'image_settings' | 'video_prompt' | 'video_settings'>>) {
    const shot = getShot(id);
    if (!shot || !isCurrentContext(contextEpoch.current) || currentAccess.current.readOnly || shotMutation.current || shotLocks.current.has(id) || shot.deleted_at) return;
    const next = { ...shot, ...patch };
    editing.ensure(shot, pageRef.current?.storyboard_version); editing.edit(next); setShot(next);
  }

  async function saveShot(id: string): Promise<boolean> {
    if (currentAccess.current.readOnly) return false;
    const saved = await editing.flushShot(id);
    const resource = editing.get(id);
    if (saved && resource && mounted.current) setShot(resource.shot, resource.storyboard_version);
    return saved;
  }

  async function reloadShot(id: string, keepLocal = false) {
    if (shotLocks.current.has(id)) return;
    const epoch = contextEpoch.current, token = Symbol(id);
    shotLocks.current.set(id, token); setLockedShots(new Set(shotLocks.current.keys()));
    try {
      const resource = await editing.reload(id, keepLocal);
      const local = editing.get(id);
      if (resource && local && isCurrentContext(epoch)) { setShot(local.shot, local.storyboard_version); setMessage(''); }
    } catch (cause) { if (isCurrentContext(epoch)) setMessage(errorMessage(cause)); }
    finally {
      if (shotLocks.current.get(id) === token) shotLocks.current.delete(id);
      if (isCurrentContext(epoch)) setLockedShots(new Set(shotLocks.current.keys()));
    }
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
            && !editing.unsettledIds().has(id);
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
    if (!await editing.flush()) return false;
    if (mounted.current) for (const id of editing.ids()) {
      const resource = editing.get(id);
      if (resource) setShot(resource.shot, resource.storyboard_version);
    }
    for (const barrier of privateBarriers.current.values()) if (barrier.hasUnsettled() && !await barrier.flush()) return false;
    return true;
  }

  useEffect(() => {
    const barrier: NavigationBarrier = {
      hasUnsettled: () => [...privateBarriers.current.values()].some(barrier => barrier.hasUnsettled()) || !!dialogueBarrier.current?.hasUnsettled() || shotLocks.current.size > 0 || editing.hasUnsettled(),
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
    const scope = `new-shot:${projectId}`;
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
      const page = await generations.list({ service_type: 'text', project_id: projectId, source_scene: 'script_shots', offset: tasks.length, limit: 20 });
      if (!isCurrentContext(epoch)) return;
      setTasks(current => [...current, ...page.items.filter(item => !current.some(own => own.generation_id === item.generation_id))]); setTasksTotal(page.total);
    } catch (cause) { if (isCurrentContext(epoch)) setTasksError(errorMessage(cause)); }
    finally { taskMoreLock.current = false; if (isCurrentContext(epoch)) setTasksLoading(false); }
  }

  async function generateStoryboard() {
    if (submissionLock.current || busy || generationPending || readOnly || !scriptId) return;
    submissionLock.current = true;
    setSubmitting(true); setMessage('');
    try {
      if (!await writingSession.flush()) { setMessage('剧本未保存，未发起生成。'); return; }
      const latest = writingSession.getSnapshot();
      if (!latest.script.trim() || !latest.scriptId) { setMessage('先编写或采用一份剧本。'); return; }
      const body = {
        ...(value.models.storyboardText ? { config_id: value.models.storyboardText } : {}),
        ...scriptShotsRequest(projectId, latest.scriptId, latest.contentVersion, instructions, averageShotDurationMs),
      };
      const scope = `script-shots:${projectId}`;
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
    if (mode === 'replace' && !await confirmAction(`替换会移出当前 ${current.total} 个分镜，旧媒体与历史会保留。确定继续？`, { className: 'standard-creation-dialog' })) return;
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
  const selectedShotUnsettled = !!selectedShotId && unsettledIds().has(selectedShotId);
  useLayoutEffect(() => { selectShotId(subject?.type === 'shot' ? subject.id : null); }, [subject?.type, subject?.id]);
  useEffect(() => {
    if (!selectedShotId || selectedShotRef.current !== selectedShotId || selectedShotUnsettled) return;
    const loaded = getShot(selectedShotId);
    if (loaded && !storyboardRevision && !refreshToken && !detailRetry) return;
    const controller = new AbortController();
    const epoch = contextEpoch.current;
    const id = selectedShotId;
    setDetailLoading(true); setDetailError('');
    api.shot(id, controller.signal).then(result => {
      if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
      if (BigInt(result.storyboard_version) < BigInt(storyboardVersionFloor.current)) return;
      if (result.shot.deleted_at) { unavailableShot(id, result.shot); return; }
      if (!unsettledIds().has(id)) setShot(restoreShotDraft(result.shot, result.storyboard_version), result.storyboard_version);
    }).catch(cause => {
      if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
      if (cause instanceof ApiError && cause.status === 404) unavailableShot(id);
      else setDetailError(errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted && isCurrentContext(epoch) && selectedShotRef.current === id) setDetailLoading(false); });
    return () => controller.abort();
  }, [contextKey, selectedShotId, selectedShotUnsettled, storyboardRevision, refreshToken, detailRetry]);
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
  const selectionDisabled = loading || !!loadError || moreLoading || deletingShotId !== null;

  async function selectShot(id: string) {
    if (selectionDisabled) return;
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    if (editing.hasUnsettled()) void editing.flush();
    for (const barrier of privateBarriers.current.values()) if (barrier.hasUnsettled()) void barrier.flush();
    if (!isCurrentContext(epoch) || selectionEpoch.current !== selection) return;
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

  useEffect(() => {
    if (sourceMediaRequest.current === sourceMedia) return;
    sourceMediaRequest.current = sourceMedia;
    if (sourceMedia) selectEditorTab(sourceMedia);
  }, [sourceMedia]);

  function navigateEditorTabs(event: KeyboardEvent<HTMLButtonElement>) {
    const tabs = ['image', 'video'];
    const index = tabs.indexOf(editorTab);
    const next = event.key === 'Home' ? 'image' : event.key === 'End' ? 'video'
      : event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length]
        : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length] : null;
    if (next) { event.preventDefault(); selectEditorTab(next, true); }
  }

  async function mutateShot(id: string, action: 'up' | 'down') {
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
      if (action === 'up' && shot.position <= 1 || action === 'down' && shot.position >= current.total) return;
      await api.move(id, current.storyboard_version, action === 'up' ? -1 : 1);
      if (!isCurrentContext(epoch)) return;
      setStoryboardRevision(revision => revision + 1);
    } catch (cause) { if (isCurrentContext(epoch)) setMessage(errorMessage(cause)); }
    finally { if (shotMutation.current === token) shotMutation.current = null; if (isCurrentContext(epoch)) setBusy(false); }
  }

  async function deleteShot(id: string) {
    const access = currentAccess.current;
    const shot = getShot(id);
    if (!shot || shot.deleted_at || shotMutation.current || access.readOnly || access.busy || access.loading || access.loadError || shotLocks.current.size) return;
    if (dialogueBarrier.current?.hasUnsettled()) {
      setDeleteNotice({ id, type: 'warning', message: '对白尚未保存，请先保存或明确放弃对白修改，再删除分镜。' });
      return;
    }
    const epoch = contextEpoch.current, token = Symbol(id);
    shotMutation.current = token; setBusy(true); setDeletingShotId(id); setDeleteNotice(null);
    try {
      const title = shot.title || `分镜 ${String(shot.position).padStart(2, '0')}`;
      if (!await confirmAction(`删除“${title}”后无法恢复。当前输入会先保存，保存失败时不会删除。媒体与生成历史保留，已加入成片的片段保留并标记来源已删除。`, { title: '删除分镜', confirmText: '删除分镜', danger: true, className: 'standard-creation-dialog' })) return;
      if (!isCurrentContext(epoch) || currentAccess.current.readOnly) return;
      if (!await flushAll()) {
        if (isCurrentContext(epoch)) setDeleteNotice({ id, type: 'warning', message: '分镜或生成设置尚未保存，未执行删除。当前输入已保留，请先处理保存详情。' });
        return;
      }
      if (!isCurrentContext(epoch) || currentAccess.current.readOnly || shotLocks.current.size) return;
      const current = pageRef.current, saved = getShot(id);
      if (!current || !saved || saved.deleted_at) return;
      const result = await api.remove(id, { row_version: saved.row_version, storyboard_version: current.storyboard_version });
      if (!isCurrentContext(epoch)) return;
      const latest = pageRef.current ?? current;
      const remaining = latest.items.filter(item => item.id !== id);
      const wasSelected = selectedShotRef.current === id;
      const nextId = wasSelected ? (remaining.find(item => item.position > saved.position) ?? remaining.at(-1))?.id : selectedShotRef.current;
      removedShotIds.current.add(result.deleted_id);
      storyboardVersionFloor.current = BigInt(result.storyboard_version) > BigInt(latest.storyboard_version) ? result.storyboard_version : latest.storyboard_version;
      for (const savedId of editing.ids()) editing.forget(savedId);
      extraShots.current.clear(); setPage(null); setLoading(true); setLoadError('');
      batchSelection.toggle(id, false);
      if (wasSelected) { selectShotId(null); selectSubject?.(null); }
      const selection = selectionEpoch.current;
      try {
        const remote = await loadLoadedShots(api, new AbortController().signal, latest.items.length);
        if (BigInt(remote.storyboard_version) < BigInt(storyboardVersionFloor.current)) throw new Error('分镜列表仍是删除前版本，请重新加载。');
        const detail = nextId && !remote.items.some(item => item.id === nextId) ? await api.shot(nextId) : null;
        if (detail && BigInt(detail.storyboard_version) < BigInt(storyboardVersionFloor.current)) throw new Error('分镜详情仍是删除前版本，请重新加载。');
        if (!isCurrentContext(epoch)) return;
        setPage(mergeLoadedPage(remote));
        if (detail && !detail.shot.deleted_at) setShot(restoreShotDraft(detail.shot, detail.storyboard_version), detail.storyboard_version);
        if (selectionEpoch.current === selection) {
          const next = nextId ? getShot(nextId) : undefined;
          selectShotId(next?.id ?? null);
          selectSubject?.(next ? { type: 'shot', id: next.id, label: `分镜 ${String(next.position).padStart(2, '0')}`, revision: next.row_version } : null);
          requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(next ? `#storyboard-shot-${next.id} .storyboard-summary` : '.standard-storyboard-list .storyboard-heading-actions button')?.focus());
        }
      } catch (cause) {
        if (isCurrentContext(epoch)) {
          setLoadError(errorMessage(cause));
          setDeleteNotice({ id, type: 'error', message: '分镜已删除，列表尚未刷新。请重新加载列表后继续编辑。', description: errorMessage(cause) });
        }
      } finally {
        if (isCurrentContext(epoch)) setLoading(false);
      }
      window.dispatchEvent(new Event('creation-workflow-updated'));
    } catch (cause) {
      if (isCurrentContext(epoch)) setDeleteNotice({ id, type: cause instanceof ApiError && cause.status === 409 ? 'warning' : 'error', message: cause instanceof ApiError && cause.status === 409 ? '分镜版本已变化，未执行删除。当前卡片和输入已保留。' : '删除结果未确认，当前卡片和输入已保留。请重新加载列表核对。', description: errorMessage(cause) });
    } finally {
      if (shotMutation.current === token) shotMutation.current = null;
      if (isCurrentContext(epoch)) { setBusy(false); setDeletingShotId(null); }
    }
  }

  const renderShotActions = (origin: 'main' | 'history') => !readOnly && (selectedShot || batchSelection.enabled) ? <Dropdown trigger={['click']} overlayClassName="standard-creation-popup" open={actionsOrigin === origin} onOpenChange={open => setActionsOrigin(open ? origin : null)} menu={{ items: [
    ...(batchSelection.enabled ? [{ key: 'batch', label: batchMode ? '退出批量操作' : '批量操作' }, { type: 'divider' as const }] : []),
    ...(selectedShot ? [
      { key: 'up', label: '上移', disabled: selectedShot.position <= 1 },
      { key: 'down', label: '下移', disabled: !page || selectedShot.position >= page.total },
    ] : []),
  ], onClick: ({ key }) => {
    setActionsOrigin(null); setHistoryOpen(false); setCandidate(null);
    if (key === 'batch') setBatchMode(current => !current);
    else if (selectedShot) void mutateShot(selectedShot.id, key as 'up' | 'down');
  }, onKeyDown: event => {
    if (event.key !== 'Escape') return;
    event.preventDefault(); event.stopPropagation(); setActionsOrigin(null);
    requestAnimationFrame(() => actionsTrigger.current?.focus());
  } }}>
    <Button type="text" aria-label={selectedShot ? `分镜 ${String(selectedShot.position).padStart(2, '0')} 更多操作` : '分镜列表更多操作'} title="更多操作" aria-haspopup="menu" aria-expanded={actionsOrigin === origin} disabled={selectionDisabled} icon={<Ellipsis size={18}/>} onClick={event => { if (event.currentTarget instanceof HTMLButtonElement) actionsTrigger.current = event.currentTarget; }}/>
  </Dropdown> : null;

  useEffect(() => { setActionsOrigin(null); setMediaHistoryOpen(null); }, [selectedShotId, mediaTab]);
  const availableReferences = assets.filter(asset => asset.state === 'confirmed' && asset.media_id && asset.image?.url)
    .map(asset => ({ media_id: asset.media_id!, name: asset.name, url: asset.image!.url }));

  const shotInformation = selectedShot ? <div className="storyboard-shot-information">
    <label className="writing-control"><span>镜头名称</span><Input aria-label="镜头名称" maxLength={255} value={selectedShot.title ?? ''} disabled={readOnly || !!shotMutation.current || lockedShots.has(selectedShot.id)} onChange={event => updateLocal(selectedShot.id, { title: event.target.value })}/></label>
    <label className="writing-control"><span>镜头脚本</span><Input.TextArea aria-label={'分镜 ' + selectedShot.position + ' 脚本'} rows={4} value={selectedShot.script} disabled={readOnly || !!shotMutation.current || lockedShots.has(selectedShot.id)} onChange={event => updateLocal(selectedShot.id, { script: event.target.value })}/></label>
    {editing.hasPrevious(selectedShot.id) && <Button type="text" onClick={() => downloadShotRecovery(selectedShot.id)}>下载本机备份</Button>}
    {['error', 'conflict'].includes(editing.status(selectedShot.id) ?? '') && <Alert className="storyboard-shot-feedback" type="warning" message="镜头保存已暂停，输入保留。" action={<>
      <Button onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify(selectedShot, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = `shot-${selectedShot.id}-draft.json`; link.click(); URL.revokeObjectURL(url); }}>下载镜头草稿</Button>
      {editing.status(selectedShot.id) === 'error' && <Button onClick={() => void editing.retry()}>重试保存镜头</Button>}
      {editing.status(selectedShot.id) === 'conflict' && <Button loading={lockedShots.has(selectedShot.id)} onClick={() => void reloadShot(selectedShot.id, true)}>以当前版本保留本机修改</Button>}
      <Button loading={lockedShots.has(selectedShot.id)} onClick={() => void reloadShot(selectedShot.id)}>载入服务端镜头</Button>
    </>}/>}
  </div> : null;
  const visibleShots = page?.items.filter(shot => (!search || (shot.title ?? '').includes(search)) && (!pendingOnly || (mediaTab === 'video' ? !shot.video : !shot.image))) ?? [];
  useEffect(() => {
    if (!initialSelectionPending.current || loading || loadError || !page || selectedShotRef.current || subject?.type === 'shot') return;
    const recovered = [...editing.unsettledIds()].map(id => getShot(id)).find(shot => shot && !shot.deleted_at);
    const first = recovered ?? visibleShots[0];
    if (!first) return;
    selectShotId(first.id);
    selectSubject?.({ type: 'shot', id: first.id, label: `分镜 ${String(first.position).padStart(2, '0')}`, revision: first.row_version });
  }, [page, loading, loadError, selectedShotId, subject?.type, subject?.id, search, pendingOnly, mediaTab, editingSnapshot.revision, selectSubject]);
  return <div className="storyboard-workspace">
    {deleteNotice && <Alert type={deleteNotice.type} title="分镜删除" noticeKey={`shot:${deleteNotice.id}:delete`} message={deleteNotice.message} description={deleteNotice.description} actions={<Button loading={loading} onClick={() => { setDeleteNotice(null); setStoryboardRevision(revision => revision + 1); }}>重新加载列表核对</Button>}/>}
    {message && !settingsOpen && <Alert type={feedbackType(message)} message={message} action={unavailableDraftId ? <Button onClick={downloadShotDraft}>下载分镜草稿</Button> : undefined}/>}
    {detailError && <Alert type="error" showIcon message={'分镜详情加载失败：' + detailError} action={<Button loading={detailLoading} onClick={() => setDetailRetry(revision => revision + 1)}>重新加载分镜详情</Button>}/>}
    {assetsError && <Alert type="error" showIcon message={'关联素材加载失败：' + assetsError} action={<Button loading={assetsLoading} onClick={() => setAssetsRevision(revision => revision + 1)}>重新加载关联素材</Button>}/>}
    {loadError && <Alert type="error" showIcon message={'分镜列表加载失败：' + loadError} action={<Button loading={loading} onClick={() => setStoryboardRevision(revision => revision + 1)}>重新加载分镜列表</Button>}/>}
    <EpisodeEditorSlot stage="storyboard">
      <div className="standard-storyboard-panel">
        <header className="standard-object-heading">
          <h3>{selectedShot ? `${String(selectedShot.position).padStart(2, '0')} · ${selectedShot.title || '未命名镜头'}` : '分镜制作'}</h3>
          <div className="storyboard-media-switch" role="tablist" aria-label="镜头制作内容">
            {([{ key: 'image', label: '分镜图' }, { key: 'video', label: '分镜视频' }]).map(tab => <button key={tab.key} type="button" role="tab" id={'storyboard-' + tab.key + '-tab'} aria-selected={editorTab === tab.key} aria-controls={'storyboard-' + tab.key + '-pane'} tabIndex={editorTab === tab.key ? 0 : -1} disabled={!!selectedShot && lockedShots.has(selectedShot.id)} onClick={() => selectEditorTab(tab.key)} onKeyDown={navigateEditorTabs}>{tab.label}</button>)}
          </div>
          <nav aria-label="切换编辑镜头">
            <Button type="text" aria-label="上一个分镜" disabled={!selectedShot || selectionDisabled || selectedShot.position <= 1} onClick={() => void selectAdjacent(-1)} icon={<Icon name="back" size={16}/>}/>
            <Button type="text" aria-label="下一个分镜" loading={moreLoading} disabled={!selectedShot || selectionDisabled || !page || selectedShot.position >= page.total} onClick={() => void selectAdjacent(1)} icon={<Icon name="arrow" size={16}/>}/>
            <Button type="text" aria-label={mediaTab === 'video' ? '视频生成记录' : '图片生成记录'} title="生成记录" disabled={!selectedShot} onClick={() => setMediaHistoryOpen(mediaTab === 'video' ? 'video' : 'image')} icon={<History size={16}/>}/>
            {renderShotActions('main')}
            <Button type="text" aria-label={mediaSettingsOpen ? '收起生成设置' : '展开生成设置'} aria-expanded={mediaSettingsOpen} aria-controls={mediaTab === 'video' ? 'storyboard-video-settings' : 'storyboard-image-settings'} onClick={() => { if (!mediaSettingsOpen) revealPanel?.(); setMediaSettingsOpen(value => !value); }} icon={<PanelRight size={16}/>}/>
          </nav>
        </header>
        {selectedShot ? <div className={`standard-storyboard-media${mediaSettingsOpen ? '' : ' is-settings-collapsed'}`}>
          <div id="storyboard-image-pane" role="tabpanel" aria-labelledby="storyboard-image-tab" hidden={mediaTab !== 'image'}>
            <ShotImageCandidates key={selectedShot.id} projectId={projectId} information={shotInformation} settingsOpen={mediaSettingsOpen} active={mediaTab === 'image'} onCloseSettings={() => setMediaSettingsOpen(false)} onOpenSettings={() => { revealPanel?.(); setMediaSettingsOpen(true); }}
              historyOpen={mediaHistoryOpen === 'image'} onHistoryOpenChange={open => setMediaHistoryOpen(open ? 'image' : null)}
              availableReferences={availableReferences} availableLoading={assetsLoading} availableError={assetsError} onRefreshAvailable={() => setAssetsRevision(revision => revision + 1)}
              registerBarrier={registerImageDraft} modelSelection={value.models.storyboardImage} onModelChange={id => onChange({ ...value, models: { ...value.models, storyboardImage: id } })} onResolvedModel={onResolvedImageModel}
              shot={selectedShot} disabled={readOnly || busy || lockedShots.has(selectedShot.id) || ['error', 'conflict'].includes(editing.status(selectedShot.id) ?? '')} modelId={imageModelId} capabilities={imageCapabilities} capabilitiesLoading={capabilitiesLoading}
              onRefreshCapabilities={refreshCapabilities} prepareShot={purpose => prepareShot(selectedShot.id, purpose)} onChanged={() => setStoryboardRevision(revision => revision + 1)}/>
          </div>
          <div id="storyboard-video-pane" role="tabpanel" aria-labelledby="storyboard-video-tab" hidden={mediaTab !== 'video'}>
            <ShotVideoCandidates key={selectedShot.id} projectId={projectId} settingsOpen={mediaSettingsOpen} active={mediaTab === 'video'} onCloseSettings={() => setMediaSettingsOpen(false)} onOpenSettings={() => { revealPanel?.(); setMediaSettingsOpen(true); }}
              historyOpen={mediaHistoryOpen === 'video'} onHistoryOpenChange={open => setMediaHistoryOpen(open ? 'video' : null)}
              information={<>{shotInformation}<details className="storyboard-dialogue-settings"><summary>对白与音色</summary><NativeDialoguePanel key={selectedShot.id} revision={storyboardRevision} registerBarrier={registerDialogueBarrier} projectId={projectId} shotId={selectedShot.id} disabled={readOnly || busy || lockedShots.has(selectedShot.id)} prepare={() => saveShot(selectedShot.id)} onChanged={() => setStoryboardRevision(revision => revision + 1)}/></details></>}
              registerBarrier={registerVideoDraft} shot={selectedShot} disabled={readOnly || busy || lockedShots.has(selectedShot.id) || ['error', 'conflict'].includes(editing.status(selectedShot.id) ?? '')} model={value.models.video}
              onModelChange={id => onChange({ ...value, models: { ...value.models, video: id } })} prepareShot={() => prepareShot(selectedShot.id, 'adoption')} onChanged={() => setStoryboardRevision(revision => revision + 1)}/>
          </div>
        </div> : <div className="studio-empty">{detailLoading ? '正在载入镜头…' : '选择左侧镜头开始制作'}</div>}
      </div>
    </EpisodeEditorSlot>
    <section className="standard-storyboard-list" aria-label="分镜创作区域">
      <header className="storyboard-editor-heading"><h3>本集镜头 <Button className="storyboard-list-count" type="text" aria-label="分镜生成记录" title="分镜提取记录" onClick={() => { setHistoryOpen(true); setCandidate(null); setTaskRevision(revision => revision + 1); }}>{page?.total ?? 0}</Button></h3><div className="storyboard-heading-actions">
        <Button type="text" aria-label="新增分镜" title="新增分镜" disabled={readOnly || busy || loading || !!loadError || !page} onClick={() => { setSearch(''); setPendingOnly(false); void add(); }} icon={<Icon name="plus" size={16}/>}/>
        <Button type="text" aria-label="提取分镜" title="提取分镜" disabled={readOnly || busy || submitting} onClick={() => setSettingsOpen(true)} icon={<Sparkles size={16}/>}/>
      </div></header>
      <div className="standard-storyboard-filters"><Input prefix={<Icon name="search" size={14}/>} aria-label="搜索镜头名称" value={search} placeholder="搜索镜头名称" allowClear onChange={event => setSearch(event.target.value)}/>
        <div className="storyboard-category-tabs" aria-label="镜头筛选"><button type="button" aria-pressed={!pendingOnly} onClick={() => setPendingOnly(false)}>全部镜头</button><button type="button" aria-pressed={pendingOnly} onClick={() => setPendingOnly(true)}>待制作</button></div>
      </div>
      {batchSelection.enabled && !readOnly && batchMode && <Button type="text" disabled={selectionDisabled} onClick={() => setBatchMode(false)}>退出批量操作</Button>}
      {!readOnly && batchMode && <BatchLauncher presentation="standard-creation" scope={{ library: 'project', project_id: projectId }} selection={{ ...batchSelection, enabled: batchSelection.enabled && batchMode }} loadedIds={page?.items.map(shot => shot.id) ?? []} disabled={busy || loading || !!loadError || lockedShots.size > 0} beforePreflight={flushAll}/>}
      <div className="storyboard-list lazy-scroll" aria-busy={loading}>
        {loading && !page ? Array.from({ length: 3 }, (_, index) => <div className="storyboard-card-skeleton" key={index} role={index === 0 ? 'status' : undefined} aria-label={index === 0 ? '正在加载分镜' : undefined}><span/><div><i/><i/></div></div>) : <>
          {page && !page.items.length && !loadError && <div className="studio-empty"><p>还没有镜头</p><Button disabled={readOnly || busy || loading} onClick={() => void add()}>新增第一个分镜</Button></div>}
          {!!page?.items.length && !visibleShots.length && !loadError && <div className="studio-empty storyboard-filter-empty" role="status"><p>已加载镜头中没有匹配项</p><Button type="text" onClick={() => { setSearch(''); setPendingOnly(false); }}>清空筛选</Button></div>}
          {visibleShots.map(shot => <StoryboardShotCard key={shot.id} shot={shot} media={mediaTab === 'video' ? 'video' : 'image'} selected={selectedShotId === shot.id} disabled={selectionDisabled} batch={batchSelection.enabled && batchMode && !readOnly} checked={batchSelection.ids.includes(shot.id)} onSelect={() => void selectShot(shot.id)} onCheck={checked => batchSelection.toggle(shot.id, checked)} onDelete={readOnly ? undefined : () => void deleteShot(shot.id)} deleting={deletingShotId === shot.id} deleteDisabled={selectionDisabled || busy || lockedShots.size > 0}/>)}
          <LazyLoadMore hasMore={!!page && page.items.length < page.total} loading={loading || moreLoading} error={moreError} onLoad={() => void loadMoreShots()}/>
        </>}
      </div>
    </section>    {settingsOpen && <Dialog title="提取分镜" className="storyboard-settings-dialog standard-creation-dialog" canClose={!submitting} onClose={() => setSettingsOpen(false)}>
      <div className="storyboard-settings-body">
        <p className="episode-help">从当前剧本生成分镜候选，完成后在历史记录中预览并采用。</p>
        <label className="writing-control"><span>文本模型</span><EpisodeModelSelect presentation="standard-creation" optionLayout="compact" kind="text" label="分镜模型" value={value.models.storyboardText} disabled={readOnly || submitting} onChange={id => onChange({ ...value, models: { ...value.models, storyboardText: id } })}/></label>
        <div className="writing-control"><span>平均镜头时长</span><Segmented block aria-label="平均镜头时长预设" value={durationMode} disabled={readOnly || submitting} options={[{ label: '2 秒', value: '2000' }, { label: '3 秒', value: '3000' }, { label: '5 秒', value: '5000' }, { label: '自定', value: 'custom' }]} onChange={next => { const mode = String(next); setDurationMode(mode); if (mode !== 'custom') setAverageShotDurationMs(Number(mode)); }}/>
          {durationMode === 'custom' && <InputNumber aria-label="自定义平均镜头时长" min={1} max={10} step={0.5} precision={1} value={averageShotDurationMs / 1000} addonAfter="秒" disabled={readOnly || submitting} onChange={seconds => { if (seconds !== null) setAverageShotDurationMs(Math.round(seconds * 1000)); }}/>}</div>
        <label className="writing-control"><span>分镜要求描述</span><Input.TextArea rows={4} maxLength={4000} value={instructions} onChange={event => setInstructions(event.target.value)} aria-label="分镜要求描述" disabled={readOnly || submitting} placeholder="例如：更多近景，突出人物情绪"/></label>
        {!writingSession.getSnapshot().script.trim() && <Alert quiet persistent type="warning" message="先编写或采用一份剧本。"/>}
        {message && <Alert type={feedbackType(message)} message={message}/>}
      </div>
      <footer className="storyboard-settings-footer"><Button disabled={submitting} onClick={() => setSettingsOpen(false)}>关闭</Button><Button type="primary" aria-label="生成分镜" aria-busy={submitting || generationPending} loading={submitting || generationPending} disabled={readOnly || busy || generationPending || !writingSession.getSnapshot().script.trim() || !scriptId} onClick={() => void generateStoryboard()}>生成分镜</Button></footer>
    </Dialog>}
    {historyOpen && <Dialog title="分镜生成记录" className="storyboard-history-dialog standard-creation-dialog" canClose={!busy} onClose={() => { setHistoryOpen(false); setCandidate(null); }}>
      {candidate ? <><Button type="link" onClick={() => setCandidate(null)}>返回生成记录</Button><StoryboardResultPreview key={candidate.generation_id} projectId={projectId} generationId={candidate.generation_id} busy={busy} disabled={readOnly || loading || !!loadError || !page} error={message} onApply={mode => void applyResult(mode)}/></> : <div className="lazy-scroll storyboard-history-scroll">
        {tasksLoading && !tasks.length && <p role="status" className="episode-help">正在加载分镜生成记录…</p>}
        {tasksError && <Alert type="error" showIcon message={'分镜生成记录加载失败：' + tasksError} action={<Button loading={tasksLoading} onClick={() => setTaskRevision(revision => revision + 1)}>重新加载生成记录</Button>}/>}
        {tasksLoaded && !tasksLoading && !tasksError && !tasks.length && <p className="episode-help">暂无分镜生成记录，编写或采用剧本后开始生成。</p>}
        {tasks.map(task => <div key={task.generation_id} className="resource-import-row"><div><strong>{taskLabel(task)}</strong><p>{new Date(task.created_at).toLocaleString('zh-CN')}</p><small>任务 {task.generation_id}</small>{task.error && <p role="alert">{task.error.message}</p>}</div>{task.status === 'succeeded' ? <Button onClick={() => { setMessage(''); setCandidate(task); }}>查看分镜</Button> : <Button onClick={() => setTaskDetailId(task.generation_id)}>查看任务</Button>}</div>)}
        <LazyLoadMore hasMore={tasks.length < tasksTotal} loading={tasksLoading} error={tasksError} onLoad={() => void loadMoreTasks()}/>
      </div>}
      <div className="production-dialog-footer">{renderShotActions('history')}<Button disabled={busy} onClick={() => { setActionsOrigin(null); setHistoryOpen(false); setCandidate(null); }}>关闭</Button></div>
    </Dialog>}
    {taskDetailId && <TaskDetail presentation="standard-creation" id={taskDetailId} onClose={() => setTaskDetailId(null)} onChanged={() => setTaskRevision(revision => revision + 1)} onCreated={task => { setTaskDetailId(task.generation_id); setTaskRevision(revision => revision + 1); }}/ >}
  </div>;
}
