import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Segmented, Select, Skeleton, Tag } from 'antd';
import { assistantMemoryApi } from '../../api/modules/assistant-memory';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantMemory, AssistantMemoryContext, AssistantMemoryCreate, AssistantMemoryRevision, AssistantMemorySummary, MemoryKind, MemoryScope } from '../../api/types/assistant-memory';
import { Dialog } from '../../components/ui/Dialog';
import { StudioFeedback, useStudioMessage } from '../../components/ui/StudioFeedback';
import { confirmAction } from '../../components/ui/confirm';
import { workflowStorage } from '../auth/account-storage';
import { clearMemoryDraft, readMemoryDraft, saveMemoryDraft, type MemoryDraft } from './assistant-memory-draft';
import { AssistantMemoryReview } from './AssistantMemoryReview';
import './assistant-memory.css';

const scopeNames: Record<MemoryScope, string> = { user: '账号', conversation: '会话', project: '作品' };
const kindNames: Record<MemoryKind, string> = { preference: '偏好', correction: '纠正', setting: '设定', decision: '决定', reference: '参考' };
const factNames = { user_fact: '用户事实', proposal: '助手提议', completed: '已完成', pending: '待办' };
const jobNames = { pending: '等待整理', running: '整理中', succeeded: '已完成', failed: '整理失败', stale: '来源已变更', cancelled: '已取消' };
const fieldsOf = (item: AssistantMemory): AssistantMemoryCreate => ({ scope_type: item.scope_type,
  ...(item.conversation_id ? { conversation_id: item.conversation_id } : {}), ...(item.project_id ? { project_id: item.project_id } : {}),
  kind: item.kind, memory_key: item.memory_key, title: item.title, content: item.content,
  aliases: item.aliases, tags: item.tags, pinned: item.pinned });
const sameFields = (item: AssistantMemory, values: AssistantMemoryCreate) => item.title === values.title.trim()
  && item.content === values.content.trim() && item.pinned === values.pinned
  && JSON.stringify(item.aliases) === JSON.stringify(values.aliases.map(value => value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()))
  && JSON.stringify(item.tags) === JSON.stringify(values.tags.map(value => value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()));

