import { useEffect, useRef, useState } from 'react';
import { Button, Skeleton, Tooltip } from 'antd';
import { History, MessageSquarePlus, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { ApiError, errorMessage } from '../../api/http';
import type { AssistantConversation } from '../../api/types/assistant';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { AssistantConversationBody, type AssistantContextProps } from './AssistantConversationBody';
import { AssistantHistory } from './AssistantHistory';
import { LegacyAssistantMessages } from './LegacyAssistantMessages';
import './ai-assistant.css';

export interface CreativeAssistantPanelProps extends AssistantContextProps {
  accountId?: string;
  projectId: string;
  projectTitle: string;
  open?: boolean;
  readOnly?: boolean;
  onClose: () => void;
  selectedConversationId?: string | null;
  onConversationChange?: (id: string) => void;
  onOpenModelSettings: () => void;
  onOpenLegacy?: (id: string) => void;
  legacyConversationId?: string | null;
  onCloseLegacy?: () => void;
}

/** 共用内容直接沿用画布助手的标题、消息与输入几何；停靠和抽屉由两端适配器负责。 */
export function CreativeAssistantPanel(props: CreativeAssistantPanelProps) {
  const { accountId, projectId, projectTitle, open = true, readOnly = false, onClose, selectedConversationId, onOpenModelSettings } = props;
  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(false);
  const [legacyId, setLegacyId] = useState<string | null>(null);
  const alive = useRef(true);
  const request = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const loadedScope = useRef('');
  const latest = useRef(props); latest.current = props;
  useEffect(() => { alive.current = true; return () => { alive.current = false; loadController.current?.abort(); }; }, []);
  useEffect(() => {
    const scope = `${accountId ?? ''}:${projectId}`;
    const scopeChanged = loadedScope.current !== scope;
    if (scopeChanged) { loadedScope.current = scope; setConversation(null); setLegacyId(null); setHistory(false); }
    if (!accountId || !open) { ++request.current; loadController.current?.abort(); setLoading(false); return; }
    if (!scopeChanged && !revision && conversation && (selectedConversationId === conversation.id || !selectedConversationId)) return;
    const generation = ++request.current;
    loadController.current?.abort();
    const controller = new AbortController(); loadController.current = controller; setLoading(true); setError('');
    const promise = selectedConversationId ? assistantApi.conversation(selectedConversationId, controller.signal) : assistantApi.resolveConversation(projectId, controller.signal);
    promise.then(item => {
      if (controller.signal.aborted || generation !== request.current) return;
      if (item.project_id !== projectId || item.scope_version !== 2) throw new ApiError('这条对话不属于当前项目助手。', 'assistant_scope_invalid');
      setConversation(item); latest.current.onConversationChange?.(item.id);
    }).catch(cause => { if (!controller.signal.aborted) { setConversation(null); setError(errorMessage(cause)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [accountId, projectId, selectedConversationId, open, revision]);
  async function create() {
    if (busy || sending || readOnly || !accountId) return;
    setBusy(true); setError('');
    const generation = ++request.current; loadController.current?.abort();
    const scope = `assistant-new:${projectId}`;
    try {
      const key = await requestAttempt(scope, { project_id: projectId }, attemptStorage());
      const item = await assistantApi.createConversation(projectId, key); clearAttempt(scope, attemptStorage());
      if (!alive.current || generation !== request.current) return;
      setConversation(item); latest.current.onConversationChange?.(item.id);
    } catch (cause) { if (alive.current) { setError(errorMessage(cause)); throw cause; } }
    finally { if (alive.current) { setBusy(false); setLoading(false); } }
  }
  const fallbackLegacy = props.legacyConversationId ?? legacyId;
  return <section className="ai-assistant-panel" aria-label="AI 创作助手" style={open ? undefined : { display: 'none' }}>
    <header className="ai-assistant-header"><div className="ai-assistant-heading"><h2>AI 创作助手</h2><span title={projectTitle}>{projectTitle}</span></div>
      <Tooltip title="新对话"><Button type="text" size="small" aria-label="新对话" disabled={readOnly || busy || sending || loading || !accountId} loading={busy} icon={<MessageSquarePlus size={16}/>} onClick={() => { void create().catch(() => {}); }}/></Tooltip>
      <Tooltip title="对话记录"><Button type="text" size="small" aria-label="对话记录" disabled={!conversation || busy || sending} icon={<History size={16}/>} onClick={() => setHistory(true)}/></Tooltip>
      <Tooltip title="关闭助手"><Button type="text" size="small" aria-label="关闭助手" icon={<X size={16}/>} onClick={() => { ++request.current; loadController.current?.abort(); onClose(); }}/></Tooltip>
    </header>
    {error ? <div className="ai-assistant-notice" role="alert"><span>{error}</span><Button size="small" onClick={() => setRevision(value => value + 1)}>重新读取</Button></div> : null}
    {!accountId ? <p className="ai-assistant-meta">登录后可以使用 AI 创作助手。</p>
      : loading ? <div className="ai-assistant-log"><Skeleton active paragraph={{ rows: 5 }}/></div>
        : conversation ? <AssistantConversationBody key={`${accountId}:${conversation.id}`} conversation={conversation} accountId={accountId} readOnly={readOnly || conversation.archived}
          contextPreview={props.contextPreview} mentionReferences={props.mentionReferences} prepareContext={props.prepareContext} onOpenModelSettings={onOpenModelSettings} onSendingChange={setSending}/>
          : !error ? <p className="ai-assistant-meta">暂无对话，点击“新对话”开始。</p> : null}
    {history && conversation ? <AssistantHistory projectId={projectId} currentId={conversation.id} readOnly={readOnly} onNew={create} onUpdated={item => setConversation(old => old?.id === item.id ? item : old)} onClose={() => setHistory(false)} onSelect={id => {
      if (latest.current.onConversationChange) latest.current.onConversationChange(id);
      else { setConversation(null); assistantApi.conversation(id).then(item => { if (alive.current && item.project_id === projectId) setConversation(item); }).catch(cause => { if (alive.current) setError(errorMessage(cause)); }); }
    }} onLegacy={id => { if (props.onOpenLegacy) props.onOpenLegacy(id); else setLegacyId(id); }}/> : null}
    {fallbackLegacy ? <LegacyAssistantMessages key={fallbackLegacy} id={fallbackLegacy} projectId={projectId} onClose={() => { setLegacyId(null); props.onCloseLegacy?.(); }}/> : null}
  </section>;
}
