import { useEffect, useRef, useState } from 'react';
import { Button, Skeleton, Tag } from 'antd';
import { assistantMemoryApi } from '../../api/modules/assistant-memory';
import { errorMessage } from '../../api/http';
import type { AssistantMemoryReview as Review } from '../../api/types/assistant-memory';
import { StudioFeedback, useStudioMessage } from '../../components/ui/StudioFeedback';

interface Props { conversationId: string; onBusy: (value: boolean) => void; onChanged: () => void }
export function AssistantMemoryReview({ conversationId, onBusy, onChanged }: Props) {
  const message = useStudioMessage();
  const [items, setItems] = useState<Review[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const alive = useRef(true);
  const locked = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setReadError('');
    assistantMemoryApi.reviews(conversationId, offset, controller.signal)
      .then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); setUncertain(false); setError(''); } })
      .catch(cause => { if (!controller.signal.aborted) setReadError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [conversationId, offset, revision]);
  async function choose(job: Review, candidate: Review['clarifications'][number], choice: 'keep_current' | 'use_candidate') {
    if (locked.current) return;
    locked.current = true; const identity = `${job.id}:${candidate.index}`;
    setBusy(identity); onBusy(true); setError('');
    try {
      const updated = await assistantMemoryApi.clarify(job.id, candidate.index, { content_version: candidate.content_version, choice }, crypto.randomUUID());
      if (alive.current) {
        setItems(current => current.map(item => item.id === updated.id ? updated : item));
        message.success(choice === 'keep_current' ? '已保留当前记忆' : '已使用候选内容', 'assistant-memory-clarification');
        onChanged();
      }
    } catch (cause) { if (alive.current) { setError(errorMessage(cause)); setUncertain(true); } }
    finally { locked.current = false; onBusy(false); if (alive.current) setBusy(''); }
  }
  const refresh = () => setRevision(value => value + 1);
  return <section className="assistant-memory-review" aria-label="记忆冲突澄清">
    <div className="assistant-memory-toolbar"><p>自动整理遇到不同内容时，先保留原记忆。请对照后选择。</p><Button disabled={!!busy} loading={loading} onClick={refresh}>刷新候选</Button></div>
    {error && <StudioFeedback type="warning" title="澄清选择待核对" message={error} noticeKey="assistant-memory-clarification-error" persistent actions={<Button disabled={!!busy} onClick={refresh}>重新读取候选</Button>}/>}
    {loading ? <Skeleton active/> : readError ? <div role="alert"><p>{readError}</p><Button onClick={refresh}>重新读取候选</Button></div> : !items.length ? <p className="assistant-memory-help">当前会话没有需要澄清的记忆。</p> : items.map(job => job.clarifications.map(candidate => <article key={`${job.id}:${candidate.index}`} role="group" aria-label={`澄清：${candidate.title}`}>
      <h3>{candidate.title}<Tag>{candidate.status === 'resolved' ? '已处理' : candidate.status === 'unavailable' ? '已停止使用' : '等待选择'}</Tag></h3>
      {candidate.status === 'unavailable' ? <p className="assistant-memory-help">原记忆已遗忘或不可访问，旧候选不会将它恢复。</p> : <>
        {candidate.status === 'changed' && <p className="assistant-memory-help">原记忆已更新，请对照当前版本 {candidate.content_version} 后选择。</p>}
        <div className="assistant-memory-review-comparison"><section><h4>当前记忆 · 版本 {candidate.content_version}</h4><p>{candidate.current_content}</p></section><section><h4>整理候选</h4><p>{candidate.candidate_content}</p><small>来源消息：{candidate.source_message_ids.join('、')}</small></section></div>
        {candidate.status === 'resolved' ? <p className="assistant-memory-help">{candidate.choice === 'keep_current' ? '已明确保留当时的当前版本。' : '已明确使用候选，变更保存在记忆版本记录中。'}</p> : <div className="assistant-memory-actions"><Button disabled={!!busy || uncertain} loading={busy === `${job.id}:${candidate.index}`} aria-label="保留当前记忆" onClick={() => void choose(job, candidate, 'keep_current')}>保留当前记忆</Button><Button type="primary" disabled={!!busy || uncertain} loading={busy === `${job.id}:${candidate.index}`} aria-label="使用候选内容" onClick={() => void choose(job, candidate, 'use_candidate')}>使用候选内容</Button></div>}
      </>}
    </article>)) }
    <nav aria-label="澄清记录分页"><Button size="small" disabled={!!busy || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>{total} 组整理记录</span><Button size="small" disabled={!!busy || loading || offset + items.length >= total} onClick={() => setOffset(value => value + 20)}>下一页</Button></nav>
  </section>;
}
