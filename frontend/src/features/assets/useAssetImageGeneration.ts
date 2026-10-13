import { useCallback, useEffect, useRef, useState } from 'react';
import { generations } from '../../api/modules/generations';
import type { GenerationDetail, GenerationSummary } from '../../api/types/generations';
import { errorMessage, isCancelled } from '../../api/http';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { buildAssetImageRequest, mergeAssetImageTasks, missingActiveTaskIds, shouldRefreshAssetCandidates, type AssetImageOptions } from './asset-image-generation';
import type { LibraryAssetRead } from '../../api/modules/assets';
import type { UserCreationDraft } from '../../api/modules/creation-drafts';
import { clearRecovery, creationScope, readRecovery, saveRecovery } from '../projects/creation-recovery';
import { assetGenerationAcceptanceUnknown, isPendingAssetGeneration, type PendingAssetGeneration } from './asset-generation-submission';
import { useAuth } from '../auth/AuthSession';

const ACTIVE = new Set(['queued', 'running']);
const PAGE_SIZE = 20;

export function useAssetImageGeneration(
  asset: LibraryAssetRead,
  onSaveBeforeGenerate: () => Promise<LibraryAssetRead | null>,
  onCandidatesChanged: () => void,
  onSubmissionBusyChange: (busy: boolean) => void,
  onVersionConflict?: (cause: unknown) => void,
  onSavePrivateDraft?: () => Promise<UserCreationDraft | null>,
) {
  const auth = useAuth();
  const recoveryScope = creationScope(`asset-generation-submission:${asset.id}`);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [details, setDetails] = useState<Record<string, GenerationDetail>>({});
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [refreshError, setRefreshError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [actionTaskId, setActionTaskId] = useState<string>();
  const [uncertain, setUncertain] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const pendingRequest = useRef<PendingAssetGeneration | null>(null);
  const recoveryBlocked = useRef(false);
  const reconcileLock = useRef(false);
  const sequence = useRef(0);
  const detailSequence = useRef(0);
  const submittingRef = useRef(false);
  const actionRef = useRef(false);
  const loadMoreRef = useRef(false);
  const alive = useRef(true);
  const assetIdRef = useRef(asset.id);
  const tasksRef = useRef<GenerationSummary[]>([]);
  const candidatesChangedRef = useRef(onCandidatesChanged);
  assetIdRef.current = asset.id;
  candidatesChangedRef.current = onCandidatesChanged;

  useEffect(() => {
    pendingRequest.current = null; recoveryBlocked.current = false;
    try {
      const pending = readRecovery(recoveryScope, isPendingAssetGeneration);
      if (pending && pending.body.source?.scene === 'asset_image' && pending.body.source.asset_id === asset.id) {
        pendingRequest.current = pending; setUncertain(true); setSubmitError('图片生成请求受理结果待核对。原请求已保留，请先核对。');
      } else setUncertain(false);
    } catch (cause) { recoveryBlocked.current = true; setUncertain(true); setSubmitError(errorMessage(cause)); }
  }, [asset.id, auth.user?.id, recoveryScope]);

  const fetchActive = useCallback(async (status: 'queued' | 'running', signal?: AbortSignal) => {
    const found: GenerationSummary[] = [];
    let offset = 0;
    let total = 1;
    do {
      const page = await generations.list({
        service_type: 'image', source_scene: 'asset_image', source_id: asset.id, status, offset, limit: 100,
      }, signal);
      found.push(...page.items);
      total = page.total;
      offset += page.items.length;
      if (!page.items.length) break;
    } while (offset < total);
    return found;
  }, [asset.id]);

  const refresh = useCallback(async (signal?: AbortSignal): Promise<GenerationSummary[] | undefined> => {
    const requestNo = ++sequence.current;
    setHistoryLoading(true);
    try {
      const [latest, queued, running] = await Promise.all([
        generations.list({ service_type: 'image', source_scene: 'asset_image', source_id: asset.id, offset: 0, limit: PAGE_SIZE }, signal),
        fetchActive('queued', signal),
        fetchActive('running', signal),
      ]);
      const filtered = mergeAssetImageTasks(latest.items, [...queued, ...running]);
      const missingIds = missingActiveTaskIds(tasksRef.current, filtered);
      const reconciled = await Promise.allSettled(missingIds.map((id) => generations.detail(id, signal)));
      const resolved = reconciled.flatMap((result) => result.status === 'fulfilled'
        && result.value.source?.scene === 'asset_image' && result.value.source.asset_id === asset.id ? [result.value] : []);
      const resolvedIds = new Set(resolved.map((task) => task.generation_id));
      const unresolved = tasksRef.current.filter((task) => missingIds.includes(task.generation_id) && !resolvedIds.has(task.generation_id));
      const incoming = mergeAssetImageTasks(filtered, [...resolved, ...unresolved]);
      if (signal?.aborted || requestNo !== sequence.current || assetIdRef.current !== asset.id) return undefined;
      if (shouldRefreshAssetCandidates(tasksRef.current, incoming)) candidatesChangedRef.current();
      setTasks((current) => {
        const next = mergeAssetImageTasks(
          current.filter((task) => task.source?.scene === 'asset_image' && task.source.asset_id === asset.id),
          incoming,
        );
        tasksRef.current = next;
        return next;
      });
      setHistoryTotal(latest.total);
      setHistoryOffset((current) => Math.max(current, latest.items.length));
      setRefreshError(resolved.length < missingIds.length ? '部分活动生成任务无法核对，已保留上次状态。请重试读取记录。' : '');
      setHistoryLoaded(true);
      return incoming;
    } catch (cause) {
      if (!signal?.aborted && !isCancelled(cause) && alive.current && requestNo === sequence.current && assetIdRef.current === asset.id) {
        setRefreshError(`读取生成记录失败：${errorMessage(cause)}${tasksRef.current.length ? '已保留上次记录。' : ''}`);
      }
      return undefined;
    } finally {
      if (!signal?.aborted && alive.current && requestNo === sequence.current && assetIdRef.current === asset.id) setHistoryLoading(false);
    }
  }, [asset.id, fetchActive]);

  const loadMore = useCallback(async () => {
    if (loadMoreRef.current || historyOffset >= historyTotal) return;
    loadMoreRef.current = true;
    setLoadingMore(true);
    const expectedAssetId = asset.id;
    try {
      const page = await generations.list({
        service_type: 'image', source_scene: 'asset_image', source_id: expectedAssetId, offset: historyOffset, limit: PAGE_SIZE,
      });
      if (assetIdRef.current !== expectedAssetId) return;
      setTasks((current) => {
        const next = mergeAssetImageTasks(current, page.items);
        tasksRef.current = next;
        return next;
      });
      setHistoryOffset((current) => current + page.items.length);
      setHistoryTotal(page.total);
      setRefreshError('');
    } catch (cause) {
      if (assetIdRef.current === expectedAssetId) setRefreshError(errorMessage(cause));
    } finally {
      loadMoreRef.current = false;
      if (alive.current && assetIdRef.current === expectedAssetId) setLoadingMore(false);
    }
  }, [asset.id, historyOffset, historyTotal]);

  const loadDetail = useCallback(async (id: string, signal?: AbortSignal) => {
    const requestNo = ++detailSequence.current;
    const expectedAssetId = asset.id;
    try {
      const detail = await generations.detail(id, signal);
      if (signal?.aborted || requestNo !== detailSequence.current || assetIdRef.current !== expectedAssetId
        || detail.source?.scene !== 'asset_image' || detail.source.asset_id !== expectedAssetId) return;
      setDetails((current) => ({ ...current, [id]: detail }));
      setTasks((current) => {
        const next = mergeAssetImageTasks(current, [detail]);
        tasksRef.current = next;
        return next;
      });
      setRefreshError('');
    } catch (cause) {
      if (!signal?.aborted && !isCancelled(cause) && assetIdRef.current === expectedAssetId) setRefreshError(errorMessage(cause));
    }
  }, [asset.id]);

  useEffect(() => {
    alive.current = true;
    tasksRef.current = [];
    setTasks([]);
    setDetails({});
    setHistoryOffset(0);
    setHistoryTotal(0);
    setRefreshError('');
    setHistoryLoaded(false); setHistoryLoading(true); setLoadingMore(false);
    if (!pendingRequest.current && !recoveryBlocked.current) setSubmitError('');
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function tick() {
      const incoming = await refresh(controller.signal);
      if (!controller.signal.aborted) {
        const delay = incoming?.some((task) => ACTIVE.has(task.status)) ? 3000 : 15000;
        timer = setTimeout(tick, delay);
      }
    }
    void tick();
    return () => {
      alive.current = false;
      controller.abort();
      clearTimeout(timer);
      sequence.current += 1;
      detailSequence.current += 1;
    };
  }, [asset.id, refresh]);

  async function submit(options: AssetImageOptions) {
    if (submittingRef.current || pendingRequest.current || recoveryBlocked.current) return null;
    submittingRef.current = true;
    setSubmitting(true);
    onSubmissionBusyChange(true);
    setSubmitError('');
    const expectedAssetId = asset.id;
    try {
      const saved = await onSaveBeforeGenerate();
      if (!saved || assetIdRef.current !== expectedAssetId) return null;
      const privateDraft = onSavePrivateDraft ? await onSavePrivateDraft() : null;
      if (onSavePrivateDraft && !privateDraft || assetIdRef.current !== expectedAssetId) return null;
      const body = buildAssetImageRequest(saved, privateDraft ? {
        ...options, configId: privateDraft.model_config_id ?? undefined, supplement: '',
        count: privateDraft.image_settings?.count ?? 1, aspect: privateDraft.image_settings?.aspect,
        resolution: privateDraft.image_settings?.resolution,
        draft: { id: privateDraft.id, draft_version: privateDraft.draft_version },
      } : options);
      const scope = 'asset-image:' + saved.id;
      const key = await requestAttempt(scope, body, attemptStorage());
      const pending = { key, body };
      saveRecovery(recoveryScope, pending);
      pendingRequest.current = pending;
      const receipt = await generations.generateImage(body, key);
      pendingRequest.current = null; setUncertain(false); clearRecovery(recoveryScope);
      clearAttempt(scope, attemptStorage());
      if (alive.current && assetIdRef.current === expectedAssetId) {
        setTasks((current) => {
          const next = mergeAssetImageTasks(current, [{
            ...receipt,
            created_at: new Date().toISOString(),
            source: body.source,
            can_cancel: receipt.status === 'queued' || receipt.status === 'running',
            can_retry: false,
            can_resume: false,
          }]);
          tasksRef.current = next;
          return next;
        });
        await refresh();
      }
      return receipt;
    } catch (cause) {
      if (alive.current && assetIdRef.current === expectedAssetId) {
        if (pendingRequest.current && assetGenerationAcceptanceUnknown(cause)) {
          setUncertain(true); setSubmitError('图片生成请求受理结果待核对。原请求已保留，请先核对，避免重复生成。');
        } else {
          if (pendingRequest.current) { pendingRequest.current = null; clearRecovery(recoveryScope); }
          setSubmitError(errorMessage(cause)); onVersionConflict?.(cause);
        }
      }
      return null;
    } finally {
      submittingRef.current = false;
      onSubmissionBusyChange(false);
      if (alive.current && assetIdRef.current === expectedAssetId) setSubmitting(false);
    }
  }

  async function reconcileSubmission() {
    if (!pendingRequest.current || reconcileLock.current) return;
    const pending = pendingRequest.current; const expectedAssetId = asset.id;
    reconcileLock.current = true; setReconciling(true);
    try {
      const receipt = await generations.lookup('image', pending.key);
      if (!alive.current || assetIdRef.current !== expectedAssetId) return;
      if (!receipt) {
        setSubmitError('尚未查到原请求的生成记录。受理状态仍待核对，原输入和请求键已保留。'); return;
      }
      const task = await generations.detail(receipt.generation_id);
      if (task.source?.scene !== 'asset_image' || task.source.asset_id !== expectedAssetId) throw new Error('原生成请求的来源不匹配，请核对任务记录。');
      pendingRequest.current = null; clearRecovery(recoveryScope); clearAttempt('asset-image:' + expectedAssetId, attemptStorage());
      setUncertain(false); setSubmitError('');
      setTasks(current => { const next = mergeAssetImageTasks(current, [task]); tasksRef.current = next; return next; });
      if (task.status === 'succeeded') candidatesChangedRef.current();
      await refresh();
    } catch (cause) { if (alive.current && assetIdRef.current === expectedAssetId) setSubmitError(`原生成请求核对失败：${errorMessage(cause)}`); }
    finally { reconcileLock.current = false; if (alive.current && assetIdRef.current === expectedAssetId) setReconciling(false); }
  }

  async function perform(task: GenerationSummary, action: 'cancel' | 'resume' | 'retry') {
    if (actionRef.current) return;
    actionRef.current = true;
    setActionTaskId(task.generation_id);
    const expectedAssetId = asset.id;
    try {
      if (action === 'cancel') await generations.cancel(task.generation_id);
      else if (action === 'resume') await generations.resume(task.generation_id);
      else {
        const scope = 'asset-image-retry:' + task.generation_id;
        const key = await requestAttempt(scope, {}, attemptStorage());
        await generations.retry(task.generation_id, key);
        clearAttempt(scope, attemptStorage());
      }
      if (assetIdRef.current === expectedAssetId) {
        await refresh();
        await loadDetail(task.generation_id);
      }
    } catch (cause) {
      if (alive.current && assetIdRef.current === expectedAssetId) setSubmitError(errorMessage(cause));
    } finally {
      actionRef.current = false;
      if (alive.current && assetIdRef.current === expectedAssetId) setActionTaskId(undefined);
    }
  }

  return {
    tasks, details, hasMore: historyOffset < historyTotal, refreshError, historyLoading, historyLoaded, loadingMore, submitError, submitting,
    actionTaskId, submit, perform, load: refresh, loadMore, loadDetail, uncertain, reconciling, reconcileSubmission,
  };
}
