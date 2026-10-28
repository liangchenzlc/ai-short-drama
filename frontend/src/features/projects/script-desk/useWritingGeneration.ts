import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { generations } from '../../../api/modules/generations';
import { errorMessage } from '../../../api/http';
import type { WritingTask } from '../../../api/modules/episode-writing';
import type { GenerationReceipt, GenerationSummary, TextGenerationRequest } from '../../../api/types/generations';
import { useStudioMessage } from '../../../components/ui/StudioFeedback';
import { confirmAction } from '../../../components/ui/confirm';
import { creationScope, clearRecovery, readRecovery, readRecoveryText, saveRecovery } from '../creation-recovery';
import type { WritingSession } from '../writing-session';
import type { NavigationBarrier } from '../writing-navigation';
import type { WritingTarget } from './writing-requests';

interface WritingAttempt { format: 1; key: string; target: WritingTarget; body: TextGenerationRequest }
function isWritingAttempt(value: unknown): value is WritingAttempt {
  if (!value || typeof value !== 'object') return false;
  const attempt = value as Partial<WritingAttempt>;
  const source = attempt.body?.source;
  return attempt.format === 1 && typeof attempt.key === 'string' && /^[\da-f-]{36}$/i.test(attempt.key)
    && ['script', 'outline'].includes(attempt.target ?? '') && !!source && ['story_outline', 'script_generate', 'script_rewrite'].includes(source.scene)
    && 'project_id' in source && typeof source.project_id === 'string' && /^\d+$/.test(source.project_id);
}

