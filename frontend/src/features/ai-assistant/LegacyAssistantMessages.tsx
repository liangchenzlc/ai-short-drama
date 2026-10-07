import { useEffect, useState } from 'react';
import { Button, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentMessage } from '../../api/types/agents';
import { Dialog } from '../../components/ui/Dialog';
import { AssistantMessage } from './AssistantMessage';

export function LegacyAssistantMessages({ id, projectId, onClose }: { id: string; projectId: string; onClose: () => void }) {
  const [items, setItems] = useState<AgentMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [title, setTitle] = useState('历史创作记录');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    (async () => {
      const conversation = await agentsApi.conversation(id, controller.signal);
      if (conversation.project_id !== projectId || conversation.scope_version === 2) throw new ApiError('这条历史记录不属于当前项目。', 'assistant_scope_invalid');
      const page = await agentsApi.messages(id, offset, controller.signal);
      if (!controller.signal.aborted) { setTitle(conversation.title || '历史创作记录'); setItems(page.items.filter(item => item.role === 'user' || item.role === 'assistant').sort((a, b) => a.seq - b.seq)); setTotal(page.total); }
    })().catch(cause => { if (!controller.signal.aborted) { setItems([]); setError(errorMessage(cause)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, projectId, offset, revision]);
  return <Dialog title={title} className="ai-assistant-history-dialog ai-assistant-legacy-dialog" onClose={onClose}>
    <div className="ai-assistant-history-body"><p className="ai-assistant-meta">历史消息只供查看。原任务和候选可在工作台的历史创作记录中处理。</p>
      {error ? <div className="ai-assistant-notice" role="alert"><span>{error}</span><Button size="small" onClick={() => setRevision(value => value + 1)}>重新读取</Button></div>
        : loading ? <Skeleton active paragraph={{ rows: 5 }}/>
          : items.length ? <div className="ai-assistant-log">{items.map(message => <AssistantMessage key={message.id} message={message}/>)}</div> : <p className="ai-assistant-meta">暂无消息</p>}
      {total > 50 ? <div className="ai-assistant-history-pagination"><Button disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>更新消息</Button><span>{offset + 1}–{Math.min(offset + 50, total)} / {total}</span><Button disabled={loading || offset + 50 >= total} onClick={() => setOffset(value => value + 50)}>更早消息</Button></div> : null}
    </div>
  </Dialog>;
}
