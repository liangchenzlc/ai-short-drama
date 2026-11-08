import { storyboardApi } from '@host/api/creation/storyboard/shots';
import { generations } from '@host/api/generations/tasks/requests';
import { errorMessage } from '@host/api/http';
import { ResourceCollection } from '@host/features/creation/shared/resource-view/resourceCollection';
import { ResourceViewToggle, useResourceView } from '@host/features/creation/shared/resource-view/ResourceViewToggle';
import { flushStandardProjectSessions } from '@host/features/creation/shared/sessions/registry';
import { NativeDialoguePanel } from '@host/features/creation/sound/voices/NativeVoicePanel';
import { createShotMutations } from '@host/features/creation/storyboard/editor/shot-mutations';
import { ShotInformation } from '@host/features/creation/storyboard/editor/ShotInformation';
import { StoryboardHistory } from '@host/features/creation/storyboard/history/StoryboardHistory';
import { useStoryboardTasks } from '@host/features/creation/storyboard/history/useStoryboardTasks';
import { prepareShotOperation } from '@host/features/creation/storyboard/images/shot-image-workflow';
import { ShotImageCandidates } from '@host/features/creation/storyboard/images/ShotImageCandidates';
import { useShotImageCapabilities } from '@host/features/creation/storyboard/images/useShotImageCapabilities';
import '@host/features/creation/storyboard/list/index.css';
import { StoryboardShotList } from '@host/features/creation/storyboard/list/StoryboardShotList';
import { useStoryboardAssets } from '@host/features/creation/storyboard/references/useStoryboardAssets';
import { useStoryboardCollection } from '@host/features/creation/storyboard/sessions/useStoryboardCollection';
import { ShotVideoCandidates } from '@host/features/creation/storyboard/videos/ShotVideoCandidates';
import { StoryboardEditorHeader } from '@host/features/creation/storyboard/workbench/views/StoryboardEditorHeader';
import { StoryboardShotActions } from '@host/features/creation/storyboard/workbench/views/StoryboardShotActions';
import { CreationEditorSlot, useCreationControls } from '@host/features/creation/workspace/layout/CreationWorkspace';
import '@host/features/creation/workspace/layout/refinement.css';
import { BatchLauncher, useBatchSelection } from '@host/features/generations/batches/BatchGeneration';
import { useCollectionBatchProgress } from '@host/features/generations/batches/useCollectionBatchProgress';
import { TaskDetail } from '@host/features/generations/tasks/TaskDetail';
import { useAuth } from '@host/features/identity/session/AuthSession';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { confirmAction } from '@host/shared/ui/confirm';
import { Icon } from '@host/shared/ui/Icon';
import { StudioFeedback as Alert, feedbackType } from '@host/shared/ui/StudioFeedback';
import type { ShotDeleteNotice } from '@host/types/creation/storyboard/editor/mutations';
import type { PreparedShot } from '@host/types/creation/storyboard/images/shot-image-workflow';
import type { ShotRead } from '@host/types/creation/storyboard/shots';
import type { DraftVideoReference } from '@host/types/creation/drafts/requests';
import type { ResourceView } from '@host/types/creation/shared/resource-view/ResourceViewToggle';
import type { StoryboardWorkspaceProps } from '@host/types/creation/storyboard/workbench';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import type { GenerationSummary } from '@host/types/generations/api';
import { Button, Checkbox, Input, Tooltip } from 'antd';
import { History } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import './storyboardView.css';

