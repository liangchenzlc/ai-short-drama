import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Skeleton } from 'antd';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantUndoPreview } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { workflowStorage } from '../auth/account-storage';
import { clearUndoDraft, readUndoDraft, saveUndoDraft, type UndoDraft } from './assistant-undo-draft';

export function AssistantBatchUndo({ accountId, conversationId, operationId, disabled, accessEnded, onClose, onBusy, onBlocked, onUpdated }: {
  accountId: string; conversationId: string; operationId: string | null; disabled: boolean; accessEnded: boolean;
  onClose: () => void; onBusy: (value: boolean) => void; onBlocked: (value: boolean) => void; onUpdated: () => void;
}) {
  const storage = workflowStorage();
  const [restored] = useState(() => readUndoDraft(accountId, conversationId, storage));
  const [draft, setDraft] = useState<UndoDraft | null>(restored.draft);
  const [target, setTarget] = useState<string | null>(restored.draft?.operationId ?? null);
  const [error, setError] = useState(restored.error);
  const [damaged, setDamaged] = useState(!!restored.error);
  const [preview, setPreview] = useState<AssistantUndoPreview | null>(null);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0);
  const alive = useRef(true), sending = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  useEffect(() => { onBlocked(!!draft?.pending || damaged); }, [draft?.pending, damaged, onBlocked]);
  useEffect(() => {
    if (!operationId || accessEnded) return;
    setTarget(draft?.operationId ?? operationId);
    if (draft && draft.operationId !== operationId) setError('原撤销选择已保留，请先处理后再撤销其他批次。');
  // A new explicit entry must not reset a saved selection.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operationId, accessEnded]);
  useEffect(() => {
    if (accessEnded) { clearUndoDraft(accountId, conversationId, storage); setDraft(null); setTarget(null); setDamaged(false); setError(''); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessEnded, accountId, conversationId]);
  useEffect(() => {
    if (!target || accessEnded) return;
    const controller = new AbortController(); setLoading(true); setPreview(null);
    void assistantApi.undoPreview(target, controller.signal).then(value => {
      if (!controller.signal.aborted && alive.current) setPreview(value);
    }).catch(cause => { if (!controller.signal.aborted && alive.current) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted && alive.current) setLoading(false); });
    return () => controller.abort();
  }, [target, revision, accessEnded]);
  function persist(next: UndoDraft) {
    if (!saveUndoDraft(accountId, conversationId, next, storage)) { setError('撤销选择未能保存，请核对本机存储后重试。'); return false; }
    setDraft(next); return true;
  }
  function choose(keys: string[]) {
    if (!target || !preview || draft?.pending || damaged) return;
    persist({ version: 1, operationId: target, key: crypto.randomUUID(), pending: false, body: { preview_hash: preview.preview_hash, selected_keys: keys } });
  }
  function close() { setTarget(null); onClose(); }
  function discard() {
    if (!clearUndoDraft(accountId, conversationId, storage)) { setError('本机撤销记录未能清除。'); return; }
    setDraft(null); setDamaged(false); setError(''); close();
  }
  async function submit() {
    if (!target || !draft || damaged || disabled || sending.current || !draft.body.selected_keys.length || !draft.pending && (!preview || draft.body.preview_hash !== preview.preview_hash)) return;
    const original = { ...draft, pending: true };
    if (!persist(original)) return;
    sending.current = true; setBusy(true); setError('');
    try {
      await assistantApi.undoBatch(original.operationId, original.body, original.key);
      if (!alive.current) return;
      if (clearUndoDraft(accountId, conversationId, storage, original.key)) { setDraft(null); setRevision(value => value + 1); onUpdated(); }
      else setError('撤销已受理，本机记录尚未清除，请使用原请求核对。');
    } catch (cause) {
      if (!alive.current) return;
      if (cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500) persist({ ...original, pending: false });
      setError(errorMessage(cause));
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  }
  const changed = !!draft && !!preview && draft.body.preview_hash !== preview.preview_hash;
  const selected = draft?.body.selected_keys ?? [];
  return <>
    {!target && (draft || damaged) && <StudioFeedback type="warning" persistent noticeKey={`assistant-undo-pending:${conversationId}`} title="撤销待处理" message={damaged ? error : '原批次与撤销选择已保留。'} actions={draft ? <Button size="small" onClick={() => setTarget(draft.operationId)}>打开原撤销</Button> : <Button size="small" onClick={discard}>明确清除损坏记录</Button>}/>}
    {target && <Dialog title="撤销操作批次" className="standard-creation-dialog standard-assistant-dialog assistant-undo-dialog" canClose={!busy} onClose={close}>
      {error && <StudioFeedback type="error" persistent noticeKey={`assistant-undo:${conversationId}`} title="撤销未完成" message={error} actions={<Button size="small" disabled={busy} onClick={() => setRevision(value => value + 1)}>重新核对预览</Button>}/>}
      <div className="assistant-undo-body">
        <p>逐项选择要撤销的内容。后续编辑、引用或媒体变化受到保护；停止和改向保留成果。已发生的生成费用不会退回。</p>
        <small>原操作批次 {target}</small>
        {loading ? <Skeleton active paragraph={{ rows: 4 }}/> : preview ? <>
          {!preview.items.length ? <p>此批次没有可撤销的结构变更。</p> : <ul aria-label="撤销范围">{preview.items.map(item => <li key={item.key}>
            <Checkbox checked={selected.includes(item.key)} disabled={busy || disabled || !!draft?.pending || damaged || changed || !item.allowed} onChange={event => choose(event.target.checked ? [...selected, item.key] : selected.filter(key => key !== item.key))}>{item.name}</Checkbox>
            <span>{item.allowed ? '可安全撤销' : item.reason}</span>
            {!!item.requires.length && <small>须同时选择：{item.requires.map(key => preview.items.find(value => value.key === key)?.name ?? key).join('、')}</small>}
          </li>)}</ul>}
          {!!preview.receipts.length && <section aria-label="已保存的撤销回执"><h4>撤销记录</h4>{preview.receipts.map(item => <p key={item.id}>已撤销 {item.selected_keys.length} 项 · 回执 {item.id}</p>)}</section>}
        </> : <div role="alert"><p>未能读取撤销预览，原选择仍保留。</p><Button onClick={() => setRevision(value => value + 1)}>重新读取预览</Button></div>}
        {changed && <StudioFeedback type="warning" persistent noticeKey={`assistant-undo-version:${target}`} title="撤销范围已有变化" message="原选择已保留，请核对当前安全项与冲突项后明确采用新预览。" actions={<Button size="small" disabled={busy || !!draft?.pending} onClick={() => { if (preview) choose(selected.filter(key => preview.items.some(item => item.key === key && item.allowed))); setError(''); }}>采用新预览</Button>}/>}
      </div>
      <footer className="standard-assistant-dialog-footer">
        <Button disabled={busy} onClick={close}>关闭并保留</Button>
        {draft && !draft.pending && <Button disabled={busy} onClick={discard}>舍弃撤销选择</Button>}
        <Button danger type="primary" aria-label={draft?.pending ? '使用原撤销请求核对' : '撤销所选内容'} loading={busy} disabled={disabled || accessEnded || loading || damaged || !selected.length || !draft?.pending && (changed || !preview)} onClick={() => void submit()}>{draft?.pending ? '使用原撤销请求核对' : `撤销所选 ${selected.length} 项`}</Button>
      </footer>
    </Dialog>}
  </>;
}