export function useWritingGeneration(projectId: string, session: WritingSession, accountId?: string, registerBarrier?: (barrier: NavigationBarrier | null) => void) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const scope = creationScope(`writing-generation:${projectId}`, accountId);
  const [attempt, setAttempt] = useState<WritingAttempt | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [lastTask, setLastTask] = useState<GenerationSummary | null>(null);
  const active = useRef(true);
  const sending = useRef(false);
  const message = useStudioMessage();
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const pending = useRef(false); pending.current = !!attempt || blocked || submitting;
  useEffect(() => {
    registerBarrier?.({ hasUnsettled: () => pending.current, flush: async () => !pending.current });
    return () => registerBarrier?.(null);
  }, [registerBarrier]);
  useEffect(() => {
    try {
      const stored = readRecovery(scope, isWritingAttempt);
      const source = stored?.body.source;
      if (source && (!('project_id' in source) || source.project_id !== projectId)) throw new Error('生成恢复记录不属于当前作品。');
      setAttempt(stored);
      if (stored) setError('有一笔生成请求尚待核对，请先核对原任务。');
    } catch { setBlocked(true); setError('本机生成请求记录无法读取。请下载原记录，并核对当前作品的生成状态和已保存内容后清理记录。'); }
  }, [scope, projectId]);

  function accept(receipt: GenerationReceipt, original: WritingAttempt) {
    if (!active.current) return;
    const scene = original.body.source?.scene;
    if (scene !== 'story_outline' && scene !== 'script_generate' && scene !== 'script_rewrite') return;
    if (receipt.status === 'queued' || receipt.status === 'running') session.trackTask({ task_id: receipt.generation_id, target: original.target, operation: scene, status: receipt.status });
    void session.refreshFromServer();
    clearRecovery(scope); setAttempt(null); setBlocked(false); setError('');
  }

  async function send(original: WritingAttempt) {
    setSubmitting(true);
    try {
      const receipt = await generations.generateText(original.body, original.key);
      accept(receipt, original);
      return true;
    } catch (cause) {
      if (!active.current) return false;
      const status = (cause as { status?: number }).status;
      if (status && status < 500) { clearRecovery(scope); setAttempt(null); }
      setError(status && status < 500 ? errorMessage(cause) : '生成请求的受理结果尚未明确。请核对原任务，避免重复生成。');
      if (status === 409) await session.refreshFromServer();
      return false;
    } finally { if (active.current) setSubmitting(false); }
  }

  async function submit(target: WritingTarget, build: () => TextGenerationRequest) {
    if (sending.current || attempt || blocked) return false;
    sending.current = true; setSubmitting(true); setError('');
    try {
      if (!await session.flush()) {
        setError('正文尚未保存成功，请先处理保存提示。');
        return false;
      }
      const original: WritingAttempt = { format: 1, key: crypto.randomUUID(), target, body: build() };
      saveRecovery(scope, original);
      setAttempt(original);
      return await send(original);
    } catch (cause) { if (active.current) setError(errorMessage(cause)); return false; }
    finally { sending.current = false; if (active.current) setSubmitting(false); }
  }

  async function resolve(resend = false) {
    if (!attempt || submitting) return;
    setSubmitting(true);
    try {
      const found = await generations.lookup('text', attempt.key);
      if (!active.current) return;
      if (found) accept(found, attempt);
      else if (resend) await send(attempt); // Same frozen request and idempotency key.
      else setError('暂未查到原任务。可以稍后核对，或按原请求重试；不会创建重复任务。');
    } catch (cause) { if (active.current) setError(errorMessage(cause)); }
    finally { if (active.current) setSubmitting(false); }
  }

  function downloadRecovery() {
    try {
      const raw = readRecoveryText(scope);
      if (raw === null) throw new Error('未找到本机生成请求记录，请核对浏览器存储权限。');
      const url = URL.createObjectURL(new Blob([raw], { type: 'application/json;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = `writing-request-${projectId}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (cause) { setError(errorMessage(cause)); }
  }
  async function clearBlocked() {
    if (!blocked || submitting) return;
    if (!await confirmAction('请先下载原请求记录，并核对当前作品的生成状态和已保存内容。清理只移除本机记录，已受理的任务仍会继续。', { title: '清理损坏的请求记录', confirmText: '已核对，清理记录', className: 'standard-creation-dialog' })) return;
    setSubmitting(true);
    try {
      if (!await session.refreshFromServer()) throw new Error('无法核对服务端状态，请恢复连接后重试。');
      const raw = readRecoveryText(scope);
      if (raw !== null) saveRecovery(`${scope}:previous`, { raw });
      clearRecovery(scope); setAttempt(null); setBlocked(false); setError('');
    } catch (cause) { setError(errorMessage(cause)); }
    finally { if (active.current) setSubmitting(false); }
  }

  const taskIds = state.activeWritingTasks.map(task => task.task_id).sort().join(',');
  const reported = useRef(new Set<string>());
  useEffect(() => {
    if (!taskIds) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const tasks = await Promise.all(taskIds.split(',').map(id => generations.detail(id, controller.signal)));
        if (controller.signal.aborted) return;
        for (const task of tasks) {
          if (task.status === 'queued' || task.status === 'running' || reported.current.has(task.generation_id)) continue;
          if (!await session.refreshFromServer()) continue;
          reported.current.add(task.generation_id); setLastTask(task);
          if (task.status === 'succeeded') message.success('创作内容已更新', `writing:${task.generation_id}`);
          else if (task.status === 'failed') setError(task.error?.message ?? 'AI 修改未完成，原正文已保留。');
        }
      } catch (cause) { if (!controller.signal.aborted) setError(`无法读取生成进度：${errorMessage(cause)}`); }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 1500);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [taskIds, session, message]);

  const stop = useCallback(async (task: WritingTask) => {
    if (stopping) return;
    setStopping(task.task_id);
    try { await generations.cancel(task.task_id); await session.refreshFromServer(); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { if (active.current) setStopping(null); }
  }, [session, stopping]);
  async function resume() {
    if (!lastTask || submitting) return;
    setSubmitting(true);
    try { await generations.resume(lastTask.generation_id); await session.refreshFromServer(); setLastTask(null); setError(''); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { if (active.current) setSubmitting(false); }
  }
  return { submit, submitting, pendingTarget: attempt?.target, uncertain: !!attempt, blocked, error, setError, resolve, downloadRecovery, clearBlocked, stop, stopping, lastTask, resume };
}
