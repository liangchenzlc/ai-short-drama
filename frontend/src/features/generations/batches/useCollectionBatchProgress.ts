import { generationBatches } from '@host/api/generations/batches/requests';
import { imageGroups } from '@host/api/media/image-groups';
import { batchSubmissionChanged, readProjectBatchSubmissions } from '@host/features/generations/batches/batchSubmission';
import type { SavedBatchSubmission } from '@host/features/generations/batches/batchSubmission';
import type { BatchDetail, BatchScene } from '@host/types/generations/batches/requests';
import { useEffect, useRef, useState } from 'react';

type CollectionStatuses = Record<BatchScene, Record<string, string>>;
type ImageScene = 'asset_image' | 'shot_image';
type CandidateGroups = Record<ImageScene, Record<string, string>>;
type CollectionProjection = { statuses: CollectionStatuses; groups: CandidateGroups };
type CandidateImage = { mediaId: string; url: string };

const itemLabels: Record<string, string> = {
  waiting: '排队', active: '生成中', running: '生成中', succeeded: '生成完成',
  failed: '失败', needs_review: '受理待核对', blocked: '待处理', cancelled: '已取消',
};

const emptyStatuses = (): CollectionStatuses => ({ asset_image: {}, shot_image: {}, shot_video: {} });
const emptyProjection = (): CollectionProjection => ({ statuses: emptyStatuses(), groups: { asset_image: {}, shot_image: {} } });
const needsPolling = (batch: BatchDetail) => batch.status === 'running' || batch.items.some((item) => item.status === 'active');

async function loadBatch(id: string, signal: AbortSignal): Promise<BatchDetail> {
  let page = await generationBatches.detail(id, 0, signal);
  const items = [...page.items];
  while (items.length < page.total) {
    page = await generationBatches.detail(id, items.length, signal);
    if (!page.items.length) break;
    items.push(...page.items);
  }
  return { ...page, items };
}

function collectionStatuses(
  submissions: SavedBatchSubmission[], cache: Map<string, BatchDetail>, unavailable: Set<string>,
): CollectionProjection {
  const next = emptyProjection();
  const latest = new Map<string, string>();
  for (const [id, batch] of cache) {
    for (const item of batch.items) {
      const key = `${batch.scene}:${item.source_id}`;
      const order = `${item.task.created_at || batch.created_at}:${item.task.generation_id.padStart(24, '0')}`;
      if (!latest.has(key) || order > latest.get(key)!) {
        latest.set(key, order);
        next.statuses[batch.scene][item.source_id] = unavailable.has(id) ? '状态暂不可用'
          : item.status === 'waiting' && batch.status === 'paused' ? '已暂停' : itemLabels[item.status] ?? item.status;
        if (batch.scene !== 'shot_video') {
          if (!unavailable.has(id) && item.status === 'succeeded' && item.task.image_group_id)
            next.groups[batch.scene][item.source_id] = item.task.image_group_id;
          else delete next.groups[batch.scene][item.source_id];
        }
      }
    }
  }
  for (const { submission } of submissions) {
    for (const part of submission.parts) {
      const scene = part.value.scene;
      if (!part.id || unavailable.has(part.id) && !cache.has(part.id)) {
        const label = part.id ? '状态暂不可用' : part.rejected ? '预检失效' : part.started ? '受理待核对' : '待提交';
        for (const sourceId of part.value.accepted_ids) {
          next.statuses[scene][sourceId] = label;
          if (scene !== 'shot_video') delete next.groups[scene][sourceId];
        }
      }
    }
    if (submission.pendingRetry) {
      const original = cache.get(submission.pendingRetry.batchId);
      for (const item of original?.items ?? []) {
        if (submission.pendingRetry.itemIds.includes(item.id)) {
          next.statuses[original!.scene][item.source_id] = '受理待核对';
          if (original!.scene !== 'shot_video') delete next.groups[original!.scene][item.source_id];
        }
      }
    }
  }
  return next;
}

