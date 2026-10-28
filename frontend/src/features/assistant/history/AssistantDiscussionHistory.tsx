import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { AssistantReply } from '@host/features/assistant/messages/AssistantMessage';
import {
  clearDiscussionDraft,
  readDiscussionDraft,
  saveDiscussionDraft,
} from '@host/features/assistant/recovery/assistant-history-draft';
import { workflowStorage } from '@host/features/identity/storage/account-storage';
import { Dialog } from '@host/shared/ui/Dialog';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { DiscussionEligibility, DiscussionRevision } from '@host/types/assistant/api';
import type { HistoryRequest, Props } from '@host/types/assistant/history/AssistantDiscussionHistory';
import type { DiscussionDraft } from '@host/types/assistant/recovery/assistant-history-draft';
import { Button, Input, Skeleton } from 'antd';
import { useEffect, useRef, useState } from 'react';
/** 修订记录只显示用户看得懂的时间，不暴露内部版本号与运行 ID。 */
function formatRevisionTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
}
export function AssistantDiscussionHistory({
  accountId,
  conversationId,
  request,
  disabled,
  accessEnded,
  onClose,
  onAccepted,
  onBlocked,
  onBusy,
  onCorrect,
}: Props) {
  const storage = workflowStorage();
  const [restored] = useState(() => readDiscussionDraft(accountId, conversationId, storage));
  const [draft, setDraft] = useState<DiscussionDraft | null>(restored.draft);
  const [target, setTarget] = useState<{
    id: string;
    mode: HistoryRequest['mode'];
  } | null>(restored.draft ? { id: restored.draft.messageId, mode: restored.draft.body.kind } : null);
  const [error, setError] = useState(restored.error);
  const [damaged, setDamaged] = useState(!!restored.error);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [eligibility, setEligibility] = useState<DiscussionEligibility | null>(null);
  const [items, setItems] = useState<DiscussionRevision[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState(0);
  const alive = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onBlocked(!!draft?.pending || damaged);
  }, [draft?.pending, damaged, onBlocked]);
  useEffect(() => {
    onBusy(busy);
    return () => onBusy(false);
  }, [busy, onBusy]);
  function persist(next: DiscussionDraft) {
    if (!saveDiscussionDraft(accountId, conversationId, next, storage)) {
      setError('修订草稿未能保存，请保留输入并核对浏览器存储。');
      return false;
    }
    setDraft(next);
    return true;
  }
  useEffect(() => {
    if (!request || accessEnded) return;
    if (
      draft &&
      (draft.messageId !== request.message.id || (request.mode !== 'history' && draft.body.kind !== request.mode))
    ) {
      setTarget({ id: draft.messageId, mode: draft.body.kind });
      setError('已恢复原修订草稿，请先完成或舍弃后再修订其他消息。');
      return;
    }
    setTarget({ id: request.message.id, mode: request.mode });
    setOffset(0);
    setEligibility(null);
    setItems([]);
    if (request.mode !== 'history' && !draft && !damaged)
      persist({
        version: 1,
        messageId: request.message.id,
        key: crypto.randomUUID(),
        pending: false,
        body: {
          kind: request.mode,
          row_version: request.message.row_version ?? '1',
          ...(request.mode === 'edit' ? { content: request.message.content } : {}),
        },
      });
    // The clicked request is an explicit action; subsequent draft edits must not reset it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, accessEnded]);
  useEffect(() => {
    if (accessEnded) {
      clearDiscussionDraft(accountId, conversationId, storage);
      setDraft(null);
      setTarget(null);
      setError('');
      setDamaged(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessEnded, accountId, conversationId]);
  useEffect(() => {
    if (!target || accessEnded) return;
    const controller = new AbortController();
    setLoading(true);
    setItems([]);
    void Promise.allSettled([
      target.mode === 'history'
        ? Promise.resolve(null)
        : assistantApi.discussionEligibility(conversationId, target.id, controller.signal),
      assistantApi.discussionRevisions(conversationId, target.id, offset, controller.signal),
    ])
      .then(([allowed, history]) => {
        if (controller.signal.aborted || !alive.current) return;
        if (allowed.status === 'fulfilled') setEligibility(allowed.value);
        else setError(errorMessage(allowed.reason));
        if (history.status === 'fulfilled') {
          setItems(history.value.items);
          setTotal(history.value.total);
        } else setError(errorMessage(history.reason));
      })
      .finally(() => {
        if (!controller.signal.aborted && alive.current) setLoading(false);
      });
    return () => controller.abort();
  }, [target, offset, revision, conversationId, accessEnded]);
  function close() {
    setTarget(null);
    onClose();
  }
  function discard() {
    if (!clearDiscussionDraft(accountId, conversationId, storage)) {
      setError('本机记录未能清除，请核对浏览器存储。');
      return;
    }
    setDraft(null);
    setDamaged(false);
    setError('');
    close();
  }
  async function submit() {
    if (
      !draft ||
      damaged ||
      disabled ||
      accessEnded ||
      submitting.current ||
      (!draft.pending && (!eligibility?.allowed || eligibility.row_version !== draft.body.row_version))
    )
      return;
    const original = { ...draft, pending: true };
    if (!persist(original)) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await assistantApi.reviseDiscussion(
        conversationId,
        original.messageId,
        original.body,
        original.key,
      );
      if (!alive.current) return;
      onAccepted(result);
      if (clearDiscussionDraft(accountId, conversationId, storage, original.key)) {
        setDraft(null);
        close();
      } else setError('修订已受理，但本机记录尚未清除，请继续使用原请求核对。');
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500)
        persist({ ...original, pending: false });
      setError(
        cause instanceof ApiError && cause.code === 'assistant_history_dependency'
          ? '此讨论已被后续创作使用，请发送新消息纠正。'
          : errorMessage(cause),
      );
    } finally {
      submitting.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const changed = eligibility && draft && eligibility.row_version !== draft.body.row_version;
  const feedback = error ? (
    <StudioFeedback
      scope="chat"
      type="error"
      persistent
      title="历史讨论"
      message={error}
      noticeKey={`assistant-history:${conversationId}`}
      actions={
        <Button size="small" disabled={busy} onClick={() => setRevision((value) => value + 1)}>
          重新核对
        </Button>
      }
    />
  ) : null;
  return (
    <>
      {!target && (draft || damaged) && (
        <StudioFeedback
          scope="chat"
          type={draft?.pending || damaged ? 'warning' : 'info'}
          persistent
          title={draft?.pending ? '历史修订待核对' : '历史修订草稿'}
          message={damaged ? error : '原输入与请求已保留。'}
          noticeKey={`assistant-history-pending:${conversationId}`}
          actions={
            draft ? (
              <Button size="small" onClick={() => setTarget({ id: draft.messageId, mode: draft.body.kind })}>
                打开原修订
              </Button>
            ) : (
              <Button size="small" onClick={discard}>
                明确清除损坏记录
              </Button>
            )
          }
        />
      )}
      {target && (
        <Dialog
          title={target.mode === 'history' ? '讨论修订记录' : target.mode === 'edit' ? '编辑讨论' : '重新回答'}
          className="standard-creation-dialog standard-assistant-dialog assistant-discussion-dialog"
          canClose={!busy}
          onClose={close}
        >
          {feedback}
          <div className="assistant-discussion-body">
            <p className="assistant-discussion-help">
              仅更新这段纯讨论的回复，保留原修订记录。已被后续创作使用的历史请用新消息纠正。
            </p>
            {loading ? (
              <Skeleton active paragraph={{ rows: 3 }} />
            ) : (
              <>
                {target.mode !== 'history' && eligibility && !eligibility.allowed && !draft?.pending && (
                  <StudioFeedback
                    scope="chat"
                    type="warning"
                    persistent
                    title="保留执行依据"
                    message={
                      eligibility.code === 'assistant_history_dependency'
                        ? '此讨论已被后续创作使用，请发送新消息纠正。'
                        : (eligibility.reason ?? '此历史不能改写，请发送新消息纠正。')
                    }
                    noticeKey={`assistant-history-protected:${target.id}`}
                    actions={
                      <Button
                        size="small"
                        onClick={() => {
                          onCorrect(draft?.body.content ?? request?.message.content ?? '');
                          discard();
                        }}
                      >
                        用新消息纠正
                      </Button>
                    }
                  />
                )}
                {target.mode === 'edit' && draft?.body.kind === 'edit' && (
                  <label>
                    讨论消息
                    <Input.TextArea
                      aria-label="修订讨论正文"
                      autoFocus
                      autoSize={{ minRows: 5, maxRows: 14 }}
                      maxLength={32000}
                      value={draft.body.content}
                      disabled={busy || draft.pending || damaged}
                      onChange={(event) =>
                        persist({
                          ...draft,
                          body: { ...draft.body, content: event.target.value },
                        })
                      }
                    />
                  </label>
                )}
                {target.mode === 'regenerate' && <p>保留用户原文，成功后替换原回复；失败时继续保留旧回复。</p>}
                {changed && (
                  <details>
                    <summary>核对当前消息</summary>
                    <p style={{ whiteSpace: 'pre-wrap' }}>{eligibility.current_content}</p>
                  </details>
                )}
                {changed && !draft?.pending && (
                  <StudioFeedback
                    scope="chat"
                    type="warning"
                    persistent
                    title="消息已有新版本"
                    message="修订输入已保留，请核对当前消息后明确采用新版本。"
                    noticeKey={`assistant-history-version:${target.id}`}
                    actions={
                      <Button
                        size="small"
                        onClick={() => {
                          if (
                            draft &&
                            eligibility &&
                            persist({
                              ...draft,
                              key: crypto.randomUUID(),
                              body: {
                                ...draft.body,
                                row_version: eligibility.row_version,
                              },
                            })
                          )
                            setError('');
                        }}
                      >
                        采用当前消息版本
                      </Button>
                    }
                  />
                )}
                <section aria-label="历史修订记录">
                  <h4>修订记录</h4>
                  {!items.length ? (
                    <p className="assistant-discussion-help">尚无历史修订。</p>
                  ) : (
                    items.map((item) => (
                      <details key={item.id}>
                        <summary>
                          {item.kind === 'edit' ? '编辑前' : '重答前'} · {formatRevisionTime(item.created_at)}
                        </summary>
                        <AssistantReply text={item.content} />
                      </details>
                    ))
                  )}
                  {total > 30 && (
                    <nav aria-label="修订记录分页">
                      <Button
                        size="small"
                        disabled={offset === 0}
                        onClick={() => setOffset((value) => Math.max(0, value - 30))}
                      >
                        上一页
                      </Button>
                      <span>{total} 次修订</span>
                      <Button
                        size="small"
                        disabled={offset + items.length >= total}
                        onClick={() => setOffset((value) => value + 30)}
                      >
                        下一页
                      </Button>
                    </nav>
                  )}
                </section>
              </>
            )}
          </div>
          <footer className="standard-assistant-dialog-footer">
            <Button disabled={busy} onClick={close}>
              关闭并保留
            </Button>
            {draft && !draft.pending && (
              <Button disabled={busy} onClick={discard}>
                舍弃修订草稿
              </Button>
            )}
            {target.mode !== 'history' && draft && (
              <Button
                type="primary"
                aria-label={
                  draft.pending ? '使用原请求核对' : draft.body.kind === 'edit' ? '保存并重新回答' : '重新回答这段讨论'
                }
                loading={busy}
                disabled={
                  disabled ||
                  accessEnded ||
                  loading ||
                  damaged ||
                  (!draft.pending &&
                    (!eligibility?.allowed || !!changed || (draft.body.kind === 'edit' && !draft.body.content?.trim())))
                }
                onClick={() => void submit()}
              >
                {draft.pending ? '使用原请求核对' : draft.body.kind === 'edit' ? '保存并重新回答' : '重新回答这段讨论'}
              </Button>
            )}
          </footer>
        </Dialog>
      )}
    </>
  );
}
