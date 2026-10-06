import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Dropdown, Input, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentConversation, ConversationScope } from '../../api/types/agents';
import { Icon } from '../../components/ui/Icon';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { agentRunLabel, isAgentRunActive, type CreationMode } from './agent-navigation';
import type { useAgentAvailability } from './useAgentAvailability';
import { AgentConversationRuntime } from './AgentConversationRuntime';
import { Dialog } from '../../components/ui/Dialog';
import { conversationMatchesScope, conversationScopeKey, episodeConversationScope, type AgentSubject } from './agent-scope';

interface PanelProps {
  projectId: string; episodeId: string; episodeTitle: string; accountId: string; selectedId?: string;
  enabled: boolean; readOnly: boolean; mode: CreationMode;
  stage?: 'source' | 'assets' | 'storyboard' | 'assembly'; subject?: AgentSubject | null;
  availability: ReturnType<typeof useAgentAvailability>; beforeSend: () => Promise<boolean>;
  onSelect: (id?: string) => void; onConversation: (conversation: AgentConversation | null) => void;
  onOpenArtifact: (id: string, runId?: string, conversationId?: string) => void;
  navigationIntent: () => number;
  navigationVersion: number;
}

export function AgentConversationPanel(props: PanelProps) {
  const scope = episodeConversationScope(props.stage === 'assets' || props.stage === 'storyboard' ? props.stage : 'source', props.episodeId, props.subject);
  return <ScopedConversationPanel key={`${props.accountId}:${props.projectId}:${props.episodeId}:${conversationScopeKey(scope)}`} {...props} scope={scope}/>;
}

