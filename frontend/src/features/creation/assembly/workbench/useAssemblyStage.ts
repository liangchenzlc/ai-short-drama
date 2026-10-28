import { renderBody } from '@host/api/creation/assembly/document';
import { errorMessage } from '@host/api/http';
import { useRenderRecovery } from '@host/features/creation/assembly/export/useRenderRecovery';
import { clipFrames, FPS, isIncludedVideo } from '@host/features/creation/assembly/sessions/assembly-editing';
import { useAssembly } from '@host/features/creation/assembly/workbench/useAssembly';
import { CreationRecoveryError } from '@host/features/creation/shared/recovery/creation-recovery';
import { confirmAction } from '@host/shared/ui/confirm';
import type { RenderJob } from '@host/types/creation/assembly/document';
import type { AssemblyStageProps } from '@host/types/creation/assembly/workbench/stage';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

/** 合成受理、轮询与恢复共享同一互斥锁，避免重复提交和旧轮询覆盖当前任务。 */
export function useAssemblyStage({ projectId, readOnly, registerBarrier }: AssemblyStageProps) {

  const barriers = useRef<{
    video: NavigationBarrier | null;
    sound: NavigationBarrier | null;
  }>({ video: null, sound: null });
  const registerVideo = useCallback((value: NavigationBarrier | null) => {
    barriers.current.video = value;
  }, []);
  const registerSound = useCallback((value: NavigationBarrier | null) => {
    barriers.current.sound = value;
  }, []);
  useEffect(() => {
    registerBarrier({
      hasUnsettled: () => Object.values(barriers.current).some((b) => b?.hasUnsettled()),
      flush: async () => {
        for (const barrier of Object.values(barriers.current))
          if (barrier?.hasUnsettled() && !(await barrier.flush())) return false;
        return true;
      },
    });
    return () => registerBarrier(null);
  }, [registerBarrier]);
  const editor = useAssembly(projectId, registerVideo);
  const { value, api } = editor;
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [preparationError, setPreparationError] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const [history, setHistory] = useState<RenderJob[] | null>(null);
  const [moreHistory, setMoreHistory] = useState(false);
  const [result, setResult] = useState<RenderJob | null>(null);
  const [applying, setApplying] = useState<RenderJob | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewJob, setPreviewJob] = useState<string | null>(null);
  const [previewStatus, setPreviewStatus] = useState<RenderJob | null>(null);
  const [previewError, setPreviewError] = useState('');
  const mutation = useRef(false);
  const completedPreviews = useRef(new Set<string>());
  const [focused, setFocused] = useState(false);
  const [soundOpen, setSoundOpen] = useState(false);
  const [soundTarget, setSoundTarget] = useState<HTMLDivElement | null>(null);
  const workspace = useRef<HTMLDivElement>(null);
  function setFocusMode(next: boolean) {
    setFocused(next);
    requestAnimationFrame(() => {
      workspace.current?.scrollIntoView({ block: 'start' });
      workspace.current?.querySelector<HTMLButtonElement>('.assembly-focus-toggle')?.focus({ preventScroll: true });
    });
  }
  const {
    pendingRender,
    damagedRender,
    renderRecoveryError,
    loadRenderRecovery,
    readPendingRender,
    submitRender,
    backupRenderRecovery,
    discardRenderRecovery
  } = useRenderRecovery(projectId, api.submitRender, setNotice);
  async function reconcileRender() {
    const attempt = readPendingRender();
    if (!attempt) return;
    const job = await submitRender(attempt);
    if (attempt.kind === 'preview') {
      if (job.status === 'succeeded') setResult(job);
      else if (['queued', 'running'].includes(job.status)) {
        setPreviewStatus(job);
        setPreviewJob(job.id);
      }
    }
    await editor.load();
  }
  const clips = value?.clips ?? [];
  const included = clips.filter(isIncludedVideo);
  const total = (included.reduce((sum, c) => sum + clipFrames(c), 0) / FPS) * 1000;
  const blocked = included.filter((c) => c.issue);
  const stale = included.some((c) => c.is_stale);
  const activeExports =
    value?.jobs?.filter((j) => j.kind === 'export' && ['queued', 'running'].includes(j.status)) ?? [];
  const active = activeExports[0];
  const latestExport = value?.jobs?.find((j) => j.kind === 'export');
  const preparation = value?.jobs?.find((j) => j.kind === 'probe' && j.status === 'failed');
  const current = value?.jobs?.find((j) => j.media_id && j.media_id === value?.assembly?.current_media_id);
  const displayedHistory = history?.map((job) => value?.jobs?.find((latest) => latest.id === job.id) ?? job);
  const disabled = readOnly || busy || previewBusy || !!renderRecoveryError;
  const preparedAssembly = useRef<string | null>(null);
  const needsThumbnails = (value?.sources ?? []).some((c) => c.url && !c.filmstrip && c.issue !== 'invalid');
  useEffect(() => {
    const id = value?.assembly?.id;
    if (!id || readOnly || !needsThumbnails || preparedAssembly.current === id) return;
    preparedAssembly.current = id;
    // 已有草稿的初始化只准备缩略图等衍生产物，不同步来源或改变剪辑；读取继续保留待保存输入。
    void api
      .initialize()
      .then(() => {
        setPreparationError('');
        return editor.load();
      })
      .catch(() => setPreparationError('缩略图准备失败，可通过“同步分镜视频”重试。'));
  }, [value?.assembly?.id, readOnly, needsThumbnails, api]);

  async function action(work: () => Promise<void>) {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setNotice('');
    setPreparationError('');
    try {
      await work();
    } catch (cause) {
      if (cause instanceof CreationRecoveryError) loadRenderRecovery();
      setNotice(cause instanceof CreationRecoveryError ? cause.message : errorMessage(cause));
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  }
  async function flushRenderDraft() {
    // 画面与声音各自串行保存，全部确认后才读取版本并冻结合成请求。
    if (!(await editor.flush())) return false;
    const sound = barriers.current.sound;
    if (sound?.hasUnsettled() && !(await sound.flush())) {
      setNotice('声音或字幕尚未保存。请先在声音面板处理保存问题，再合成。');
      return false;
    }
    await editor.load();
    return true;
  }
  async function preview() {
    if (mutation.current || previewJob) return;
    mutation.current = true;
    setPreviewBusy(true);
    setNotice('');
    try {
      if (!(await flushRenderDraft())) return;
      const latest = editor.latest()!;
      if (
        latest.clips?.some((c) => isIncludedVideo(c) && c.is_stale) &&
        !(await confirmAction('部分片段使用此前采用的分镜视频，是否保留当前轨道并合成预览？', {
          className: 'standard-creation-dialog',
        }))
      )
        return;
      const job = await submitRender({
        kind: 'preview',
        body: renderBody(latest, true),
      });
      setPreviewError('');
      setPreviewStatus(job);
      if (job.status === 'succeeded') {
        completedPreviews.current.add(job.id);
        setResult(job);
      } else if (['failed', 'cancelled'].includes(job.status))
        setNotice(job.error?.message ?? '合成预览已取消，可以重新生成。');
      else setPreviewJob(job.id);
      await editor.load();
    } catch (cause) {
      if (cause instanceof CreationRecoveryError) loadRenderRecovery();
      setNotice(cause instanceof CreationRecoveryError ? cause.message : errorMessage(cause));
    } finally {
      mutation.current = false;
      setPreviewBusy(false);
    }
  }
  const restoredPreview = value?.jobs?.find(
    (j) => j.kind === 'preview' && ['queued', 'running'].includes(j.status) && !j.cancel_requested,
  )?.id;
  useEffect(() => {
    if (restoredPreview && !previewJob && !completedPreviews.current.has(restoredPreview))
      setPreviewJob(restoredPreview);
  }, [restoredPreview, previewJob]);
  useEffect(() => {
    if (!previewJob) return;
    let disposed = false;
    let finished = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const job = await api.job(previewJob, controller.signal);
        if (disposed) return;
        setPreviewStatus(job);
        setPreviewError('');
        if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
          finished = true;
          completedPreviews.current.add(job.id);
          if (job.status === 'succeeded') setResult(job);
          else setNotice(job.error?.message ?? '合成预览已取消，可以重新生成。');
          setPreviewJob(null);
          void editor.load().catch(() => { });
        }
      } catch (cause) {
        if (!disposed) setPreviewError(`${errorMessage(cause)} 后台任务仍在运行，正在自动重新连接。`);
      } finally {
        if (!disposed && !finished) timer = setTimeout(() => void poll(), 2000);
      }
    };
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [previewJob, api]);
  async function prepareExport() {
    if (!(await flushRenderDraft())) return;

    setExportOpen(true);
  }
  async function submitExport(acknowledge: boolean) {
    if (!(await flushRenderDraft())) return;
    const latest = editor.latest()!;
    await submitRender({
      kind: 'export',
      body: renderBody(latest, acknowledge),
    });
    setExportOpen(false);
    await editor.load();
  }
  async function openHistory() {
    const page = await api.history();
    setHistory(page.items);
    setMoreHistory(page.has_more);
  }
  async function retry(job: RenderJob) {
    await submitRender({ kind: 'retry', body: {}, job_id: job.id });
    await editor.load();
    if (history) await openHistory();
  }
  function backup() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(editor.latest(), null, 2)], {
        type: 'application/json',
      }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = `assembly-${projectId}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function backupRecovery() {
    try {
      const content = editor.exportRecovered();
      if (!content) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `assembly-${projectId}-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '无法下载剪辑恢复记录。');
    }
  }


  return {
    editor, value, api, clips, included, total, blocked, stale,
    busy, notice, preparationError, disabled, action,
    workspace, focused, setFocusMode, registerSound, soundTarget, setSoundTarget, soundOpen, setSoundOpen,
    renderRecoveryError, damagedRender, pendingRender, backupRenderRecovery, discardRenderRecovery,
    loadRenderRecovery, reconcileRender, backupRecovery, backup,
    previewBusy, previewJob, previewStatus, previewError, setPreviewStatus, preview, result, setResult,
    activeExports, active, latestExport, current, preparation, needsThumbnails, flushRenderDraft,
    applying, setApplying, exportOpen, setExportOpen, prepareExport, submitExport, retry,
    syncOpen, setSyncOpen, history, setHistory, displayedHistory, moreHistory, setMoreHistory, openHistory,
  };
}
