import { storyboardApi } from '@host/api/creation/storyboard/shots';
import { ApiError, errorMessage } from '@host/api/http';
import { releaseStandardSession, retainStandardSession } from '@host/features/creation/shared/sessions/registry';
import { loadLoadedShots } from '@host/features/creation/storyboard/sessions/collection';
import { StoryboardEditingSession, shotDraftValue } from '@host/features/creation/storyboard/sessions/editing-session';
import { mergeStoryboardReload } from '@host/features/creation/storyboard/sessions/storyboard-session';
import type { StoryboardCollectionOptions } from '@host/types/creation/storyboard/sessions/collection';
import type { ShotRead, StoryboardPage } from '@host/types/creation/storyboard/shots';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
/** 列表、离页详情与本机草稿共享版本下限，旧响应不得重新带回已删除镜头。 */
export function useStoryboardCollection({
  projectId,
  account,
  refreshToken,
  subject,
  selectSubject,
  shotLocks,
  context: { epoch: contextEpoch, isCurrent: isCurrentContext },
  setMessage,
}: StoryboardCollectionOptions) {
  const api = storyboardApi(projectId);
  const contextKey = projectId;
  const editingKey = `storyboard-edits:${projectId}`;

  const editing = useMemo(
    () =>
      retainStandardSession(account, editingKey, () => new StoryboardEditingSession(projectId, account), {
        label: '分镜',
        projectId,
      }),
    [account, editingKey, projectId],
  );

  const editingSnapshot = useSyncExternalStore(editing.subscribe, editing.getSnapshot);

  useEffect(() => () => releaseStandardSession(account, editingKey), [account, editingKey]);

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

  const [loading, setLoading] = useState(true);

  const [loadError, setLoadError] = useState('');

  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);

  const selectedShotRef = useRef<string | null>(null);

  const initialSelectionPending = useRef(true);

  const selectionEpoch = useRef(0);

  const locateShot = useRef<string | null>(null);

  const [moreLoading, setMoreLoading] = useState(false);

  const [moreError, setMoreError] = useState('');

  const moreLock = useRef(false);

  const [storyboardRevision, setStoryboardRevision] = useState(0);

  useLayoutEffect(() => {
    initialSelectionPending.current = true;
    setPage(null);
    removedShotIds.current.clear();
    storyboardVersionFloor.current = '0';
    extraShots.current.clear();
    setDetailError('');
    setDetailLoading(false);
    setUnavailableDraftId(null);
    locateShot.current = null;
    selectShotId(subject?.type === 'shot' ? subject.id : null);
  }, [contextKey]);

  function setPage(next: StoryboardPage | null) {
    pageRef.current = next;
    setPageState(next);
  }

  function getShot(id: string) {
    return editing.get(id)?.shot ?? pageRef.current?.items.find((shot) => shot.id === id) ?? extraShots.current.get(id);
  }

  function setShot(shot: ShotRead, storyboardVersion?: string) {
    if (removedShotIds.current.has(shot.id)) return;
    if (storyboardVersion && BigInt(storyboardVersion) < BigInt(storyboardVersionFloor.current)) return;
    editing.accept(shot, storyboardVersion);
    const current = pageRef.current;
    const version =
      storyboardVersion && (!current || BigInt(storyboardVersion) > BigInt(current.storyboard_version))
        ? storyboardVersion
        : current?.storyboard_version;
    if (current?.items.some((item) => item.id === shot.id)) {
      setPage({
        ...current,
        storyboard_version: version ?? current.storyboard_version,
        items: current.items.map((item) => (item.id === shot.id ? shot : item)),
      });
    } else {
      extraShots.current.set(shot.id, shot);
      setDetailRevision((revision) => revision + 1);
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
    if (listPending) setStoryboardRevision((revision) => revision + 1);
    window.dispatchEvent(new Event('creation-workflow-updated'));
  }, [editing, editingSnapshot.savedRevision]);

  function selectShotId(id: string | null) {
    if (id) initialSelectionPending.current = false;
    if (selectedShotRef.current !== id) {
      selectionEpoch.current++;
      setDetailLoading(false);
      setDetailError('');
    }
    selectedShotRef.current = id;
    setSelectedShotId(id);
  }

  function mergeLoadedPage(remote: StoryboardPage) {
    if (BigInt(remote.storyboard_version) < BigInt(storyboardVersionFloor.current)) return pageRef.current;
    if (pageRef.current && BigInt(remote.storyboard_version) < BigInt(pageRef.current.storyboard_version))
      return pageRef.current;
    const unsettled = unsettledIds();
    const items = remote.items
      .filter((item) => !removedShotIds.current.has(item.id))
      .map((item) => {
        const extra = extraShots.current.get(item.id);
        extraShots.current.delete(item.id);
        return extra && unsettled.has(item.id)
          ? extra
          : unsettled.has(item.id)
            ? item
            : restoreShotDraft(item, remote.storyboard_version);
      });
    return mergeStoryboardReload({ ...remote, items }, pageRef.current, unsettled);
  }

  function restoreShotDraft(remote: ShotRead, storyboardVersion = pageRef.current?.storyboard_version): ShotRead {
    return editing.restore(remote, storyboardVersion);
  }

  function unavailableShot(id: string, archived?: ShotRead) {
    const local = getShot(id);
    if (local && unsettledIds().has(id)) {
      setShot({
        ...local,
        deleted_at: archived?.deleted_at ?? new Date().toISOString(),
      });
      setUnavailableDraftId(id);
      setMessage('当前分镜已归档或删除，未保存的输入仍保留。请下载分镜草稿后核对。');
    } else setMessage('当前分镜已归档或删除，已返回本集分镜规划。');
    selectShotId(null);
    selectSubject?.(null);
    setDetailLoading(false);
  }

  function downloadShotDraft() {
    const shot = unavailableDraftId ? getShot(unavailableDraftId) : undefined;
    if (!shot) return;
    const url = URL.createObjectURL(
      new Blob(
        [
          JSON.stringify(
            {
              project_id: projectId,
              id: shot.id,
              row_version: shot.row_version,
              ...shotDraftValue(shot),
            },
            null,
            2,
          ),
        ],
        { type: 'application/json' },
      ),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `shot-${shot.id}-draft.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function downloadShotRecovery(id: string) {
    try {
      const raw = editing.exportRecovered(id);
      if (!raw) return;
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `shot-${id}-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setMessage(errorMessage(cause));
    }
  }

  function unsettledIds() {
    return new Set([...editing.unsettledIds(), ...shotLocks.current.keys()]);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError('');
    setMoreError('');
    loadLoadedShots(api, controller.signal, pageRef.current?.items.length ?? 20)
      .then((remote) => {
        if (controller.signal.aborted) return;
        setPage(mergeLoadedPage(remote));
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setLoadError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, storyboardRevision, refreshToken]);

  async function loadMoreShots() {
    const current = pageRef.current;
    if (!current || loading || moreLock.current || current.items.length >= current.total) return;
    const epoch = contextEpoch.current;
    moreLock.current = true;
    setMoreLoading(true);
    setMoreError('');
    try {
      const next = await api.shots(undefined, false, current.items.length);
      if (!isCurrentContext(epoch)) return;
      const latest = pageRef.current!;
      if (next.storyboard_version !== latest.storyboard_version)
        throw new Error('分镜列表已变化，请重新加载分镜列表后继续。');
      setPage(
        mergeLoadedPage({
          ...latest,
          total: next.total,
          items: [...latest.items, ...next.items.filter((item) => !latest.items.some((own) => own.id === item.id))],
        }),
      );
    } catch (cause) {
      if (isCurrentContext(epoch)) setMoreError(errorMessage(cause));
    } finally {
      moreLock.current = false;
      if (isCurrentContext(epoch)) setMoreLoading(false);
    }
  }

  const selectedShotUnsettled = !!selectedShotId && unsettledIds().has(selectedShotId);

  useLayoutEffect(() => {
    selectShotId(subject?.type === 'shot' ? subject.id : null);
  }, [subject?.type, subject?.id]);

  useEffect(() => {
    if (!selectedShotId || selectedShotRef.current !== selectedShotId) return;
    const loaded = getShot(selectedShotId);
    if (loaded && !storyboardRevision && !refreshToken && !detailRetry) return;
    const controller = new AbortController();
    const epoch = contextEpoch.current;
    const id = selectedShotId;
    setDetailLoading(true);
    setDetailError('');
    api
      .shot(id, controller.signal)
      .then((result) => {
        if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
        if (BigInt(result.storyboard_version) < BigInt(storyboardVersionFloor.current)) return;
        if (result.shot.deleted_at) {
          unavailableShot(id, result.shot);
          return;
        }
        setShot(restoreShotDraft(result.shot, result.storyboard_version), result.storyboard_version);
      })
      .catch((cause) => {
        if (controller.signal.aborted || !isCurrentContext(epoch) || selectedShotRef.current !== id) return;
        if (cause instanceof ApiError && cause.status === 404) unavailableShot(id);
        else setDetailError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted && isCurrentContext(epoch) && selectedShotRef.current === id)
          setDetailLoading(false);
      });
    return () => controller.abort();
  }, [contextKey, selectedShotId, selectedShotUnsettled, storyboardRevision, refreshToken, detailRetry]);

  useEffect(() => {
    if (!locateShot.current || !page || loading || moreLoading) return;
    const id = locateShot.current;
    if (page.items.some((shot) => shot.id === id)) {
      document.getElementById(`storyboard-shot-${id}`)?.scrollIntoView({ block: 'nearest' });
      locateShot.current = null;
    } else if (page.items.length < page.total && !moreError) void loadMoreShots();
  }, [page, loading, moreLoading, moreError]);
  return {
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
    loadMoreShots,
  };
}
