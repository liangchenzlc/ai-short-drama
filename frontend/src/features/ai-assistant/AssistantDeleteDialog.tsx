import { useEffect, useState } from 'react';
import { Button, Checkbox, Skeleton } from 'antd';
import { assistantApi } from '../../api/modules/assistant';
import { errorMessage } from '../../api/http';
import type { AssistantDeletionInput, AssistantDeletionPreview, GlobalAssistantConversation } from '../../api/types/assistant';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback } from '../../components/ui/StudioFeedback';

export function AssistantDeleteDialog({ conversation, initialClear, busy, pending, error, layout, onClose, onSubmit, onRetry }: {
  conversation: GlobalAssistantConversation; initialClear: boolean; busy: boolean; pending: boolean;
  error: string; layout?: 'standard'; onClose: () => void;
  onSubmit: (body: AssistantDeletionInput) => void; onRetry: () => void;
}) {
  const [page, setPage] = useState<AssistantDeletionPreview | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [clear, setClear] = useState(initialClear);
  const [selected, setSelected] = useState<Record<string, string>>({});
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError('');
    assistantApi.deletionMemory(conversation.id, offset, controller.signal).then(value => {
      if (!controller.signal.aborted) setPage(value);
    }).catch(cause => { if (!controller.signal.aborted) setLoadError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [conversation.id, offset, revision]);
  const locked = busy || pending;
  const count = Object.keys(selected).length;
  return <Dialog title="删除对话" className={`ai-assistant-delete-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} canClose={!busy} onClose={onClose}>
    <div className="ai-assistant-delete-body">
      <p>永久删除“{conversation.title}”？删除后无法恢复。</p>
      <p className="ai-assistant-meta">当前工具完整结束后停止并删除。已创建的卡片、分镜、画布和媒体成果保留；本会话摘要与会话记忆会清理。</p>
      <Checkbox checked={clear} disabled={locked} onChange={event => { setClear(event.target.checked); if (!event.target.checked) setSelected({}); }}>一并清理下方勾选的账号和作品记忆</Checkbox>
      <p className="ai-assistant-meta">账号和作品记忆默认保留。有独立来源或后来编辑的条目需逐条明确选择；确认时再次核对版本。</p>
      {(loadError || error) && <StudioFeedback type="error" title="删除前核对" message={loadError || error} noticeKey={`assistant-delete-preview:${conversation.id}`} actions={!pending && <Button size="small" disabled={busy} onClick={() => { setSelected({}); setRevision(value => value + 1); }}>重新读取预览</Button>}/>}
      {pending && <StudioFeedback type="warning" persistent title="删除结果待核对" message="原删除请求和记忆选择已保留，核对时使用同一请求。" noticeKey={`assistant-delete-receipt:${conversation.id}`} actions={<Button size="small" disabled={busy} onClick={onRetry}>使用原请求核对删除</Button>}/>}
      {loading ? <Skeleton active paragraph={{ rows: 3 }}/> : loadError ? null : page?.items.length ? <div className="ai-assistant-delete-memories" aria-label="可一并清理的记忆">{page.items.map(item => <article key={item.id}>
        <Checkbox aria-label={`清理记忆：${item.title}`} checked={!!selected[item.id]} disabled={locked || !clear || count >= 200 && !selected[item.id]} onChange={event => setSelected(previous => {
          const next = { ...previous }; if (event.target.checked) next[item.id] = item.content_version; else delete next[item.id]; return next;
        })}><strong>{item.title}</strong></Checkbox>
        <span>{item.scope_type === 'user' ? '账号记忆' : '作品记忆'} · 版本 {item.content_version}</span>
        <p>{item.content_preview}</p>
        {!!item.protection_reasons.length && <small>{item.protection_reasons.map(reason => reason === 'user_edited' ? '后来由你编辑过' : '还有其他会话来源').join('；')}，默认保留。</small>}
      </article>)}</div> : <p className="ai-assistant-meta">没有由本会话形成的账号或作品记忆。</p>}
      {page && page.total > page.limit && <div className="ai-assistant-history-pagination"><Button disabled={locked || loading || !offset} onClick={() => setOffset(value => Math.max(0, value - 30))}>上一页</Button><span>{offset + 1}–{Math.min(offset + 30, page.total)} / {page.total}</span><Button disabled={locked || loading || offset + 30 >= page.total} onClick={() => setOffset(value => value + 30)}>下一页</Button></div>}
      {clear && <p className="ai-assistant-meta">已选择 {count} 条；未勾选的记忆保留。单次最多选择 200 条。</p>}
    </div>
    <footer className="ai-assistant-history-footer"><Button disabled={busy} onClick={onClose}>取消</Button><Button danger type="primary" aria-label="永久删除" aria-busy={busy} loading={busy} disabled={locked || loading || !!loadError || !page} onClick={() => page && onSubmit({ row_version: page.row_version, clear_derived_memory: clear,
      memory_selections: clear ? Object.entries(selected).map(([id, content_version]) => ({ id, content_version })) : [],
    })}>永久删除</Button></footer>
  </Dialog>;
}
