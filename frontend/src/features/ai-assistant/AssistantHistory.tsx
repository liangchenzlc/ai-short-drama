import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Dropdown, Input, Segmented, Skeleton } from 'antd';
import { Ellipsis } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { errorMessage } from '../../api/http';
import type { AgentConversation } from '../../api/types/agents';
import type { AssistantConversation } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { confirmAction } from '../../components/ui/confirm';

export function AssistantHistory({ projectId, currentId, readOnly, onSelect, onLegacy, onNew, onClose, onUpdated }: {
  projectId: string; currentId: string; readOnly: boolean; onSelect: (id: string) => void;
  onLegacy: (id: string) => void; onNew: () => Promise<void>; onClose: () => void;
  onUpdated: (conversation: AssistantConversation) => void;
}) {
  const [legacy, setLegacy] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<(AgentConversation | AssistantConversation)[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [renaming, setRenaming] = useState<AgentConversation | AssistantConversation | null>(null);
  const [title, setTitle] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    const request = legacy ? assistantApi.legacyConversations(projectId, offset, controller.signal, search)
      : assistantApi.conversations(projectId, offset, archived, controller.signal, search);
    request.then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, legacy, archived, offset, search, revision]);
  async function mutate(operation: () => Promise<void>) {
    if (busy || readOnly) return;
    setBusy(true); setError('');
    try { await operation(); if (alive.current) setRevision(value => value + 1); }
    catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { if (alive.current) setBusy(false); }
  }
  return <Dialog title="对话记录" className="ai-assistant-history-dialog" canClose={!busy} onClose={onClose}>
    <div className="ai-assistant-history-body">
      <p className="ai-assistant-meta">本项目的对话仅本人可见，切换作品会保留当前对话。</p>
      <Segmented aria-label="对话记录类型" value={legacy ? 'legacy' : 'chat'} disabled={busy} options={[{ value: 'chat', label: '助手对话' }, { value: 'legacy', label: '历史创作记录' }]} onChange={value => { setLegacy(value === 'legacy'); setOffset(0); setRenaming(null); }}/>
      <form className="ai-assistant-history-search" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}><Input aria-label="搜索对话" placeholder="搜索对话标题" value={query} disabled={busy} onChange={event => setQuery(event.target.value)}/><Button htmlType="submit" disabled={busy}>搜索</Button></form>
      {!legacy ? <Checkbox checked={archived} disabled={busy} onChange={event => { setArchived(event.target.checked); setOffset(0); }}>包含归档对话</Checkbox> : <p className="ai-assistant-meta">查看原对话和已创建的创作记录，继续聊天请使用新的助手对话。</p>}
      {error ? <div className="ai-assistant-notice" role="alert"><span>{error}</span><Button size="small" onClick={() => setRevision(value => value + 1)}>重新读取</Button></div> : null}
      {loading ? <Skeleton active paragraph={{ rows: 4 }}/> : !items.length && !error ? <p className="ai-assistant-history-empty">{search ? '没有匹配的对话' : legacy ? '没有历史创作记录' : '还没有对话记录'}</p> : <div className="ai-assistant-history-list">{items.map(item => <article key={item.id} className={item.id === currentId ? 'is-current' : ''}>
        <button className="ai-assistant-history-entry" type="button" disabled={busy} onClick={() => { if (legacy) onLegacy(item.id); else onSelect(item.id); onClose(); }}><strong>{item.title || '还没有内容的对话'}</strong><p>{item.last_message_preview || '暂无消息'}</p><span>{new Date(item.updated_at).toLocaleString()}{item.id === currentId ? ' · 当前对话' : ''}{item.archived ? ' · 已归档' : ''}</span></button>
        {!legacy ? <Dropdown trigger={['click']} menu={{ items: [
          { key: 'rename', label: '重命名', onClick: () => { setRenaming(item); setTitle(item.title); } },
          { key: 'archive', label: item.archived ? '恢复对话' : '归档', onClick: () => void mutate(async () => {
            if (!item.archived && !await confirmAction(`归档“${item.title || '此对话'}”？消息会保留，可以从归档列表恢复。`, { title: '归档对话', confirmText: '归档' })) return;
            onUpdated(await assistantApi.updateConversation(item.id, { row_version: item.row_version, archived: !item.archived }));
          }) },
        ] }}><Button type="text" size="small" aria-label={`管理对话${item.title}`} disabled={readOnly || busy} icon={<Ellipsis size={16}/>}/></Dropdown> : null}
      </article>)}</div>}
      {renaming ? <form className="ai-assistant-history-rename" onSubmit={event => { event.preventDefault(); void mutate(async () => {
        onUpdated(await assistantApi.updateConversation(renaming.id, { row_version: renaming.row_version, title: title.trim() })); if (alive.current) setRenaming(null);
      }); }}><label>对话名称<Input aria-label="对话名称" value={title} maxLength={120} disabled={busy} onChange={event => setTitle(event.target.value)}/></label><div><Button htmlType="submit" type="primary" loading={busy} disabled={readOnly || !title.trim()}>保存</Button><Button disabled={busy} onClick={() => setRenaming(null)}>取消</Button></div></form> : null}
      {total > 20 ? <div className="ai-assistant-history-pagination"><Button disabled={busy || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 20, total)} / {total}</span><Button disabled={busy || loading || offset + 20 >= total} onClick={() => setOffset(value => value + 20)}>下一页</Button></div> : null}
    </div>
    <footer className="ai-assistant-history-footer"><Button disabled={readOnly || busy} loading={busy} onClick={() => void mutate(async () => { await onNew(); onClose(); })}>新对话</Button><Button disabled={busy} onClick={onClose}>关闭</Button></footer>
  </Dialog>;
}