export function useCollectionBatchProgress(
  projectId: string | undefined, account: string, scene: ImageScene, visibleIds: string[],
) {
  const [result, setResult] = useState(() => ({ projectId, account, projection: emptyProjection() }));
  useEffect(() => {
    const controller = new AbortController();
    const cache = new Map<string, BatchDetail>();
    const unavailable = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false, dirty = false, force = true;

    async function refresh() {
      if (running) { dirty = true; return; }
      running = true;
      do {
        dirty = false;
        const reloadAll = force;
        force = false;
        const submissions = readProjectBatchSubmissions(projectId);
        const ids = [...new Set(submissions.flatMap(({ submission }) => [
          ...submission.parts.flatMap((part) => part.id ? [part.id] : []), ...submission.retryIds,
        ]))];
        for (const id of cache.keys()) if (!ids.includes(id)) cache.delete(id);
        for (const id of unavailable) if (!ids.includes(id)) unavailable.delete(id);
        await Promise.all(ids.map(async (id) => {
          const previous = cache.get(id);
          if (!reloadAll && previous && !needsPolling(previous) && !unavailable.has(id)) return;
          try {
            cache.set(id, await loadBatch(id, controller.signal));
            unavailable.delete(id);
          } catch {
            unavailable.add(id);
          }
        }));
        if (controller.signal.aborted) return;
        if (dirty) continue;

        const next = collectionStatuses(submissions, cache, unavailable);
        setResult((previous) => previous.projectId === projectId && previous.account === account && JSON.stringify(previous.projection) === JSON.stringify(next)
          ? previous : { projectId, account, projection: next });
        if (unavailable.size || [...cache.values()].some(needsPolling)) timer = setTimeout(() => void refresh(), 4000);
      } while (dirty && !controller.signal.aborted);
      running = false;
    }

    function changed() {
      clearTimeout(timer);
      force = true;
      if (running) dirty = true;
      else void refresh();
    }
    window.addEventListener(batchSubmissionChanged, changed);
    window.addEventListener('focus', changed);
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener(batchSubmissionChanged, changed);
      window.removeEventListener('focus', changed);
    };
  }, [projectId, account]);
  const projection = result.projectId === projectId && result.account === account ? result.projection : emptyProjection();
  const { candidates, onCandidateState } = useVisibleBatchCandidates(projectId, account, scene, projection.groups[scene], visibleIds);
  return { statuses: projection.statuses, candidates, onCandidateState };
}

function useVisibleBatchCandidates(
  projectId: string | undefined, account: string, scene: ImageScene, groups: Record<string, string>, visibleIds: string[],
) {
  const cache = useRef(new Map<string, CandidateImage | null>());
  const schedule = useRef<() => void>(() => {});
  const reportImage = useRef<(sourceId: string, url: string, loaded: boolean) => void>(() => {});
  const visible = useRef(visibleIds), currentGroups = useRef(groups);
  const [, setRevision] = useState(0);
  visible.current = visibleIds;
  currentGroups.current = groups;
  const visibleKey = JSON.stringify(visibleIds), groupsKey = JSON.stringify(groups);

  useEffect(() => {
    const controller = new AbortController();
    const pending = new Set<string>(), failed = new Set<string>(), imageErrors = new Set<string>();
    let active = 0;
    cache.current.clear();
    function loadVisible() {
      for (const sourceId of visible.current) {
        if (active >= 4) break;
        const groupId = currentGroups.current[sourceId];
        if (!groupId || cache.current.has(groupId) || pending.has(groupId) || failed.has(groupId)) continue;
        active++;
        pending.add(groupId);
        void imageGroups.detail(groupId, controller.signal).then((group) => {
          if (controller.signal.aborted) return;
          const item = group.items.find((entry) => entry.status === 'ready' && entry.media_id && entry.url);
          if (item) cache.current.set(groupId, { mediaId: item.media_id!, url: item.url! });
          else if (group.status !== 'generating') cache.current.set(groupId, null);
          else failed.add(groupId);
          setRevision((value) => value + 1);
        }).catch(() => {
          if (!controller.signal.aborted) failed.add(groupId);
        }).finally(() => {
          active--;
          pending.delete(groupId);
          if (!controller.signal.aborted) loadVisible();
        });
      }
    }
    function focus() { failed.clear(); imageErrors.clear(); loadVisible(); }
    reportImage.current = (sourceId, url, loaded) => {
      const groupId = currentGroups.current[sourceId];
      if (!groupId || cache.current.get(groupId)?.url !== url) return;
      if (loaded) { imageErrors.delete(groupId); return; }
      cache.current.delete(groupId);
      if (imageErrors.has(groupId)) failed.add(groupId);
      else imageErrors.add(groupId);
      setRevision((value) => value + 1);
      loadVisible();
    };
    schedule.current = loadVisible;
    window.addEventListener('focus', focus);
    loadVisible();
    return () => {
      controller.abort();
      window.removeEventListener('focus', focus);
      schedule.current = () => {};
      reportImage.current = () => {};
      cache.current.clear();
    };
  }, [projectId, account, scene]);
  useEffect(() => { schedule.current(); }, [visibleKey, groupsKey]);

  const candidates: Record<string, CandidateImage> = {};
  for (const [sourceId, groupId] of Object.entries(groups)) {
    const image = cache.current.get(groupId);
    if (image) candidates[sourceId] = image;
  }
  return { candidates, onCandidateState: (sourceId: string, url: string, loaded: boolean) => reportImage.current(sourceId, url, loaded) };
}
