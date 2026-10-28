import { ApiError, errorMessage } from '@host/api/http';
import { loadLoadedShots } from '@host/features/creation/storyboard/sessions/collection';
import { confirmAction } from '@host/shared/ui/confirm';
import type { ShotMutationOptions } from '@host/types/creation/storyboard/editor/mutations';
/** 移动和删除共享写入互斥；先保存本机输入，再用最新行版本和集合版本提交。 */
export function createShotMutations({
  api,
  collection,
  selection,
  lifecycle,
  locks,
  state,
  flushAll,
}: ShotMutationOptions) {
  const {
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
  } = collection;
  const { selectedShotRef, selectionEpoch, selectShotId, selectSubject, toggleBatch } = selection;
  const { contextEpoch, isCurrentContext, currentAccess } = lifecycle;
  const { shotMutation, shotLocks, dialogueBarrier } = locks;
  const { setBusy, setMessage, setDeletingShotId, setDeleteNotice, setLoading, setLoadError, setStoryboardRevision } =
    state;
  async function mutateShot(id: string, action: 'up' | 'down') {
    const access = currentAccess.current;
    if (
      shotMutation.current ||
      access.readOnly ||
      access.busy ||
      access.loading ||
      access.loadError ||
      shotLocks.current.size ||
      selectedShotRef.current !== id
    )
      return;
    if (dialogueBarrier.current?.hasUnsettled()) {
      setMessage('对白尚未保存，请先在对白窗口保存或明确放弃修改。');
      return;
    }
    const epoch = contextEpoch.current;
    const token = Symbol(id);
    shotMutation.current = token;
    setBusy(true);
    setMessage('');
    try {
      if (!(await flushAll()) || !isCurrentContext(epoch) || selectedShotRef.current !== id || shotLocks.current.size)
        return;
      const shot = getShot(id);
      const current = pageRef.current;
      if (!shot || shot.deleted_at || !current || currentAccess.current.readOnly) return;
      if ((action === 'up' && shot.position <= 1) || (action === 'down' && shot.position >= current.total)) return;
      await api.move(id, current.storyboard_version, action === 'up' ? -1 : 1);
      if (!isCurrentContext(epoch)) return;
      setStoryboardRevision((revision) => revision + 1);
    } catch (cause) {
      if (isCurrentContext(epoch)) setMessage(errorMessage(cause));
    } finally {
      if (shotMutation.current === token) shotMutation.current = null;
      if (isCurrentContext(epoch)) setBusy(false);
    }
  }

  async function deleteShot(id: string) {
    const access = currentAccess.current;
    const shot = getShot(id);
    if (
      !shot ||
      shot.deleted_at ||
      shotMutation.current ||
      access.readOnly ||
      access.busy ||
      access.loading ||
      access.loadError ||
      shotLocks.current.size
    )
      return;
    if (dialogueBarrier.current?.hasUnsettled()) {
      setDeleteNotice({
        id,
        type: 'warning',
        message: '对白尚未保存，请先保存或明确放弃对白修改，再删除分镜。',
      });
      return;
    }
    const epoch = contextEpoch.current,
      token = Symbol(id);
    shotMutation.current = token;
    setBusy(true);
    setDeletingShotId(id);
    setDeleteNotice(null);
    try {
      const title = shot.title || `分镜 ${String(shot.position).padStart(2, '0')}`;
      if (
        !(await confirmAction(
          `删除“${title}”后无法恢复。当前输入会先保存，保存失败时不会删除。媒体与生成历史保留，已加入成片的片段保留并标记来源已删除。`,
          {
            title: '删除分镜',
            confirmText: '删除分镜',
            danger: true,
            className: 'standard-creation-dialog',
          },
        ))
      )
        return;
      if (!isCurrentContext(epoch) || currentAccess.current.readOnly) return;
      // 确认弹窗和自动保存都可能跨越项目切换；每个异步边界后重新核对上下文。
      if (!(await flushAll())) {
        if (isCurrentContext(epoch))
          setDeleteNotice({
            id,
            type: 'warning',
            message: '分镜或生成设置尚未保存，未执行删除。当前输入已保留，请先处理保存详情。',
          });
        return;
      }
      if (!isCurrentContext(epoch) || currentAccess.current.readOnly || shotLocks.current.size) return;
      const current = pageRef.current,
        saved = getShot(id);
      if (!current || !saved || saved.deleted_at) return;
      const result = await api.remove(id, {
        row_version: saved.row_version,
        storyboard_version: current.storyboard_version,
      });
      if (!isCurrentContext(epoch)) return;
      const latest = pageRef.current ?? current;
      const remaining = latest.items.filter((item) => item.id !== id);
      const wasSelected = selectedShotRef.current === id;
      const nextId = wasSelected
        ? (remaining.find((item) => item.position > saved.position) ?? remaining.at(-1))?.id
        : selectedShotRef.current;
      removedShotIds.current.add(result.deleted_id);
      storyboardVersionFloor.current =
        BigInt(result.storyboard_version) > BigInt(latest.storyboard_version)
          ? result.storyboard_version
          : latest.storyboard_version;
      for (const savedId of editing.ids()) editing.forget(savedId);
      extraShots.current.clear();
      setPage(null);
      setLoading(true);
      setLoadError('');
      toggleBatch(id, false);
      if (wasSelected) {
        selectShotId(null);
        selectSubject?.(null);
      }
      const selection = selectionEpoch.current;
      try {
        const remote = await loadLoadedShots(api, new AbortController().signal, latest.items.length);
        if (BigInt(remote.storyboard_version) < BigInt(storyboardVersionFloor.current))
          throw new Error('分镜列表仍是删除前版本，请重新加载。');
        const detail = nextId && !remote.items.some((item) => item.id === nextId) ? await api.shot(nextId) : null;
        if (detail && BigInt(detail.storyboard_version) < BigInt(storyboardVersionFloor.current))
          throw new Error('分镜详情仍是删除前版本，请重新加载。');
        if (!isCurrentContext(epoch)) return;
        setPage(mergeLoadedPage(remote));
        if (detail && !detail.shot.deleted_at)
          setShot(restoreShotDraft(detail.shot, detail.storyboard_version), detail.storyboard_version);
        if (selectionEpoch.current === selection) {
          const next = nextId ? getShot(nextId) : undefined;
          selectShotId(next?.id ?? null);
          selectSubject?.(
            next
              ? {
                  type: 'shot',
                  id: next.id,
                  label: `分镜 ${String(next.position).padStart(2, '0')}`,
                  revision: next.row_version,
                }
              : null,
          );
          requestAnimationFrame(() =>
            document
              .querySelector<HTMLButtonElement>(
                next
                  ? `#storyboard-shot-${next.id} .storyboard-summary`
                  : '.standard-storyboard-list .storyboard-heading-actions button',
              )
              ?.focus(),
          );
        }
      } catch (cause) {
        if (isCurrentContext(epoch)) {
          setLoadError(errorMessage(cause));
          setDeleteNotice({
            id,
            type: 'error',
            message: '分镜已删除，列表尚未刷新。请重新加载列表后继续编辑。',
            description: errorMessage(cause),
          });
        }
      } finally {
        if (isCurrentContext(epoch)) setLoading(false);
      }
      window.dispatchEvent(new Event('creation-workflow-updated'));
    } catch (cause) {
      if (isCurrentContext(epoch))
        setDeleteNotice({
          id,
          type: cause instanceof ApiError && cause.status === 409 ? 'warning' : 'error',
          message:
            cause instanceof ApiError && cause.status === 409
              ? '分镜版本已变化，未执行删除。当前卡片和输入已保留。'
              : '删除结果未确认，当前卡片和输入已保留。请重新加载列表核对。',
          description: errorMessage(cause),
        });
    } finally {
      if (shotMutation.current === token) shotMutation.current = null;
      if (isCurrentContext(epoch)) {
        setBusy(false);
        setDeletingShotId(null);
      }
    }
  }
  return { mutateShot, deleteShot };
}
