import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { workflowStorage } from '@host/features/identity/storage/account-storage';
import { questionAnswer, questionError, readQuestionDraft, saveQuestionDraft } from '@host/features/assistant/questions/assistant-question-state';
import type { AssistantQuestionAnswer } from '@host/types/assistant/api';
import type { AssistantQuestionCardProps, AssistantQuestionDraft } from '@host/types/assistant/questions/state';
import { useEffect, useRef, useState } from 'react';

export function useAssistantQuestion({ accountId, conversationId, question, disabled, refresh, onResolved }: AssistantQuestionCardProps) {
  const [draft, setDraft] = useState(() => readQuestionDraft(accountId, conversationId, question.operation_id, workflowStorage()));
  const [busy, setBusy] = useState(false), [issue, setIssue] = useState('');
  const [resolved, setResolved] = useState(question.status);
  const [blocked, setBlocked] = useState(false);
  const live = useRef(true), lock = useRef(false);
  const latest = useRef(draft); latest.current = draft;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    if (resolved !== 'pending') onResolved(question.operation_id);
  }, [resolved, question.operation_id, onResolved]);
  const remember = (next: AssistantQuestionDraft) => {
    if (!saveQuestionDraft(accountId, conversationId, question.operation_id, next, workflowStorage())) {
      setIssue('浏览器无法保存回答记录，草稿仍在当前页面。请恢复本机存储后重试。'); return false;
    }
    latest.current = next; setDraft(next); return true;
  };
  useEffect(() => {
    if (question.status === 'pending') return;
    setResolved(question.status);
    if (latest.current.pending) remember({ ...latest.current, pending: null });
  }, [question.status]);
  async function reconcile() {
    const current = await refresh();
    if (!live.current) return;
    if (current) {
      setResolved(current.status);
      if (current.status !== 'pending') {
        remember({ ...latest.current, pending: null }); setIssue('');
      } else if (!current.can_answer || current.question_version !== question.question_version) {
        setBlocked(true); setIssue('问题已处理、停止或版本已变化，当前草稿已保留。');
      } else {
        setBlocked(false); setIssue('问题仍待回答，可使用原请求重试。');
      }
      return;
    }
    // 快照缺项不代表成功；以原 Operation 的事实区分已答、停止与未知。
    const operation = await assistantApi.operation(question.operation_id);
    if (!live.current) return;
    const outcome = operation.result?.outcome;
    if (outcome === 'answered' || outcome === 'skipped') {
      setResolved(outcome); remember({ ...latest.current, pending: null }); setIssue('');
    } else if (operation.error?.code === 'agent_cancelled') {
      setResolved('cancelled'); setBlocked(true); setIssue('问题已停止，草稿已保留。');
    } else { setBlocked(true); setIssue('当前列表未包含此问题，回答结果尚不能确认，草稿与原请求已保留。'); }
  }
  async function check() {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { await reconcile(); }
    catch (cause) { if (live.current) setIssue(`核对未完成：${errorMessage(cause)}`); }
    finally { lock.current = false; if (live.current) setBusy(false); }
  }
  async function submit(outcome: AssistantQuestionAnswer['outcome'], retry = false) {
    if (lock.current || disabled || blocked || !question.can_answer || resolved !== 'pending') return;
    if (!retry && draft.pending) return;
    if (!retry && outcome === 'answered') { const invalid = questionError(question, draft); if (invalid) { setIssue(invalid); return; } }
    if (outcome === 'skipped' && !question.request.allow_skip) return;
    const pending = retry ? draft.pending : { key: crypto.randomUUID(), body: questionAnswer(question, draft, outcome) };
    if (!pending || !remember({ ...draft, pending })) return;
    lock.current = true; setBusy(true); setIssue('');
    try {
      await assistantApi.answerQuestion(question.operation_id, pending.body, pending.key);
      if (!live.current) return;
      setResolved(pending.body.outcome); remember({ ...latest.current, pending: null });
      await refresh();
    } catch (cause) {
      if (!live.current) return;
      if (cause instanceof ApiError && cause.status === 409) {
        setBlocked(true); setIssue('问题已处理、过期或停止，回答草稿已保留。请核对问题状态。');
      } else if (cause instanceof ApiError && cause.status && cause.status < 500 && cause.status !== 408) {
        remember({ ...latest.current, pending: null }); setIssue(errorMessage(cause));
      } else {
        setIssue('回答结果待确认，正在核对同一问题。');
        try { await reconcile(); } catch { if (live.current) setIssue('回答结果待确认，草稿与原请求已保留。'); }
      }
    } finally { lock.current = false; if (live.current) setBusy(false); }
  }
  return { draft, busy, issue, resolved, blocked, submit, check,
    edit: (patch: Partial<AssistantQuestionDraft>) => remember({ ...draft, ...patch }),
    editable: !disabled && question.can_answer && resolved === 'pending' && !blocked && !draft.pending && !busy };
}
