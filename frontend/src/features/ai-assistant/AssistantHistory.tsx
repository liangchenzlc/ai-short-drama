import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Segmented, Skeleton } from 'antd';
import { Conversations } from '@ant-design/x';
import { Ellipsis } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentConversation } from '../../api/types/agents';
import type { GlobalAssistantConversation } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { AssistantDeleteDialog } from './AssistantDeleteDialog';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { workflowStorage } from '../auth/account-storage';
import { clearAssistantPendingAction, readAssistantPendingAction, saveAssistantPendingAction, type AssistantPendingAction } from './assistant-draft';

export function AssistantHistory({ accountId, currentId, onSelect, onLegacy, onNew, onClose, onUpdated, onDeleted, layout }: {
  accountId: string; currentId: string; onSelect: (id: string) => void;
  onLegacy: (id: string, projectId: string) => void; onNew: () => Promise<void>; onClose: () => void;
  onUpdated: (conversation: GlobalAssistantConversation) => void; onDeleted: (id: string) => void;
  layout?: 'standard';
}) {
  const [legacy, setLegacy] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [clearMemory, setClearMemory] = useState(false);
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<(AgentConversation | GlobalAssistantConversation)[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [renaming, setRenaming] = useState<GlobalAssistantConversation | null>(null);
  const [deleting, setDeleting] = useState<GlobalAssistantConversation | null>(null);
  const [title, setTitle] = useState('');
  const [restoredDeletion] = useState(() => readAssistantPendingAction(accountId, 'history', 'delete', workflowStorage()));
  const [pendingDeletion, setPendingDeletion] = useState<Extract<AssistantPendingAction, { kind: 'delete' }> | null>(restoredDeletion.pending?.kind === 'delete' ? restoredDeletion.pending : null);
  const alive = useRef(true);
  const locked = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    const request = legacy ? assistantApi.globalHistory(offset, controller.signal, search)
      : assistantApi.globalConversations(offset, controller.signal, search);
    request.then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [legacy, offset, search, revision]);
  async function mutate(operation: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await operation(); if (alive.current) setRevision(value => value + 1); }
    catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  async function deleteConversation(request: Extract<AssistantPendingAction, { kind: 'delete' }>, retry = false) {
    const storage = workflowStorage();
    if (!saveAssistantPendingAction(accountId, 'history', request, storage)) throw new ApiError('浏览器无法保存删除核对记录，本次尚未发送。请允许本机存储后重试。', 'assistant_recovery_unavailable');
    setPendingDeletion(request);
    const scope = `assistant-delete:${request.targetId}`;
    try {
      const result = await assistantApi.deleteGlobalConversation(request.targetId, request.body.row_version, request.body.clear_derived_memory, request.key, request.body.memory_selections);
      const cleared = clearAssistantPendingAction(accountId, 'history', 'delete', request.key, storage);
      if (cleared) clearAttempt(scope, attemptStorage());
      if (!alive.current) return;
      setPendingDeletion(cleared ? null : request);
      if (!cleared) setError('删除已受理，但本机记录尚未清理。请使用原请求核对。');
      if (result.status === 'deleted') onDeleted(request.targetId); else onUpdated(result);
    } catch (cause) {
      if (!retry && cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500
        && clearAssistantPendingAction(accountId, 'history', 'delete', request.key, storage)) {
        clearAttempt(scope, attemptStorage()); if (alive.current) setPendingDeletion(null);
      }
      throw cause;
    }
  }
  return <><Dialog title="对话记录" className={`ai-assistant-history-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} canClose={!busy} onClose={onClose}>
    <div className="ai-assistant-history-body">
      <p className="ai-assistant-meta">账号对话仅你可见，切换作品不会改变任务目标。</p>
      <Segmented aria-label="对话记录类型" value={legacy ? 'legacy' : 'chat'} disabled={busy} options={[{ value: 'chat', label: '全局对话' }, { value: 'legacy', label: '历史只读对话' }]} onChange={value => { setLegacy(value === 'legacy'); setOffset(0); setRenaming(null); }}/>
      <form className="ai-assistant-history-search" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}><Input aria-label="搜索对话" placeholder="搜索对话标题" value={query} disabled={busy} onChange={event => setQuery(event.target.value)}/><Button htmlType="submit" disabled={busy}>搜索</Button></form>
      {!legacy ? <Checkbox checked={clearMemory} disabled={busy} onChange={event => setClearMemory(event.target.checked)}>删除时查看可一并清理的账号和作品记忆</Checkbox> : <p className="ai-assistant-meta">旧对话仅供查看，请新建全局任务继续创作。</p>}
      {error && <StudioFeedback type="error" title="对话记录" message={error} noticeKey="assistant-history" actions={<Button size="small" onClick={() => setRevision(value => value + 1)}>重新读取</Button>}/>}
      {restoredDeletion.error && <StudioFeedback type="error" persistent title="删除记录无法恢复" message={restoredDeletion.error} noticeKey="assistant-delete-corrupt"/>}
      {pendingDeletion && !busy && <StudioFeedback type="warning" persistent title="删除结果待核对" message="删除结果尚未确认，原请求与记忆清理选择已保留。" noticeKey={`assistant-delete:${pendingDeletion.targetId}`} actions={<Button size="small" onClick={() => void mutate(() => deleteConversation(pendingDeletion, true))}>使用原请求核对删除</Button>}/>}
      {loading ? <Skeleton active paragraph={{ rows: 4 }}/> : !items.length && !error ? <p className="ai-assistant-history-empty">{search ? '没有匹配的对话' : legacy ? '没有历史创作记录' : '还没有对话记录'}</p> : <Conversations className="ai-assistant-history-list" aria-label={legacy ? '历史创作记录列表' : '助手对话列表'} activeKey={currentId} items={items.map(item => ({
        key: item.id,
        disabled: busy,
        className: item.id === currentId ? 'is-current' : '',
        label: <button className="ai-assistant-history-entry" type="button" disabled={busy} aria-current={item.id === currentId ? 'true' : undefined}><strong>{item.title || '新对话'}</strong><p>{item.last_message_preview || '暂无消息'}</p><span>{new Date(item.updated_at).toLocaleString()}{'status' in item && item.status === 'deleting' ? ' · 当前工具结束后删除' : item.id === currentId ? ' · 当前对话' : ''}</span></button>,
      }))} onActiveChange={id => { if (busy) return; const item = items.find(item => item.id === id); if (legacy && item?.project_id) onLegacy(id, item.project_id); else if (!legacy && id !== currentId) onSelect(id); onClose(); }} menu={legacy ? undefined : conversation => {
        const item = items.find(value => value.id === conversation.key) as GlobalAssistantConversation | undefined;
        if (!item || item.status === 'deleting') return undefined;
        return { className: layout === 'standard' ? 'standard-assistant-menu standard-creation-menu standard-creation-popup' : undefined, getPopupContainer: layout === 'standard' ? (trigger: HTMLElement) => trigger.closest('dialog') ?? trigger.parentElement ?? document.body : undefined, items: [
          { key: 'rename', label: '重命名', onClick: () => { setRenaming(item); setTitle(item.title); } },
          { key: 'delete', label: '删除', danger: true, disabled: !!pendingDeletion || !!restoredDeletion.error, onClick: () => { setError(''); setDeleting(item); } },
        ], trigger: <Button type="text" size="small" aria-label={`管理对话${item.title}`} disabled={busy} icon={<Ellipsis size={16}/>}/> };
      }}/>}
      {renaming ? <form className="ai-assistant-history-rename" onSubmit={event => { event.preventDefault(); void mutate(async () => {
        onUpdated(await assistantApi.renameGlobalConversation(renaming.id, renaming.row_version, title.trim())); if (alive.current) setRenaming(null);
      }); }}><label>对话名称<Input aria-label="对话名称" value={title} maxLength={120} disabled={busy} onChange={event => setTitle(event.target.value)}/></label><div><Button htmlType="submit" type="primary" loading={busy} disabled={!title.trim()}>保存</Button><Button disabled={busy} onClick={() => setRenaming(null)}>取消</Button></div></form> : null}
      {total > 20 ? <div className="ai-assistant-history-pagination"><Button disabled={busy || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 20, total)} / {total}</span><Button disabled={busy || loading || offset + 20 >= total} onClick={() => setOffset(value => value + 20)}>下一页</Button></div> : null}
    </div>
    <footer className="ai-assistant-history-footer"><Button disabled={busy} loading={busy} onClick={() => void mutate(async () => { await onNew(); onClose(); })}>新对话</Button><Button disabled={busy} onClick={onClose}>关闭</Button></footer>
  </Dialog>{deleting && <AssistantDeleteDialog key={deleting.id} conversation={deleting} initialClear={clearMemory} busy={busy} pending={pendingDeletion?.targetId === deleting.id} error={error} layout={layout} onClose={() => setDeleting(null)} onRetry={() => pendingDeletion && void mutate(async () => { await deleteConversation(pendingDeletion, true); setDeleting(null); })} onSubmit={body => void mutate(async () => {
    const key = await requestAttempt(`assistant-delete:${deleting.id}`, body, attemptStorage());
    await deleteConversation({ version: 1, kind: 'delete', targetId: deleting.id, body, key });
    if (alive.current) setDeleting(null);
  })}/>}</>;
}
