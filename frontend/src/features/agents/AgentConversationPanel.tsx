import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Dropdown, Input, Select, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentConversation } from '../../api/types/agents';
import { Icon } from '../../components/ui/Icon';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { agentRunLabel, isAgentRunActive, type CreationMode } from './agent-navigation';
import type { useAgentAvailability } from './useAgentAvailability';
import { AgentConversationRuntime } from './AgentConversationRuntime';

export function AgentConversationPanel({ projectId, episodeId, episodeTitle, selectedId, mode, stage, enabled, readOnly, availability, beforeSend, onSelect, onConversation, onOpenArtifact }: {
  projectId: string; episodeId: string; episodeTitle: string; selectedId?: string; enabled: boolean; readOnly: boolean;
  mode: CreationMode;
  stage?: 'source' | 'assets' | 'storyboard' | 'assembly';
  availability: ReturnType<typeof useAgentAvailability>;
  beforeSend: () => Promise<boolean>;
  onSelect: (id?: string) => void; onConversation: (conversation: AgentConversation | null) => void;
  onOpenArtifact: (id: string, runId?: string, conversationId?: string) => void;
}) {
  const [items, setItems] = useState<AgentConversation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selected, setSelected] = useState<AgentConversation | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [listRevision, setListRevision] = useState(0);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState('');
  const [notice, setNotice] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const lock = useRef(false);
  const stageConversations = useRef(new Map<string, string>());
  const stageCreationErrors = useRef(new Map<string, string>());
  const alive = useRef(true);
  const selection = useRef({ id: selectedId, mode, stage, generation: 0 });
  const selectedCallback = useRef(onConversation);
  const selectionCallback = useRef(onSelect);
  selectedCallback.current = onConversation;
  selectionCallback.current = onSelect;
  const active = enabled && availability.available;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { selectedCallback.current(selected); }, [selected]);
  useLayoutEffect(() => {
    selection.current = { id: selectedId, mode, stage, generation: selection.current.generation + 1 };
  }, [selectedId, mode, stage]);
  useLayoutEffect(() => {
    setRenaming(false);
    setActionError(!selectedId && stage ? stageCreationErrors.current.get(stage) ?? '' : '');
    setNotice('');
  }, [selectedId, stage]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    agentsApi.conversations(projectId, episodeId, offset, includeArchived, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setItems(previous => offset ? [...previous, ...page.items.filter(item => !previous.some(old => old.id === item.id))] : page.items);
      setTotal(page.total);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) { setItems([]); setSelected(null); }
      setError(errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, episodeId, active, includeArchived, offset, listRevision]);

  useEffect(() => {
    setDetailError(''); setSelected(null);
    if (!active || !selectedId) { setLoadingDetail(false); return; }
    const controller = new AbortController();
    setLoadingDetail(true);
    agentsApi.conversation(selectedId, controller.signal).then(item => {
      if (controller.signal.aborted) return;
      if (item.project_id !== projectId || item.episode_id !== episodeId) {
        setDetailError('这段对话不属于当前分集，请选择本集对话。'); return;
      }
      setSelected(item);
    }).catch(cause => {
      if (!controller.signal.aborted) setDetailError(cause instanceof ApiError && cause.status === 404
        ? '对话不存在或你已无法查看，请选择其他对话。' : errorMessage(cause));
    }).finally(() => { if (!controller.signal.aborted) setLoadingDetail(false); });
    return () => controller.abort();
  }, [projectId, episodeId, selectedId, active, revision]);

  function refreshList() { setOffset(0); setListRevision(value => value + 1); }
  function refresh() { refreshList(); setRevision(value => value + 1); }
  function isCurrentSelection(intent: typeof selection.current) {
    return alive.current && intent.id === selection.current.id && intent.mode === selection.current.mode && intent.stage === selection.current.stage && intent.generation === selection.current.generation;
  }
  async function create(automatic = false) {
    if (lock.current || readOnly || !active) return;
    const intent = selection.current;
    lock.current = true; setBusy(true); setActionError(''); setNotice('');
    try {
      const labels = { source: '小说改编', assets: '素材准备', storyboard: '分镜制作', assembly: '成片合成' };
      const body = { project_id: projectId, episode_id: episodeId, ...(stage ? { title: `${labels[stage]}：${episodeTitle}`.slice(0, 120) } : {}) };
      const scope = `agent-conversation:${projectId}:${episodeId}:${stage ?? 'general'}`;
      const storage = attemptStorage();
      const key = await requestAttempt(scope, body, storage);
      if (!isCurrentSelection(intent)) return;
      const item = await agentsApi.createConversation(body, key);
      if (!alive.current) return;
      clearAttempt(scope, storage);
      if (stage) stageCreationErrors.current.delete(stage);
      if (stage && (automatic || isCurrentSelection(intent))) stageConversations.current.set(stage, item.id);
      refreshList();
      if (isCurrentSelection(intent)) { setSelected(item); setNotice('新对话已创建。'); selectionCallback.current(item.id); }
    } catch (cause) {
      const message = `${errorMessage(cause)} 新对话请求已保留，重试会核对同一次创建。`;
      if (stage && (automatic || !intent.id)) stageCreationErrors.current.set(stage, message);
      if (isCurrentSelection(intent)) setActionError(message);
    }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    if (!stage || !active || mode !== 'agent' || selectedId || readOnly || busy || actionError || stageCreationErrors.current.has(stage)) return;
    const existing = stageConversations.current.get(stage);
    if (existing) selectionCallback.current(existing);
    else void create(true);
  }, [stage, active, mode, selectedId, readOnly, busy, actionError]);
  async function update(patch: { title?: string; archived?: boolean }) {
    if (lock.current || readOnly || !active || !selected || selected.id !== selectedId) return;
    const conversation = selected;
    const intent = selection.current;
    lock.current = true; setBusy(true); setActionError(''); setNotice('');
    try {
      const item = await agentsApi.updateConversation(conversation.id, { row_version: conversation.row_version, ...patch });
      if (!alive.current) return;
      refreshList();
      if (isCurrentSelection(intent) && intent.id === conversation.id) {
        setSelected(item); setRenaming(false);
        setNotice(patch.title !== undefined ? '对话名称已更新。' : patch.archived ? '对话已归档，作品和生成结果仍保留。' : '对话已恢复。');
      }
    } catch (cause) { if (isCurrentSelection(intent)) setActionError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  const options = [...items];
  if (selected && !options.some(item => item.id === selected.id)) options.unshift(selected);

  return <aside className="agent-conversation-panel" aria-label="Agent 创作对话">
    <header className="agent-panel-heading"><div><h2>Agent 创作</h2><p>围绕本集内容，逐步完善作品。</p></div><Button type="text" aria-label="刷新对话" icon={<Icon name="refresh" size={16}/>} disabled={busy} loading={loading} onClick={() => { availability.refresh(); refresh(); }}/></header>
    {!availability.available ? <div className="agent-panel-unavailable" role="status">
      {availability.loading ? <Skeleton active title paragraph={{ rows: 3 }}/> : <><h3>{availability.reason}</h3><p>{availability.error || '你可以继续在提示词模式中创作。'}</p><Button onClick={availability.refresh}>重新检查</Button></>}
    </div> : <>
      <div className="agent-conversation-toolbar">
        <Select aria-label="当前对话" value={selected?.id ?? selectedId} showSearch optionFilterProp="label" loading={loading}
          placeholder="选择本集对话" disabled={busy} onChange={id => { setNotice(''); onSelect(id); }}
          options={options.map(item => ({ value: item.id, label: `${item.title}${item.archived ? '（已归档）' : ''}` }))}
          notFoundContent={loading ? '正在载入…' : '还没有本集对话'}/>
        <Button aria-label="新建对话" title="新建对话" icon={<Icon name="plus" size={16}/>} loading={busy && !selected} disabled={readOnly || busy} onClick={() => void create()}/>
        {selected && <Dropdown menu={{ items: [
          { key: 'rename', label: '重命名', disabled: readOnly || busy },
          { key: 'archive', label: selected.archived ? '恢复对话' : '归档对话', disabled: readOnly || busy || !selected.archived && isAgentRunActive(selected.last_run_status) },
        ], onClick: ({ key }) => { if (key === 'rename') { setTitle(selected.title); setRenaming(true); } else void update({ archived: !selected.archived }); } }} trigger={['click']}>
          <Button type="text" aria-label="对话操作" icon={<Icon name="more" size={16}/>} disabled={busy}/>
        </Dropdown>}
      </div>
      <div className="agent-conversation-list-controls"><Checkbox checked={includeArchived} disabled={busy} onChange={event => { setIncludeArchived(event.target.checked); setOffset(0); }}>显示已归档</Checkbox><span>仅你可见</span></div>
      {items.length < total && <Button className="agent-load-more" size="small" loading={loading} onClick={() => setOffset(items.length)}>更多对话（{items.length}/{total}）</Button>}
      {renaming && <form className="agent-rename-form" onSubmit={event => { event.preventDefault(); if (title.trim()) void update({ title: title.trim() }); }}>
        <label htmlFor="agent-conversation-title">对话名称</label><Input id="agent-conversation-title" autoFocus maxLength={120} value={title} disabled={busy} onChange={event => setTitle(event.target.value)}/>
        <div><Button htmlType="submit" type="primary" loading={busy} disabled={!title.trim()}>保存名称</Button><Button disabled={busy} onClick={() => setRenaming(false)}>取消</Button></div>
      </form>}
      {error && <Alert type="error" showIcon message="对话列表暂时无法载入" description={error} action={<Button size="small" onClick={refresh}>重试</Button>}/>}
      {actionError && <Alert type="error" showIcon message={actionError} action={<Button size="small" onClick={refresh} disabled={busy}>核对最新状态</Button>}/>}
      {notice && <p className="agent-inline-notice" role="status">{notice}</p>}
      <div className="agent-conversation-content">
        {loadingDetail ? <Skeleton active title paragraph={{ rows: 4 }}/> : detailError ? <Alert type="warning" showIcon message={detailError} action={<Button size="small" onClick={refresh}>重新载入</Button>}/>
          : selected ? <>
            <div className="agent-conversation-context"><span>当前分集</span><strong>{episodeTitle}</strong><span className="agent-conversation-state">{selected.archived ? '已归档' : agentRunLabel(selected.last_run_status)}</span></div>
            {selected.archived ? <div className="agent-conversation-empty"><Icon name="film" size={28}/><h3>这段对话已归档</h3><p>恢复对话后可继续使用；已有候选和作品不受影响。</p><Button disabled={readOnly || busy} loading={busy} onClick={() => void update({ archived: false })}>恢复对话</Button></div> :
              <AgentConversationRuntime key={selected.id} conversation={selected} draft={drafts[selected.id] ?? ''} readOnly={readOnly}
                beforeSend={beforeSend} navigationIntent={() => selection.current} onOpenArtifact={onOpenArtifact}
                onDraft={value => setDrafts(old => ({ ...old, [selected.id]: value }))}
                onRun={run => setSelected(old => old?.id === selected.id && old.last_run_status !== (run?.status ?? null) ? { ...old, last_run_status: run?.status ?? null } : old)}/>}
          </> : <div className="agent-conversation-empty"><Icon name="film" size={28}/><h3>{items.length ? '选择一段对话继续' : '给本集留一段创作对话'}</h3><p>对话仅你可见；写入项目的候选与作品按项目权限共享。</p><Button type="primary" loading={busy} disabled={readOnly || busy || loading} onClick={() => void create()}>新建对话</Button></div>}
      </div>
      <footer className="agent-panel-footnote">生成候选、采用内容与确认定稿是不同操作。</footer>
    </>}
  </aside>;
}
