import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Skeleton } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { errorMessage } from '../../api/http';
import type { AgentConversation, ConversationScope } from '../../api/types/agents';
import { AgentConversationRuntime } from './AgentConversationRuntime';

export function LegacyCreationRecord({ projectId, episodeId, id, accountId, readOnly, beforeSend, onBack, onClose, onConversation, onOpenArtifact }: {
  projectId: string; episodeId: string; id: string; accountId: string; readOnly: boolean;
  onBack: () => void; onClose: () => void; onConversation: (conversation: AgentConversation | null) => void;
  beforeSend: () => Promise<boolean>;
  onOpenArtifact: (id: string, runId?: string, conversationId?: string, scope?: ConversationScope) => void;
}) {
  const [conversation, setConversation] = useState<AgentConversation | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const callback = useRef(onConversation); callback.current = onConversation;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setConversation(null); callback.current(null);
    agentsApi.conversation(id, controller.signal).then(item => {
      if (controller.signal.aborted) return;
      if (item.project_id !== projectId || item.episode_id !== episodeId || item.scope_version === 2) throw new Error('这段历史记录不属于当前分集。');
      setConversation(item); callback.current(item);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); callback.current(null); };
  }, [projectId, episodeId, id, revision]);
  const scope = conversation ? conversation.scope_version === 1 && conversation.stage && conversation.subject_type && conversation.subject_id && conversation.task_type
    ? { stage: conversation.stage, subject_type: conversation.subject_type, subject_id: conversation.subject_id, task_type: conversation.task_type }
    : undefined : undefined;
  return <aside className="agent-conversation-panel legacy-creation-record" aria-label="历史创作记录">
    <header className="agent-panel-heading"><div><h2>历史创作记录</h2><span className="agent-panel-scope">{conversation?.title || '仅你可见'}</span></div><Button type="text" aria-label="关闭历史创作记录" onClick={onClose}>关闭</Button></header>
    <Button className="legacy-assistant-return" type="link" onClick={onBack}>返回 AI 创作助手</Button>
    <p className="agent-panel-footnote">历史消息只供查看。已有计划、任务与候选可继续处理。</p>
    {loading ? <Skeleton active paragraph={{ rows: 5 }}/> : error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setRevision(value => value + 1)}>重新载入</Button>}/> : conversation ? <AgentConversationRuntime key={id} conversation={conversation} scope={scope} accountId={accountId} readOnly={readOnly} historyOnly beforeSend={beforeSend} navigationIntent={() => ({ id, generation: 0, navigation: 0 })} onRun={run => {
      const next = { ...conversation, last_run_status: run?.status ?? null };
      setConversation(next); callback.current(next);
    }} onOpenArtifact={(artifactId, runId, conversationId) => onOpenArtifact(artifactId, runId, conversationId, scope)}/> : null}
  </aside>;
}
