import { ApiError } from '@host/api/http';
import {
beginRenderAttempt,
definiteRenderRejection,
discardDamagedRenderAttempt,
finishRenderAttempt,
readRenderAttempt,
readRenderRecoveryRecord,
} from '@host/features/creation/assembly/sessions/render-recovery';
import { creationScope } from '@host/features/creation/shared/recovery/creation-recovery';
import { confirmAction } from '@host/shared/ui/confirm';
import type { RenderJob } from '@host/types/creation/assembly/document';
import type { RenderAttempt } from '@host/types/creation/assembly/sessions/render-recovery';
import { useCallback,useEffect,useRef,useState } from 'react';

import type { AssemblySubmitRender } from '@host/types/creation/assembly/export/recovery';
export function useRenderRecovery(
  projectId: string,
  submit: AssemblySubmitRender,
  setNotice: (message: string) => void,
) {
  const renderScope = useRef(creationScope(`assembly:${projectId}:render`)).current;
  const [pendingRender, setPendingRender] = useState<RenderAttempt | null>(null);
  const [damagedRender, setDamagedRender] = useState<string | null>(null);
  const [renderRecoveryError, setRenderRecoveryError] = useState('');
  const loadRenderRecovery = useCallback(() => {
    try {
      const record = readRenderRecoveryRecord(renderScope);
      setPendingRender(record.attempt);
      setDamagedRender(record.damaged);
      setRenderRecoveryError(record.damaged !== null ? '本机合成请求记录损坏，已阻止新请求，完整原文仍保留。' : '');
    } catch (cause) {
      setPendingRender(null);
      setDamagedRender(null);
      setRenderRecoveryError(cause instanceof Error ? cause.message : '无法读取本机合成请求恢复记录。');
    }
  }, [renderScope]);
  useEffect(loadRenderRecovery, [loadRenderRecovery]);
  function readPendingRender() {
    // 核对前重读本机记录，避免使用另一窗口已经处理过的旧快照。
    const attempt = readRenderAttempt(renderScope);
    if (!attempt) setPendingRender(null);
    return attempt;
  }
  async function submitRender(operation: Omit<RenderAttempt, 'format' | 'key'>) {
    // 先持久化请求键和固定载荷。网络中断后沿用这份记录核对受理结果，不能另发一次合成。
    const attempt = beginRenderAttempt(renderScope, operation);
    setPendingRender(attempt);
    let job: RenderJob;
    try {
      job = await submit(attempt.kind, attempt.body, attempt.key, attempt.job_id);
    } catch (cause) {
      // 只有明确拒绝受理才释放记录；超时或连接断开都保留原请求。
      if (cause instanceof ApiError && definiteRenderRejection(cause.code, cause.status)) {
        finishRenderAttempt(renderScope, attempt);
        setPendingRender(null);
      }
      throw cause;
    }
    finishRenderAttempt(renderScope, attempt);
    setPendingRender(null);
    return job;
  }

  function backupRenderRecovery() {
    if (damagedRender === null) return;
    try {
      const url = URL.createObjectURL(new Blob([damagedRender], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `assembly-${projectId}-render-recovery.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '无法下载合成请求恢复记录。');
    }
  }
  async function discardRenderRecovery() {
    const raw = damagedRender;
    if (
      raw === null ||
      !(await confirmAction(
        '请先下载原请求记录，并检查当前作品的导出和预览记录。清除后将失去原请求编号的核对依据；若后台已受理，再发起合成可能重复制作。确认已核对并清除这条损坏的本机记录？',
        {
          title: '清除损坏的合成请求记录',
          confirmText: '已核对，清除记录',
          danger: true,
          className: 'standard-creation-dialog',
        },
      ))
    )
      return;
    discardDamagedRenderAttempt(renderScope, raw);
    loadRenderRecovery();
  }

  return {
    pendingRender,
    damagedRender,
    renderRecoveryError,
    loadRenderRecovery,
    readPendingRender,
    submitRender,
    backupRenderRecovery,
    discardRenderRecovery,
  };
}