/** 保存、切镜头、采用与离页共享版本围栏和锁；子视图只接收事件，不另建编辑状态。 */
export function StoryboardWorkspace({
  value,
  readOnly,
  onChange,
  projectId,
  scriptId,
  writingSession,
  registerBarrier,
  refreshToken = 0,
  sourceMedia
}: StoryboardWorkspaceProps) {
  const api = storyboardApi(projectId);
  const {
    assets,
    setAssets,
    assetsLoading,
    setAssetsLoading,
    assetsError,
    setAssetsError,
    setAssetsRevision
  } =
    useStoryboardAssets(projectId, refreshToken);
  const {
    tasks,
    setTasks,
    tasksTotal,
    setTasksTotal,
    tasksLoading,
    setTasksLoading,
    tasksLoaded,
    setTasksLoaded,
    tasksError,
    setTasksError,
    setTaskRevision
  } = useStoryboardTasks(projectId, scriptId);
  const { user } = useAuth();
  const account = user?.id ?? 'anonymous';
  const [view, setView] = useResourceView('storyboard');
  const [collectionMode, setCollectionMode] = useState<ResourceView>('single');
  const [collectionShots, setCollectionShots] = useState<ShotRead[]>([]);
  const [collectionLoading, setCollectionLoading] = useState(true);
  const [collectionError, setCollectionError] = useState('');
  const { revealPanel, subject, selectSubject } = useCreationControls();
  const batchSelection = useBatchSelection(`${projectId}`);
  const [visibleCollectionIds, setVisibleCollectionIds] = useState<string[]>([]);
  const { statuses: batchProgress, candidates: batchCandidates, onCandidateState } = useCollectionBatchProgress(projectId, account, 'shot_image', visibleCollectionIds);
  const [candidate, setCandidate] = useState<GenerationSummary | null>(null);
  const [taskDetailId, setTaskDetailId] = useState<string | null>(null);
  const shotMutation = useRef<symbol | null>(null);
  const [busy, setBusy] = useState(false);
  const [deletingShotId, setDeletingShotId] = useState<string | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<ShotDeleteNotice | null>(null);
  const [message, setMessage] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [actionsOrigin, setActionsOrigin] = useState<'main' | 'history' | null>(null);
  const actionsTrigger = useRef<HTMLButtonElement | null>(null);
  const [mediaHistoryOpen, setMediaHistoryOpen] = useState<'image' | 'video' | null>(null);
  const [mediaTab, setMediaTab] = useState<string>(sourceMedia ?? 'image');
  const [search, setSearch] = useState('');
  const [pendingOnly, setPendingOnly] = useState(false);
  const privateBarriers = useRef(new Map<string, NavigationBarrier>());
  const registerImageDraft = useCallback((barrier: NavigationBarrier | null) => {
    if (barrier) privateBarriers.current.set('image', barrier);
    else privateBarriers.current.delete('image');
  }, []);
  const registerVideoDraft = useCallback((barrier: NavigationBarrier | null) => {
    if (barrier) privateBarriers.current.set('video', barrier);
    else privateBarriers.current.delete('video');
  }, []);
  const [batchMode, setBatchMode] = useState(false);
  const [videoReferenceRequest, setVideoReferenceRequest] = useState<{
    shotId: string; reference: DraftVideoReference; sequence: number;
  } | null>(null);
  const handledVideoReference = useRef(0);
  const taskMoreLock = useRef(false);
  const dialogueBarrier = useRef<NavigationBarrier | null>(null);
  const registerDialogueBarrier = useCallback((barrier: NavigationBarrier | null) => {
    dialogueBarrier.current = barrier;
  }, []);
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

  const {
    editing,
    editingSnapshot,
    page,
    pageRef,
    extraShots,
    removedShotIds,
    storyboardVersionFloor,
    detailLoading,
    detailError,
    setDetailRetry,
    unavailableDraftId,
    loading,
    setLoading,
    loadError,
    setLoadError,
    selectedShotId,
    selectedShotRef,
    initialSelectionPending,
    selectionEpoch,
    locateShot,
    moreLoading,
    moreError,
    storyboardRevision,
    setStoryboardRevision,
    setPage,
    getShot,
    setShot,
    selectShotId,
    mergeLoadedPage,
    restoreShotDraft,
    downloadShotDraft,
    downloadShotRecovery,
    loadMoreShots
  } = useStoryboardCollection({
    projectId,
    account,
    refreshToken,
    subject,
    selectSubject,
    shotLocks,
    context: { epoch: contextEpoch, isCurrent: isCurrentContext },
    setMessage,
  });
  const currentAccess = useRef({ readOnly, busy, loading, loadError });
  const { imageModelId, imageCapabilities, capabilitiesLoading, onResolvedImageModel, refreshCapabilities } =
    useShotImageCapabilities(value.models.storyboardImage, preparationEpoch);

  useEffect(() => {
    const controller = new AbortController();
    async function loadCollection() {
      setCollectionLoading(true);
      setCollectionError('');
      try {
        const items: ShotRead[] = [];
        let total = 0;
        do {
          const result = await storyboardApi(projectId).shots(controller.signal, false, items.length, 100);
          items.push(...result.items);
          total = result.total;
          if (!result.items.length) break;
        } while (items.length < total);
        if (!controller.signal.aborted) setCollectionShots(items);
      } catch (cause) {
        if (!controller.signal.aborted) setCollectionError(errorMessage(cause));
      } finally {
        if (!controller.signal.aborted) setCollectionLoading(false);
      }
    }
    void loadCollection();
    return () => controller.abort();
  }, [projectId, storyboardRevision, refreshToken, page?.storyboard_version]);

  useLayoutEffect(() => {
    mounted.current = true;
    currentContext.current = contextKey;
    setDeletingShotId(null);
    setDeleteNotice(null);
    setAssets([]);
    setAssetsLoading(true);
    setAssetsError('');
    setTasks([]);
    setTasksTotal(0);
    setTasksLoading(true);
    setTasksLoaded(false);
    setTasksError('');
    setCandidate(null);
    setMessage('');
    setBusy(false);
    setLockedShots(new Set());
    setTaskDetailId(null);
    shotMutation.current = null;
    setMediaTab(sourceMedia ?? 'image');
    setBatchMode(false);
    setVideoReferenceRequest(null);
    handledVideoReference.current = 0;
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

  function updateLocal(
    id: string,
    patch: Partial<
      Pick<
        ShotRead,
        'title' | 'shot_size' | 'script' | 'details' | 'duration_ms' | 'asset_ids' | 'image_settings' | 'video_prompt' | 'video_settings'
      >
    >,
  ) {
    const shot = getShot(id);
    if (
      !shot ||
      !isCurrentContext(contextEpoch.current) ||
      currentAccess.current.readOnly ||
      shotMutation.current ||
      shotLocks.current.has(id) ||
      shot.deleted_at
    )
      return;
    const next = { ...shot, ...patch };
    editing.ensure(shot, pageRef.current?.storyboard_version);
    editing.edit(next);
    setShot(next);
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
    const epoch = contextEpoch.current,
      token = Symbol(id);
    shotLocks.current.set(id, token);
    setLockedShots(new Set(shotLocks.current.keys()));
    try {
      const resource = await editing.reload(id, keepLocal);
      const local = editing.get(id);
      if (resource && local && isCurrentContext(epoch)) {
        setShot(local.shot, local.storyboard_version);
        setMessage('');
      }
    } catch (cause) {
      if (isCurrentContext(epoch)) setMessage(errorMessage(cause));
    } finally {
      if (shotLocks.current.get(id) === token) shotLocks.current.delete(id);
      if (isCurrentContext(epoch)) setLockedShots(new Set(shotLocks.current.keys()));
    }
  }

  async function prepareShot(
    id: string,
    purpose: 'generation' | 'adoption' = 'generation',
  ): Promise<PreparedShot | null> {
    const epoch = contextEpoch.current;
    // Adopting an existing image does not depend on the next generation model.
    // A native confirmation can restore window focus and refresh capabilities.
    const operationEpoch = purpose === 'adoption' ? adoptionEpoch : preparationEpoch;
    const preparation = operationEpoch.current;
    const access = currentAccess.current;
    const shot = getShot(id);
    if (
      !isCurrentContext(epoch) ||
      access.readOnly ||
      access.busy ||
      access.loading ||
      access.loadError ||
      !shot ||
      shot.deleted_at ||
      shotLocks.current.has(id)
    )
      return null;
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
          const valid =
            operationEpoch.current === preparation &&
            !currentAccess.current.readOnly &&
            !!current &&
            !current.deleted_at &&
            !editing.unsettledIds().has(id);
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
    if (!(await editing.flush())) return false;
    if (mounted.current)
      for (const id of editing.ids()) {
        const resource = editing.get(id);
        if (resource) setShot(resource.shot, resource.storyboard_version);
      }
    for (const barrier of privateBarriers.current.values())
      if (barrier.hasUnsettled() && !(await barrier.flush())) return false;
    return true;
  }

  useEffect(() => {
    const barrier: NavigationBarrier = {
      hasUnsettled: () =>
        [...privateBarriers.current.values()].some((barrier) => barrier.hasUnsettled()) ||
        !!dialogueBarrier.current?.hasUnsettled() ||
        shotLocks.current.size > 0 ||
        editing.hasUnsettled(),
      flush: async () =>
        !dialogueBarrier.current?.hasUnsettled() &&
        shotLocks.current.size === 0 &&
        (await flushAll()) &&
        shotLocks.current.size === 0,
    };
    registerBarrier(barrier);
    return () => registerBarrier(null);
  });

  async function add() {
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    const current = pageRef.current;
    if (!current || readOnly || busy || shotLocks.current.size || !(await flushAll()) || shotLocks.current.size) return;
    setBusy(true);
    setMessage('');
    const scope = `new-shot:${projectId}`;
    try {
      const body = { storyboard_version: pageRef.current!.storyboard_version };
      const idempotencyKey = await requestAttempt(scope, body, attemptStorage());
      const result = await api.create(body.storyboard_version, idempotencyKey);
      clearAttempt(scope, attemptStorage());
      if (!isCurrentContext(epoch)) return;
      setShot(result.shot, result.storyboard_version);
      if (selectionEpoch.current === selection) {
        selectShotId(result.shot.id);
        selectSubject?.({
          type: 'shot',
          id: result.shot.id,
          label: `分镜 ${String(result.shot.position).padStart(2, '0')}`,
        });
        revealPanel?.();
        locateShot.current = result.shot.id;
      }
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) {
      if (isCurrentContext(epoch)) setMessage(errorMessage(cause));
    } finally {
      if (isCurrentContext(epoch)) setBusy(false);
    }
  }

  async function loadMoreTasks() {
    if (taskMoreLock.current || tasks.length >= tasksTotal) return;
    const epoch = contextEpoch.current;
    taskMoreLock.current = true;
    setTasksLoading(true);
    setTasksError('');
    try {
      const page = await generations.list({
        service_type: 'text',
        project_id: projectId,
        source_scene: 'script_shots',
        offset: tasks.length,
        limit: 20,
      });
      if (!isCurrentContext(epoch)) return;
      setTasks((current) => [
        ...current,
        ...page.items.filter((item) => !current.some((own) => own.generation_id === item.generation_id)),
      ]);
      setTasksTotal(page.total);
    } catch (cause) {
      if (isCurrentContext(epoch)) setTasksError(errorMessage(cause));
    } finally {
      taskMoreLock.current = false;
      if (isCurrentContext(epoch)) setTasksLoading(false);
    }
  }

  async function applyResult(mode: 'append' | 'replace') {
    const epoch = contextEpoch.current;
    const selection = selectionEpoch.current;
    if (readOnly || loading || loadError || !pageRef.current) {
      setMessage('分镜列表尚未就绪，请先重新加载分镜列表后再采用。');
      return;
    }
    if (!candidate || busy || shotLocks.current.size || !(await flushAll()) || shotLocks.current.size) return;
    const current = pageRef.current;
    if (!current) return;
    if (
      mode === 'replace' &&
      !(await confirmAction(`替换会移出当前 ${current.total} 个分镜，旧媒体与历史会保留。确定继续？`, {
        className: 'standard-creation-dialog',
      }))
    )
      return;
    setBusy(true);
    setMessage('');
    try {
      await api.apply(candidate.generation_id, {
        mode,
        content_version: writingSession.getSnapshot().contentVersion,
        storyboard_version: current.storyboard_version,
        confirm_replace: mode === 'replace',
      });
      if (!isCurrentContext(epoch)) return;
      setCandidate(null);
      setHistoryOpen(false);
      if (mode === 'replace' && selectionEpoch.current === selection) {
        selectShotId(null);
        selectSubject?.(null);
      }
      setStoryboardRevision((revision) => revision + 1);
      setTaskRevision((revision) => revision + 1);
    } catch (cause) {
      if (isCurrentContext(epoch)) setMessage(errorMessage(cause));
    } finally {
      if (isCurrentContext(epoch)) setBusy(false);
    }
  }

  const selectedShot = selectedShotId ? getShot(selectedShotId) : undefined;
  useEffect(() => {
    if (selectedShot && subject?.id === selectedShot.id)
      selectSubject?.({
        type: 'shot',
        id: selectedShot.id,
        label: `分镜 ${String(selectedShot.position).padStart(2, '0')}`,
        revision: selectedShot.row_version,
      });
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
      if (shot)
        selectSubject?.({
          type: 'shot',
          id,
          label: `分镜 ${String(shot.position).padStart(2, '0')}`,
          revision: shot.row_version,
        });
      revealPanel?.();
    });
  }

  useEffect(() => {
    if (sourceMedia) setMediaTab(sourceMedia);
  }, [sourceMedia]);
  const { mutateShot, deleteShot } = createShotMutations({
    api,
    collection: {
      pageRef,
      getShot,
      setPage,
      setShot,
      mergeLoadedPage,
      restoreShotDraft,
      removedShotIds,
      storyboardVersionFloor,
      extraShots,
      editing,
    },
    selection: { selectedShotRef, selectionEpoch, selectShotId, selectSubject, toggleBatch: batchSelection.toggle },
    lifecycle: { contextEpoch, isCurrentContext, currentAccess },
    locks: { shotMutation, shotLocks, dialogueBarrier },
    state: { setBusy, setMessage, setDeletingShotId, setDeleteNotice, setLoading, setLoadError, setStoryboardRevision },
    flushAll,
  });

  const renderShotActions = (origin: 'main' | 'history') => (
    <StoryboardShotActions
      readOnly={readOnly}
      selectedShot={selectedShot}
      batchEnabled={batchSelection.enabled}
      batchMode={batchMode}
      total={page?.total ?? null}
      open={actionsOrigin === origin}
      selectionDisabled={selectionDisabled}
      triggerRef={actionsTrigger}
      onOpenChange={(open) => setActionsOrigin(open ? origin : null)}
      onAction={(action) => {
        setActionsOrigin(null);
        setHistoryOpen(false);
        setCandidate(null);
        if (action === 'batch') setBatchMode((current) => !current);
        else if (selectedShot) void mutateShot(selectedShot.id, action);
      }}
    />
  );

  useEffect(() => {
    setActionsOrigin(null);
    setMediaHistoryOpen(null);
  }, [selectedShotId, mediaTab]);
  const availableReferences = assets
    .filter((asset) => asset.state === 'confirmed' && asset.media_id && asset.image?.url)
    .map((asset) => ({
      media_id: asset.media_id!,
      name: asset.name,
      url: asset.image!.url,
    }));
  const shotInformation = selectedShot ? (
    <ShotInformation
      shot={selectedShot}
      disabled={readOnly || !!shotMutation.current || lockedShots.has(selectedShot.id)}
      reloading={lockedShots.has(selectedShot.id)}
      hasPrevious={editing.hasPrevious(selectedShot.id)}
      status={editing.status(selectedShot.id)}
      onChange={(patch) => updateLocal(selectedShot.id, patch)}
      onDownloadRecovery={() => downloadShotRecovery(selectedShot.id)}
      onRetry={() => editing.retry()}
      onReload={(keep) => reloadShot(selectedShot.id, keep)}
    />
  ) : null;
  const visibleShots =
    page?.items.filter(
      (shot) =>
        (!search || (shot.title ?? '').includes(search)) &&
        (!pendingOnly || (mediaTab === 'video' ? !shot.video : !shot.image)),
    ) ?? [];
  const collectionVisible = collectionShots.filter((shot) => !removedShotIds.current.has(shot.id))
    .map((shot) => getShot(shot.id) ?? shot).filter((shot) =>
    (!search || (shot.title ?? '').includes(search)) &&
    (!pendingOnly || (mediaTab === 'video' ? !shot.video : !shot.image)),
  );
  const collectionIds = collectionVisible.map((shot) => shot.id);
  const selectedCollectionIds = batchSelection.ids.filter((id) => collectionIds.includes(id));
  useEffect(() => {
    if (
      !initialSelectionPending.current ||
      loading ||
      loadError ||
      !page ||
      selectedShotRef.current ||
      subject?.type === 'shot'
    )
      return;
    const recovered = [...editing.unsettledIds()].map((id) => getShot(id)).find((shot) => shot && !shot.deleted_at);
    const first = recovered ?? visibleShots[0];
    if (!first) return;
    selectShotId(first.id);
    selectSubject?.({
      type: 'shot',
      id: first.id,
      label: `分镜 ${String(first.position).padStart(2, '0')}`,
      revision: first.row_version,
    });
  }, [
    page,
    loading,
    loadError,
    selectedShotId,
    subject?.type,
    subject?.id,
    search,
    pendingOnly,
    mediaTab,
    editingSnapshot.revision,
    selectSubject,
  ]);
  return (
    <div className="storyboard-workspace">
      {deleteNotice && (
        <Alert
          type={deleteNotice.type}
          title="分镜删除"
          noticeKey={`shot:${deleteNotice.id}:delete`}
          message={deleteNotice.message}
          description={deleteNotice.description}
          actions={
            <Button
              loading={loading}
              onClick={() => {
                setDeleteNotice(null);
                setStoryboardRevision((revision) => revision + 1);
              }}
            >
              重新加载列表核对
            </Button>
          }
        />
      )}
      {message && (
        <Alert
          type={feedbackType(message)}
          message={message}
          action={unavailableDraftId ? <Button onClick={downloadShotDraft}>下载分镜草稿</Button> : undefined}
        />
      )}
      {detailError && (
        <Alert
          type="error"
          showIcon
          message={'分镜详情加载失败：' + detailError}
          action={
            <Button loading={detailLoading} onClick={() => setDetailRetry((revision) => revision + 1)}>
              重新加载分镜详情
            </Button>
          }
        />
      )}
      {assetsError && (
        <Alert
          type="error"
          showIcon
          message={'关联素材加载失败：' + assetsError}
          action={
            <Button loading={assetsLoading} onClick={() => setAssetsRevision((revision) => revision + 1)}>
              重新加载关联素材
            </Button>
          }
        />
      )}
      {loadError && (
        <Alert
          type="error"
          showIcon
          message={'分镜列表加载失败：' + loadError}
          action={
            <Button loading={loading} onClick={() => setStoryboardRevision((revision) => revision + 1)}>
              重新加载分镜列表
            </Button>
          }
        />
      )}
      <CreationEditorSlot stage="storyboard">
        <ResourceCollection title="分镜工作区" mode={collectionMode} selectable={batchSelection.enabled} onVisibleChange={setVisibleCollectionIds} onCandidateState={onCandidateState} onMode={(next) => {
          if (collectionMode === 'single' && next !== 'single') {
            if (dialogueBarrier.current?.hasUnsettled()) void dialogueBarrier.current.flush();
            void flushAll();
          }
          setCollectionMode(next);
        }}
          selectedIds={selectedCollectionIds} onToggle={batchSelection.toggle}
          onOpen={(id) => {
            const shot = collectionShots.find((item) => item.id === id);
            if (shot && !getShot(id)) setShot(shot);
            setCollectionMode('single');
            void selectShot(id);
          }}
          items={collectionVisible.map((shot) => {
            const candidate = batchCandidates[shot.id];
            const showCandidate = !!candidate && candidate.mediaId !== shot.image?.media_id;
            return {
              id: shot.id, label: shot.title || `分镜 ${shot.position}`,
              selectionLabel: `选择分镜：${shot.title || `分镜 ${shot.position}`}`,
              description: shot.script, image: showCandidate ? candidate.url : shot.image?.url ?? null,
              imageCandidate: showCandidate,
              status: [
                `图：${shot.image ? '已设置' : '未设置'}${batchProgress.shot_image[shot.id] ? ` · ${batchProgress.shot_image[shot.id]}` : ''}`,
                `视频：${shot.video ? '已设置' : '未设置'}${batchProgress.shot_video[shot.id] ? ` · ${batchProgress.shot_video[shot.id]}` : ''}`,
              ],
            };
          })}
          toolbar={<>
            {(['shot_image', 'shot_video'] as const).map((scene) => <BatchLauncher key={scene}
              compactScene={scene} presentation="standard-creation"
              scope={{ library: 'project', project_id: projectId }}
              selection={{ ...batchSelection, ids: selectedCollectionIds }} loadedIds={collectionIds}
              disabled={readOnly || collectionLoading || !!collectionError || busy || loading || !!loadError || lockedShots.size > 0}
              beforePreflight={async () => (await flushAll()) && (await flushStandardProjectSessions(projectId))} />)}
            {batchSelection.enabled && <Tooltip title="全选当前筛选结果，用于批量生成">
              <Checkbox aria-label="全选筛选结果" disabled={readOnly || collectionLoading || !!collectionError || !collectionIds.length}
                checked={collectionIds.length > 0 && collectionIds.every((id) => batchSelection.ids.includes(id))}
                indeterminate={selectedCollectionIds.length > 0 && selectedCollectionIds.length < collectionIds.length}
                onChange={(event) => batchSelection.setIds(event.target.checked
                  ? [...new Set([...batchSelection.ids, ...collectionIds])]
                  : batchSelection.ids.filter((id) => !collectionIds.includes(id)))}>全选</Checkbox>
            </Tooltip>}
            {collectionError && <span role="alert">{collectionError}</span>}
          </>}>
        <div className="standard-storyboard-panel storyboardView">
          <StoryboardEditorHeader selectedShot={selectedShot}>
            {renderShotActions('main')}
          </StoryboardEditorHeader>
          {selectedShot ? (
            <div className="storyboardViewBody">
              <div id="storyboard-image-pane" className="storyboardDetails">
                {shotInformation}
                <div className="shotImageHeading"><h3>分镜图</h3>
                  <div className="shotImageActions">
                    {selectedShot.image?.media_id && <Button type="text" size="small"
                      disabled={readOnly || busy || lockedShots.has(selectedShot.id)}
                      onClick={() => setVideoReferenceRequest((current) => ({
                        shotId: selectedShot.id, reference: { type: 'shot_image', media_id: selectedShot.image!.media_id },
                        sequence: (current?.sequence ?? 0) + 1,
                      }))}>用作视频参考</Button>}
                    <Button type="text" size="small" onClick={() => setMediaHistoryOpen('image')}>图片记录</Button>
                  </div>
                </div>
                <ShotImageCandidates
                  key={selectedShot.id}
                  projectId={projectId}
                  linkedAssets={{
                    assets,
                    onChange: (assetId, checked) => {
                      const asset = assets.find((item) => item.id === assetId);
                      updateLocal(selectedShot.id, { asset_ids: checked
                        ? [...selectedShot.asset_ids, assetId]
                        : selectedShot.asset_ids.filter((id) => id !== assetId) });
                      if (checked && asset?.media_id && ['character', 'scene'].includes(asset.kind))
                        setVideoReferenceRequest((current) => ({ shotId: selectedShot.id,
                          reference: { type: 'asset', asset_id: assetId }, sequence: (current?.sequence ?? 0) + 1 }));
                    },
                  }}
                  settingsOpen
                  active
                  onCloseSettings={() => {}}
                  onOpenSettings={() => {}}
                  historyOpen={mediaHistoryOpen === 'image'}
                  onHistoryOpenChange={(open) => setMediaHistoryOpen(open ? 'image' : null)}
                  availableReferences={availableReferences}
                  availableLoading={assetsLoading}
                  availableError={assetsError}
                  onRefreshAvailable={() => setAssetsRevision((revision) => revision + 1)}
                  registerBarrier={registerImageDraft}
                  modelSelection={value.models.storyboardImage}
                  onModelChange={(id) =>
                    onChange({
                      ...value,
                      models: { ...value.models, storyboardImage: id },
                    })
                  }
                  onResolvedModel={onResolvedImageModel}
                  shot={selectedShot}
                  onUseForVideo={(mediaId) => setVideoReferenceRequest((current) => ({
                    shotId: selectedShot.id,
                    reference: { type: 'shot_image', media_id: mediaId },
                    sequence: (current?.sequence ?? 0) + 1,
                  }))}
                  disabled={
                    readOnly ||
                    busy ||
                    lockedShots.has(selectedShot.id) ||
                    ['error', 'conflict'].includes(editing.status(selectedShot.id) ?? '')
                  }
                  modelId={imageModelId}
                  capabilities={imageCapabilities}
                  capabilitiesLoading={capabilitiesLoading}
                  onRefreshCapabilities={refreshCapabilities}
                  prepareShot={(purpose) => prepareShot(selectedShot.id, purpose)}
                  onChanged={() => setStoryboardRevision((revision) => revision + 1)}
                />
              </div>
              <div id="storyboard-video-pane" className="storyboardVideo">
                <div className="storyboardVideoTools">
                  <Button type="text" size="small" onClick={() => setMediaHistoryOpen('video')}>视频记录</Button>
                </div>
                <ShotVideoCandidates
                  key={selectedShot.id}
                  projectId={projectId}
                  assets={assets}
                  referenceRequest={videoReferenceRequest}
                  handledReference={handledVideoReference}
                  settingsOpen
                  active
                  onCloseSettings={() => {}}
                  onOpenSettings={() => {}}
                  historyOpen={mediaHistoryOpen === 'video'}
                  onHistoryOpenChange={(open) => setMediaHistoryOpen(open ? 'video' : null)}
                  information={
                    <>
                      <details className="storyboard-dialogue-settings">
                        <summary>对白与音色</summary>
                        <NativeDialoguePanel
                          key={selectedShot.id}
                          revision={storyboardRevision}
                          registerBarrier={registerDialogueBarrier}
                          projectId={projectId}
                          shotId={selectedShot.id}
                          disabled={readOnly || busy || lockedShots.has(selectedShot.id)}
                          prepare={() => saveShot(selectedShot.id)}
                          onChanged={() => setStoryboardRevision((revision) => revision + 1)}
                        />
                      </details>
                    </>
                  }
                  registerBarrier={registerVideoDraft}
                  shot={selectedShot}
                  disabled={
                    readOnly ||
                    busy ||
                    lockedShots.has(selectedShot.id) ||
                    ['error', 'conflict'].includes(editing.status(selectedShot.id) ?? '')
                  }
                  model={value.models.video}
                  onModelChange={(id) =>
                    onChange({
                      ...value,
                      models: { ...value.models, video: id },
                    })
                  }
                  prepareShot={() => prepareShot(selectedShot.id, 'adoption')}
                  onChanged={() => setStoryboardRevision((revision) => revision + 1)}
                />
              </div>
            </div>
          ) : (
            <div className="studio-empty">{detailLoading ? '正在载入镜头…' : '选择左侧镜头开始制作'}</div>
          )}
        </div>
        </ResourceCollection>
      </CreationEditorSlot>
      <section className={`standard-storyboard-list resource-view-${view}`} aria-label="分镜创作区域">
        <header className="storyboard-editor-heading">
          <h3>
            分镜表{' '}
            <Button
              className="storyboard-list-count"
              type="text"
              aria-label="分镜生成记录"
              title="分镜提取记录"
              onClick={() => {
                setHistoryOpen(true);
                setCandidate(null);
                setTaskRevision((revision) => revision + 1);
              }}
            >
              {page?.total ?? 0}
            </Button>
          </h3>
          <div className="storyboard-heading-actions">
            <ResourceViewToggle value={view} onChange={setView} options={['list', 'grid']} />
            <Button
              type="text"
              aria-label="查看分镜提取记录"
              title="分镜提取记录"
              icon={<History size={16} />}
              onClick={() => {
                setHistoryOpen(true);
                setCandidate(null);
                setTaskRevision((revision) => revision + 1);
              }}
            />
          </div>
        </header>
        <div className="standard-storyboard-filters">
          <Input
            prefix={<Icon name="search" size={14} />}
            aria-label="搜索镜头名称"
            value={search}
            placeholder="搜索镜头名称"
            allowClear
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="storyboard-category-tabs" aria-label="镜头筛选">
            <button type="button" aria-pressed={!pendingOnly} onClick={() => setPendingOnly(false)}>
              全部镜头
            </button>
            <button type="button" aria-pressed={pendingOnly} onClick={() => setPendingOnly(true)}>
              待制作
            </button>
          </div>
        </div>
        {batchSelection.enabled && !readOnly && batchMode && (
          <Button type="text" disabled={selectionDisabled} onClick={() => setBatchMode(false)}>
            退出批量操作
          </Button>
        )}
        {!readOnly && batchMode && (
          <BatchLauncher
            presentation="standard-creation"
            scope={{ library: 'project', project_id: projectId }}
            selection={{
              ...batchSelection,
              enabled: batchSelection.enabled && batchMode,
            }}
            loadedIds={page?.items.map((shot) => shot.id) ?? []}
            disabled={busy || loading || !!loadError || lockedShots.size > 0}
            beforePreflight={flushAll}
          />
        )}
        <StoryboardShotList
          loading={loading}
          page={page}
          loadError={loadError}
          readOnly={readOnly}
          busy={busy}
          add={add}
          visibleShots={visibleShots}
          setSearch={setSearch}
          setPendingOnly={setPendingOnly}
          mediaTab={mediaTab}
          selectedShotId={selectedShotId}
          selectionDisabled={selectionDisabled}
          batchSelection={batchSelection}
          batchMode={batchMode}
          selectShot={selectShot}
          deleteShot={deleteShot}
          deletingShotId={deletingShotId}
          lockedShots={lockedShots}
          moreLoading={moreLoading}
          moreError={moreError}
          loadMoreShots={loadMoreShots}
        />
        <Button
          className="resource-create-button"
          type="text"
          aria-label="创建分镜"
          disabled={readOnly || busy || loading || !!loadError || !page}
          onClick={() => {
            setSearch('');
            setPendingOnly(false);
            void add();
          }}
          icon={<Icon name="plus" size={16} />}
        >
          创建
        </Button>
      </section>
      {historyOpen && (
        <StoryboardHistory
          projectId={projectId}
          busy={busy}
          candidate={candidate}
          setCandidate={setCandidate}
          readOnly={readOnly}
          loading={loading}
          loadError={loadError}
          hasPage={!!page}
          message={message}
          setMessage={setMessage}
          applyResult={applyResult}
          tasksLoading={tasksLoading}
          tasks={tasks}
          tasksError={tasksError}
          setTaskRevision={setTaskRevision}
          tasksLoaded={tasksLoaded}
          setTaskDetailId={setTaskDetailId}
          tasksTotal={tasksTotal}
          loadMoreTasks={loadMoreTasks}
          renderShotActions={renderShotActions}
          setActionsOrigin={setActionsOrigin}
          setHistoryOpen={setHistoryOpen}
        />
      )}
      {taskDetailId && (
        <TaskDetail
          presentation="standard-creation"
          id={taskDetailId}
          onClose={() => setTaskDetailId(null)}
          onChanged={() => setTaskRevision((revision) => revision + 1)}
          onCreated={(task) => {
            setTaskDetailId(task.generation_id);
            setTaskRevision((revision) => revision + 1);
          }}
        />
      )}
    </div>
  );
}