interface Props { accountId: string; conversationId?: string; projectId?: string; layout?: 'standard'; onClose: () => void; onSource: (id: string) => Promise<void> }
export function AssistantMemory(props: Props) {
  const [busy, setBusy] = useState(false);
  return <Dialog title="记忆管理" className={`assistant-memory-dialog${props.layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} canClose={!busy} onClose={props.onClose}>
    <MemoryBody {...props} busy={busy} onBusy={setBusy}/>
  </Dialog>;
}

function MemorySummary({ conversationId, summary }: { conversationId: string; summary: AssistantMemorySummary }) {
  const [summaryId, setSummaryId] = useState<string | null>(null);
  const [previous, setPrevious] = useState<AssistantMemorySummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!summaryId) return;
    const controller = new AbortController(); setLoading(true); setError(''); setPrevious(null);
    assistantMemoryApi.summary(conversationId, summaryId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPrevious(value); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof ApiError && cause.status === 404
        ? '这份摘要不可用，来源可能已被纠正或遗忘。请返回最新摘要。' : errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [conversationId, summaryId, revision]);
  const value = summaryId ? previous : summary;
  return <section aria-label="摘要来源链">
    <h3>{summaryId ? '历史阶段摘要' : '当前摘要'}</h3>
    {summaryId && <Button size="small" onClick={() => setSummaryId(null)}>返回最新摘要</Button>}
    {summaryId && loading ? <Skeleton active paragraph={{ rows: 2 }}/> : summaryId && error ? <div role="alert"><p>{error}</p><Button onClick={() => setRevision(current => current + 1)}>重新读取摘要</Button></div> : value ? <>
      <p>本阶段来源消息 {value.from_seq}–{value.to_seq}；此前来源沿摘要链保留。</p>
      <ul>{value.facts.map((fact, index) => <li key={index}>
        <Tag>{factNames[fact.kind]}</Tag>{fact.text}
        <small>第 {index + 1} 条{fact.source_message_ids.length ? ` · 来源消息：${fact.source_message_ids.join('、')}` : ''}{fact.prior_fact_indexes?.length ? ` · 此前摘要第 ${fact.prior_fact_indexes.map(item => item + 1).join('、')} 条` : ''}</small>
      </li>)}</ul>
      {value.base_summary_id && <Button size="small" onClick={() => setSummaryId(value.base_summary_id ?? null)}>查看上阶段摘要</Button>}
    </> : null}
  </section>;
}

function MemoryBody({ accountId, conversationId, projectId, onSource, busy, onBusy }: Props & { busy: boolean; onBusy: (value: boolean) => void }) {
  const message = useStudioMessage();
  const [tab, setTab] = useState('items');
  const [scope, setScope] = useState('user');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [forgotten, setForgotten] = useState(false);
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AssistantMemory[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState<AssistantMemory | null>(null);
  const [restored] = useState(() => readMemoryDraft(accountId, workflowStorage()));
  const [draft, setDraft] = useState<MemoryDraft | null>(restored.draft);
  const [draftError, setDraftError] = useState(restored.error);
  const [operationError, setOperationError] = useState('');
  const [serverVersion, setServerVersion] = useState<AssistantMemory | null>(null);
  const [versions, setVersions] = useState<AssistantMemoryRevision[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState('');
  const [context, setContext] = useState<AssistantMemoryContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState('');
  const alive = useRef(true);
  const locked = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    assistantMemoryApi.list({ offset, include_forgotten: forgotten, query: search || undefined,
      conversation_id: scope === 'conversation' ? conversationId : undefined, project_id: scope === 'project' ? projectId : undefined }, controller.signal)
      .then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [scope, offset, search, forgotten, conversationId, projectId, revision]);
  useEffect(() => {
    if (tab !== 'context' || !conversationId) return;
    const controller = new AbortController(); setContextLoading(true); setContextError('');
    assistantMemoryApi.context(conversationId, controller.signal).then(value => { if (!controller.signal.aborted) setContext(value); })
      .catch(cause => { if (!controller.signal.aborted) setContextError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setContextLoading(false); });
    return () => controller.abort();
  }, [tab, conversationId, revision]);
  useEffect(() => {
    setVersions([]); setVersionsError('');
    if (!selection) return;
    const controller = new AbortController(); setVersionsLoading(true);
    assistantMemoryApi.revisions(selection.id, controller.signal).then(page => { if (!controller.signal.aborted) setVersions(page.items); })
      .catch(cause => { if (!controller.signal.aborted) setVersionsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setVersionsLoading(false); });
    return () => controller.abort();
  }, [selection]);
  function persist(value: MemoryDraft) {
    const saved = saveMemoryDraft(accountId, value, workflowStorage());
    setDraft(value);
    setDraftError(saved ? '' : '浏览器未能保存记忆草稿。请保留当前内容，允许本机存储后再保存。');
    return saved;
  }
  function start(item: AssistantMemory | null) {
    if (draft || draftError) { message.warning('请先保存或明确放弃当前草稿。', 'assistant-memory-draft'); return; }
    setSelection(item); setServerVersion(null); setOperationError('');
    if (item?.state !== 'active' && item) return;
    persist({ version: 1, item, key: crypto.randomUUID(), pending: false,
      values: item ? fieldsOf(item) : { scope_type: 'user', kind: 'preference', memory_key: '', title: '', content: '', aliases: [], tags: [], pinned: false } });
  }
  function change(values: Partial<AssistantMemoryCreate>) {
    if (draft && !draft.pending) persist({ ...draft, key: crypto.randomUUID(), values: { ...draft.values, ...values } });
  }
  async function mutate(operation: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; onBusy(true); setOperationError('');
    try { await operation(); }
    catch (cause) { if (alive.current) setOperationError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) onBusy(false); }
  }
  function accepted(item: AssistantMemory) {
    if (!alive.current) return;
    if (!clearMemoryDraft(accountId, workflowStorage())) {
      setDraftError('记忆已保存，但本机恢复记录尚未清理，请核对服务器版本。');
      return;
    }
    setDraft(null); setSelection(item); setServerVersion(null); setDraftError('');
    setRevision(value => value + 1); message.success('记忆已保存', 'assistant-memory-saved');
  }
  async function save() {
    if (!draft) return;
    if (draft.pending && draft.item) { await reconcile(); return; }
    const request = { ...draft, pending: true };
    if (!persist(request)) return;
    try {
      const item = request.item ? await assistantMemoryApi.patch(request.item.id, {
        content_version: request.item.content_version, title: request.values.title, content: request.values.content,
        aliases: request.values.aliases, tags: request.values.tags, pinned: request.values.pinned,
      }) : await assistantMemoryApi.create(request.values, request.key);
      accepted(item);
    } catch (cause) {
      if (alive.current && cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500) persist({ ...request, pending: false });
      throw cause;
    }
  }
  async function reconcile() {
    if (!draft?.item) return;
    const latest = await assistantMemoryApi.read(draft.item.id);
    if (!alive.current) return;
    setServerVersion(latest);
    if (latest.state === 'active' && sameFields(latest, draft.values)) { accepted(latest); return; }
    persist({ ...draft, pending: false });
    message.warning('已读取服务端版本，请对照草稿后决定如何继续。', 'assistant-memory-compare');
  }
  async function discard() {
    if (!await confirmAction(draft?.pending ? '保存结果尚未确认。放弃本机草稿不会撤销已受理的保存，请先核对服务器。' : '这会清除本机的记忆编辑草稿。', { title: '放弃这份记忆草稿？', confirmText: '放弃草稿', cancelText: '保留草稿', danger: true })) return;
    if (clearMemoryDraft(accountId, workflowStorage())) { setDraft(null); setDraftError(''); setServerVersion(null); }
    else setDraftError('浏览器无法清除草稿，请保留当前记录。');
  }
  async function toggleState(item: AssistantMemory) {
    const restoring = item.state === 'forgotten';
    if (!await confirmAction(restoring ? '恢复后这条记忆会重新参与允许范围内的检索。' : '立即停止自动使用，并阻止后台用旧来源重新生成；原始消息仍然保留。', { title: restoring ? '重新记住这条记忆？' : '忘掉这条记忆？',
      confirmText: restoring ? '重新记住' : '忘掉', cancelText: '取消', danger: !restoring })) return;
    const result = restoring ? await assistantMemoryApi.restore(item.id, item.content_version) : await assistantMemoryApi.forget(item.id, item.content_version);
    if (alive.current) { setSelection(result); setRevision(value => value + 1); message.success(restoring ? '已重新记住' : '已遗忘', 'assistant-memory-state'); }
  }
  async function retryJob(job: AssistantMemoryContext['jobs'][number]) {
    if (!await confirmAction('将使用原模型重新整理这段对话，可能产生模型调用费用。', { title: '重新整理这段对话？', confirmText: '重新整理', cancelText: '取消' })) return;
    await assistantMemoryApi.retryJob(job.id, job.message_version, crypto.randomUUID());
    if (alive.current) { setRevision(value => value + 1); message.success('已安排重新整理', 'assistant-memory-retry'); }
  }
  const active = draft?.item ?? selection;
  const frozen = busy || !!draft?.pending;
  const saveLabel = draft?.pending ? draft.item ? '核对服务器版本' : '使用原请求核对保存' : '保存记忆';
  return <div className="assistant-memory-body">
    <Segmented aria-label="记忆管理内容" value={tab} disabled={busy} onChange={value => setTab(String(value))} options={[{ value: 'items', label: '记忆条目' }, { value: 'context', label: '摘要与整理', disabled: !conversationId }, { value: 'review', label: '待澄清', disabled: !conversationId }]}/>
    {operationError && <StudioFeedback type="error" title="记忆操作未完成" message={operationError} noticeKey="assistant-memory-operation" persistent actions={<Button size="small" loading={busy} onClick={() => void mutate(async () => { if (draft?.item) await reconcile(); else setRevision(value => value + 1); })}>核对服务器版本</Button>}/>}
    {draftError && <StudioFeedback type="warning" title="记忆草稿" message={draftError} noticeKey="assistant-memory-storage" persistent actions={!draft ? <Button size="small" onClick={() => void discard()}>明确清除损坏草稿</Button> : undefined}/>}
    {tab === 'review' && conversationId ? <AssistantMemoryReview conversationId={conversationId} onBusy={onBusy} onChanged={() => setRevision(value => value + 1)}/> : tab === 'context' ? <section className="assistant-memory-context" aria-label="会话摘要与整理">
      <div className="assistant-memory-toolbar"><p>后台整理保持安静；新消息保留在近期原文中。</p><Button aria-label="刷新详情" aria-busy={contextLoading} loading={contextLoading} disabled={contextLoading} onClick={() => setRevision(value => value + 1)}>刷新详情</Button></div>
      {contextLoading ? <Skeleton active/> : contextError ? <p role="alert">{contextError}</p> : context ? <>
        {context.summary && conversationId ? <><p>已覆盖消息 1–{context.covers_until_seq}</p><MemorySummary key={context.summary.id} conversationId={conversationId} summary={context.summary}/></> : <><h3>当前摘要</h3><p>尚未启用摘要，当前使用近期原文。</p></>}
        <h3>最近整理记录</h3>{!context.jobs.length ? <p>暂无后台整理记录。</p> : <ul>{context.jobs.map(job => <li key={job.id}><strong>{job.kind === 'summary' ? '压缩摘要' : job.kind === 'extract' ? '整理记忆' : job.kind === 'task_state' ? '整理任务约束' : '删除清理'}</strong><Tag>{jobNames[job.status]}</Tag><p>消息 {job.from_seq}–{job.to_seq} · 已调用 {job.attempts} 次</p>{job.provider_state === 'unknown' ? <p>受理结果不明，已停止自动重发。</p> : job.error ? <p>处理未完成（{job.error.code}），原始资料仍保留。</p> : null}{job.retry_job_id ? <p>已安排后续整理，请刷新查看结果。</p> : job.status === 'failed' && job.kind !== 'purge' && !['unknown', 'sending'].includes(job.provider_state) ? <Button disabled={busy || !!operationError} loading={busy} aria-label="重新整理这段对话" onClick={() => void mutate(() => retryJob(job))}>重新整理这段对话</Button> : null}</li>)}</ul>}
      </> : <p>暂无摘要详情。</p>}
    </section> : <>
      <div className="assistant-memory-toolbar"><Select aria-label="记忆查看范围" value={scope} disabled={busy} onChange={value => { setScope(value); setOffset(0); }} options={[{ value: 'user', label: '账号记忆' }, { value: 'conversation', label: '账号 + 当前会话', disabled: !conversationId }, { value: 'project', label: '账号 + 当前作品', disabled: !projectId }]}/><Button disabled={busy || !!draft || !!draftError} onClick={() => start(null)}>新建记忆</Button></div>
      <form className="assistant-memory-search" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}><Input aria-label="搜索记忆" value={query} placeholder="搜索标题、别名或正文" maxLength={120} onChange={event => setQuery(event.target.value)}/><Button htmlType="submit" disabled={busy}>搜索</Button><Checkbox checked={forgotten} onChange={event => { setForgotten(event.target.checked); setOffset(0); }}>含已遗忘</Checkbox></form>
      <div className="assistant-memory-columns"><section className="assistant-memory-list" aria-label="记忆列表" aria-busy={loading}>
        {loading ? <Skeleton active paragraph={{ rows: 5 }}/> : error ? <div role="alert"><p>{error}</p><Button onClick={() => setRevision(value => value + 1)}>重新读取</Button></div> : !items.length ? <p>当前范围暂无记忆。可以新建，或换一个范围查找。</p> : items.map(item => <button key={item.id} type="button" className="assistant-memory-entry" aria-pressed={active?.id === item.id} disabled={busy || !!draft} onClick={() => { setSelection(item); setServerVersion(null); }}><span>{item.title}</span><small>{scopeNames[item.scope_type]} · {kindNames[item.kind]} · {item.state === 'forgotten' ? '已遗忘' : item.state === 'superseded' ? '已替代' : '有效'}{item.pinned ? ' · 置顶' : ''}</small></button>)}
        <nav aria-label="记忆分页"><Button size="small" disabled={busy || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 30))}>上一页</Button><span>{total} 条</span><Button size="small" disabled={busy || loading || offset + items.length >= total} onClick={() => setOffset(value => value + 30)}>下一页</Button></nav>
      </section><section className="assistant-memory-editor" aria-label="记忆内容">
        {draft ? <form onSubmit={event => { event.preventDefault(); void mutate(save); }}>
          <h3>{draft.item ? '编辑记忆' : '新建记忆'}</h3>
          <label>保存范围<Select aria-label="记忆保存范围" value={draft.values.scope_type} disabled={frozen || !!draft.item} onChange={value => change({ scope_type: value, conversation_id: value === 'conversation' ? conversationId : undefined, project_id: value === 'project' ? projectId : undefined })} options={[{ value: 'user', label: '账号：跨会话使用' }, { value: 'conversation', label: '仅当前会话', disabled: !conversationId }, { value: 'project', label: '当前作品：按任务绑定读取', disabled: !projectId }]}/></label>
          <label>分类<Select aria-label="记忆分类" value={draft.values.kind} disabled={frozen || !!draft.item} onChange={value => change({ kind: value })} options={Object.entries(kindNames).map(([value, label]) => ({ value, label }))}/></label>
          <label>事实键<Input aria-label="记忆事实键" value={draft.values.memory_key} disabled={frozen || !!draft.item} required maxLength={160} placeholder="同一事实使用固定名称" onChange={event => change({ memory_key: event.target.value })}/></label>
          <label>标题<Input aria-label="记忆标题" value={draft.values.title} disabled={frozen} required maxLength={200} onChange={event => change({ title: event.target.value })}/></label>
          <label>内容<Input.TextArea aria-label="记忆正文" value={draft.values.content} disabled={frozen} required maxLength={12000} autoSize={{ minRows: 5, maxRows: 12 }} onChange={event => change({ content: event.target.value })}/></label>
          <label>别名<Select mode="tags" aria-label="记忆别名" value={draft.values.aliases} disabled={frozen} maxCount={16} onChange={value => change({ aliases: value })} tokenSeparators={['，', ',']}/></label>
          <label>标签<Select mode="tags" aria-label="记忆标签" value={draft.values.tags} disabled={frozen} maxCount={16} onChange={value => change({ tags: value })} tokenSeparators={['，', ',']}/></label>
          <Checkbox checked={draft.values.pinned} disabled={frozen} onChange={event => change({ pinned: event.target.checked })}>置顶目录</Checkbox>
          {serverVersion && <div className="assistant-memory-comparison"><strong>服务端当前内容 · 版本 {serverVersion.content_version}</strong><p>{serverVersion.content}</p><Button disabled={busy || serverVersion.state !== 'active'} onClick={() => { persist({ ...draft, item: serverVersion, key: crypto.randomUUID(), pending: false }); setServerVersion(null); }}>已核对，按此版本继续编辑草稿</Button></div>}
          <div className="assistant-memory-actions"><Button type="primary" htmlType="submit" aria-label={saveLabel} aria-busy={busy} disabled={busy} loading={busy}>{saveLabel}</Button><Button disabled={busy} onClick={() => void discard()}>放弃草稿</Button>{draft.item && !draft.pending && <Button disabled={busy} onClick={() => void mutate(reconcile)}>读取服务端版本</Button>}</div>
          {draft.pending && !busy && <StudioFeedback type="warning" title="记忆保存待核对" message="原请求和草稿已保留，请核对保存结果后继续编辑。" noticeKey="assistant-memory-pending" persistent/>}
        </form> : selection ? <>
          <h3>{selection.title}</h3><p className="assistant-memory-help">{scopeNames[selection.scope_type]} · {kindNames[selection.kind]} · 版本 {selection.content_version}</p><div className="assistant-memory-content">{selection.content}</div>
          <div className="assistant-memory-actions">{selection.state === 'active' && <Button disabled={busy} onClick={() => start(selection)}>编辑</Button>}{selection.state !== 'superseded' && <Button aria-label={selection.state === 'forgotten' ? '重新记住' : '忘掉'} aria-busy={busy} disabled={busy} danger={selection.state === 'active'} loading={busy} onClick={() => void mutate(() => toggleState(selection))}>{selection.state === 'forgotten' ? '重新记住' : '忘掉'}</Button>}</div>
          <h4>来源</h4>{selection.sources.length ? selection.sources.map(source => <div key={`${source.conversation_id}:${source.seq}`}><Button type="link" size="small" disabled={busy} onClick={() => void mutate(() => onSource(source.conversation_id))}>会话 {source.conversation_id.slice(-6)} · 消息 {source.seq}</Button></div>) : <p className="assistant-memory-help">由你明确保存，未绑定原始消息。</p>}
          <details><summary>版本记录</summary>{versionsLoading ? <Skeleton active paragraph={{ rows: 2 }}/> : versionsError ? <p role="alert">{versionsError}</p> : !versions.length ? <p>暂无版本记录。</p> : versions.map(version => <article key={version.content_version}><strong>版本 {version.content_version} · {version.change}</strong><p>{String(version.snapshot.content ?? '')}</p></article>)}</details>
        </> : <p className="assistant-memory-help">选择一条记忆查看内容和来源，或新建记忆。</p>}
      </section></div>
    </>}
  </div>;
}