function ScopedConversationPanel({ projectId, episodeId, episodeTitle, accountId, selectedId, mode, enabled, readOnly, availability, beforeSend, onSelect, onConversation, onOpenArtifact, subject, scope, navigationIntent, navigationVersion }: PanelProps & { scope: ConversationScope }) {
  const [items, setItems] = useState<AgentConversation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selected, setSelected] = useState<AgentConversation | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [listRevision, setListRevision] = useState(0);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<AgentConversation | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const alive = useRef(true);
  const selection = useRef({ id: selectedId, mode, generation: 0 });
  const callbacks = useRef({ onConversation, onSelect }); callbacks.current = { onConversation, onSelect };
  const active = enabled && availability.available;
  const stageLabel = { source: '小说改编', assets: '素材准备', storyboard: '分镜制作' }[scope.stage];
  const objectLabel = subject?.label || (scope.subject_type === 'episode' ? '本集作品' : scope.subject_type === 'asset' ? '当前素材' : '当前镜头');
  const defaultTitle = `${objectLabel}：${episodeTitle}`.slice(0, 120);

  useEffect(() => { alive.current = true; return () => { alive.current = false; callbacks.current.onConversation(null); }; }, []);
  useEffect(() => { callbacks.current.onConversation(selected); }, [selected]);
  useLayoutEffect(() => { selection.current = { id: selectedId, mode, generation: selection.current.generation + 1 }; }, [selectedId, mode]);
  useEffect(() => { const timer = setTimeout(() => { setSearch(query.trim()); setOffset(0); }, 200); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    if (!active || !historyOpen) return;
    const controller = new AbortController(); setLoading(true); setError('');
    agentsApi.conversations(projectId, episodeId, offset, includeArchived, controller.signal, scope, search).then(page => {
      if (controller.signal.aborted) return;
      const scoped = page.items.filter(item => conversationMatchesScope(item, scope));
      setItems(previous => offset ? [...previous, ...scoped.filter(item => !previous.some(old => old.id === item.id))] : scoped);
      setTotal(page.total);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) { setItems([]); setSelected(null); }
      setError(errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, active, historyOpen, includeArchived, offset, listRevision, search]);

  useEffect(() => {
    setDetailError(''); setSelected(null);
    if (!active) { setLoadingDetail(false); return; }
    const controller = new AbortController(); setLoadingDetail(true);
    const navigation = navigationIntent();
    const request = selectedId ? agentsApi.conversation(selectedId, controller.signal, scope)
      : readOnly ? Promise.resolve(null) : agentsApi.resolveConversation({ project_id: projectId, episode_id: episodeId, ...scope, title: defaultTitle }, controller.signal);
    request.then(item => {
      if (controller.signal.aborted || !item || !selectedId && navigation !== navigationIntent()) return;
      if (item.project_id !== projectId || item.episode_id !== episodeId || !conversationMatchesScope(item, scope)) {
        if (selectedId) { setNotice('链接中的对话不属于当前创作对象，已切回当前对象。'); callbacks.current.onSelect(undefined); }
        else setDetailError('当前对象的会话范围校验未通过，请重新载入后继续。');
        return;
      }
      if (selectedId) setSelected(item); else callbacks.current.onSelect(item.id);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (selectedId && cause instanceof ApiError && (cause.status === 404 || cause.status === 409)) {
        setNotice('原对话不属于当前创作对象或已不可访问，已切回当前对象。'); callbacks.current.onSelect(undefined);
      } else setDetailError(errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted) setLoadingDetail(false); });
    return () => controller.abort();
  }, [projectId, episodeId, selectedId, active, readOnly, revision, navigationVersion]);

  function refreshList() { setOffset(0); setListRevision(value => value + 1); }
  function refresh() { refreshList(); setRevision(value => value + 1); }
  function isCurrent(intent: typeof selection.current & { navigation: number }) {
    return alive.current && intent.navigation === navigationIntent() && intent.id === selection.current.id && intent.mode === selection.current.mode && intent.generation === selection.current.generation;
  }
  async function create() {
    if (lock.current || readOnly || !active) return;
    const intent = { ...selection.current, navigation: navigationIntent() }; lock.current = true; setBusy(true); setActionError('');
    const attemptScope = `agent-conversation:${accountId}:${projectId}:${episodeId}:${conversationScopeKey(scope)}`;
    try {
      const body = { project_id: projectId, episode_id: episodeId, ...scope, title: defaultTitle };
      const storage = attemptStorage(); const key = await requestAttempt(attemptScope, body, storage);
      if (!isCurrent(intent)) return;
      const item = await agentsApi.createConversation(body, key); clearAttempt(attemptScope, storage);
      if (!isCurrent(intent)) return;
      if (!conversationMatchesScope(item, scope)) throw new Error('新对话的对象范围不匹配，请重新载入。');
      setHistoryOpen(false); callbacks.current.onSelect(item.id); refreshList();
    } catch (cause) { if (isCurrent(intent)) setActionError(`${errorMessage(cause)} 创建请求已保留，重试会核对同一次请求。`); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  async function update(conversation: AgentConversation, patch: { title?: string; archived?: boolean }) {
    if (lock.current || readOnly || !active) return;
    const intent = { ...selection.current, navigation: navigationIntent() }; lock.current = true; setBusy(true); setActionError('');
    try {
      const item = await agentsApi.updateConversation(conversation.id, { row_version: conversation.row_version, ...patch });
      if (!isCurrent(intent)) return;
      refreshList(); setRenaming(null); if (selected?.id === item.id) setSelected(item);
      setNotice(patch.title !== undefined ? '对话名称已更新。' : patch.archived ? '对话已归档，已有作品和候选仍保留。' : '对话已恢复。');
    } catch (cause) { if (isCurrent(intent)) setActionError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  return <aside className="agent-conversation-panel" aria-label="Agent 创作对话">
    <header className="agent-panel-heading"><div><h2>{scope.subject_type === 'episode' ? '对话' : objectLabel}</h2><span className="agent-panel-scope">{stageLabel} · 仅你可见</span></div><Button type="text" aria-label="对话记录" title="对话记录" icon={<Icon name="tasks" size={16}/>} disabled={!availability.available || busy} onClick={() => setHistoryOpen(true)}/></header>
    {!availability.available ? <div className="agent-panel-unavailable" role="status">{availability.loading ? <Skeleton active title paragraph={{ rows: 3 }}/> : <><h3>{availability.reason}</h3><p>{availability.error || '你可以继续在提示词模式中创作。'}</p><Button onClick={availability.refresh}>重新检查</Button></>}</div> : <>
      {historyOpen && <Dialog title={`${objectLabel}的对话记录`} className="agent-history-dialog" onClose={() => setHistoryOpen(false)}>
        <div className="agent-history-body"><p className="agent-history-description">{stageLabel} · 仅显示当前对象的私人对话</p>
          <Input.Search aria-label="搜索当前对象对话" placeholder="搜索对话名称" value={query} allowClear disabled={busy} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); setHistoryOpen(false); } }}/>
          <div className="agent-history-controls"><Checkbox checked={includeArchived} disabled={busy} onChange={event => { setIncludeArchived(event.target.checked); setOffset(0); }}>显示已归档</Checkbox><Button type="link" size="small" aria-label="刷新记录" loading={loading} disabled={busy} onClick={refreshList}>刷新记录</Button></div>
          {error && <Alert type="error" showIcon message="对话记录暂时无法载入" description={error} action={<Button size="small" onClick={refreshList}>重试</Button>}/>}
          {loading && !offset ? <Skeleton active paragraph={{ rows: 4 }}/> : <div className="agent-history-list" aria-label="当前对象会话列表">{items.map(item => <article key={item.id} className={item.id === selected?.id ? 'is-current' : ''}>
            <button type="button" className="agent-history-entry" aria-current={item.id === selected?.id ? 'true' : undefined} disabled={busy} onClick={() => { setHistoryOpen(false); callbacks.current.onSelect(item.id); }}><strong>{item.title}</strong><p>{item.last_message_preview || '还没有消息'}</p><span><time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</time> · {item.archived ? '已归档' : agentRunLabel(item.last_run_status)}{item.id === selected?.id ? ' · 当前对话' : ''}</span></button>
            <Dropdown trigger={['click']} menu={{ items: [{ key: 'rename', label: '重命名', disabled: readOnly || busy }, { key: 'archive', label: item.archived ? '恢复对话' : '归档对话', disabled: readOnly || busy || !item.archived && isAgentRunActive(item.last_run_status) }], onClick: ({ key }) => { if (key === 'rename') { setTitle(item.title); setRenaming(item); } else void update(item, { archived: !item.archived }); } }}><Button type="text" aria-label={`${item.title}的对话操作`} disabled={busy} icon={<Icon name="more" size={16}/>} /></Dropdown>
          </article>)}{!items.length && !error && <p className="agent-context-empty">{search ? '没有匹配的对话，试试其他名称。' : '当前对象还没有对话，可以新建一段开始创作。'}</p>}</div>}
          {items.length < total && <Button size="small" loading={loading} onClick={() => setOffset(items.length)}>加载更多（{items.length}/{total}）</Button>}
          {renaming && <form className="agent-history-rename" onSubmit={event => { event.preventDefault(); if (title.trim()) void update(renaming, { title: title.trim() }); }}><label htmlFor="agent-conversation-title">对话名称</label><Input id="agent-conversation-title" autoFocus maxLength={120} value={title} disabled={busy} onChange={event => setTitle(event.target.value)}/><div><Button htmlType="submit" type="primary" loading={busy} disabled={!title.trim()}>保存名称</Button><Button disabled={busy} onClick={() => setRenaming(null)}>取消</Button></div></form>}
          {actionError && <Alert type="error" showIcon message={actionError}/>}</div>
        <footer className="agent-history-footer"><Button type="primary" aria-label="新建对话" loading={busy} disabled={readOnly || busy} onClick={() => void create()}>新建对话</Button><Button onClick={() => setHistoryOpen(false)}>关闭</Button></footer>
      </Dialog>}
      {!historyOpen && actionError && <Alert type="error" showIcon message={actionError}/>}
      {notice && <p className="agent-inline-notice" role="status">{notice}</p>}
      <div className="agent-conversation-content">{loadingDetail ? <Skeleton active title paragraph={{ rows: 4 }}/> : detailError ? <Alert type="warning" showIcon message={detailError} action={<Button size="small" onClick={refresh}>重新载入</Button>}/> : selected ? selected.archived ? <div className="agent-conversation-empty"><h3>这段对话已归档</h3><p>恢复后可继续创作；已有候选和作品仍保留。</p><Button disabled={readOnly || busy} loading={busy} onClick={() => void update(selected, { archived: false })}>恢复对话</Button></div>
        : <AgentConversationRuntime key={selected.id} conversation={selected} scope={scope} accountId={accountId} readOnly={readOnly} beforeSend={beforeSend} navigationIntent={() => ({ ...selection.current, navigation: navigationIntent() })} onOpenArtifact={onOpenArtifact} onRun={run => setSelected(old => old?.id === selected.id && old.last_run_status !== (run?.status ?? null) ? { ...old, last_run_status: run?.status ?? null } : old)}/>
        : <div className="agent-conversation-empty"><h3>从{objectLabel}开始</h3><p>描述想调整的内容，Agent 会判断如何协助。候选由你核对后采用。</p><Button type="primary" loading={busy} disabled={readOnly || busy} onClick={() => void create()}>新建对话</Button></div>}</div>
    </>}
  </aside>;
}
