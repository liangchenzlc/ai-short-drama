import { XProvider } from '@ant-design/x';
import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { AssistantConversationBody } from '@host/features/assistant/conversations/AssistantConversationBody';
import { AssistantHistory } from '@host/features/assistant/history/AssistantHistory';
import { accountStorage } from '@host/features/identity/storage/account-storage';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { ProjectAssistantConversation } from '@host/types/assistant/api';
import type { CreativeAssistantPanelProps } from '@host/types/assistant/panel/CreativeAssistantPanel';
import { Button, Skeleton, Tooltip } from 'antd';
import { History, MessageSquarePlus, Plus, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import './ai-assistant.css';
import './standard-assistant.css';
/** Each account restores its own conversation within the current project. */
export function CreativeAssistantPanel(props: CreativeAssistantPanelProps) {
  const { accountId, projectId, layout, open = true, readOnly = false, onClose, selectedConversationId } = props;
  const [conversation, setConversation] = useState<ProjectAssistantConversation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(false);
  const alive = useRef(true);
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const loadedSelection = useRef<{
    accountId: string;
    projectId: string;
    id: string;
  } | null>(null);
  const selectionKey = `assistant-conversation:v3:${projectId}`;
  const remember = (item: ProjectAssistantConversation) => {
    if (item.project_id !== latest.current.projectId) return;
    setConversation(item);
    loadedSelection.current = accountId ? { accountId, projectId, id: item.id } : null;
    if (accountId) {
      try {
        accountStorage(accountId).setItem(selectionKey, item.id);
      } catch {
        /* Server history remains authoritative. */
      }
    }
    latest.current.onConversationChange?.(item.id);
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    setConversation(null);
    setHistory(false);
  }, [accountId, projectId]);
  useEffect(() => {
    if (!accountId || !open) {
      ++request.current;
      controller.current?.abort();
      setLoading(false);
      return;
    }
    if (
      selectedConversationId &&
      loadedSelection.current?.accountId === accountId &&
      loadedSelection.current.projectId === projectId &&
      loadedSelection.current.id === selectedConversationId &&
      conversation?.id === selectedConversationId
    ) {
      setLoading(false);
      return;
    }
    const generation = ++request.current;
    controller.current?.abort();
    const read = new AbortController();
    controller.current = read;
    setLoading(true);
    setError('');
    (async () => {
      let stored: string | null = null;
      try {
        stored = accountStorage(accountId).getItem(selectionKey);
      } catch {
        /* Fall back to project history. */
      }
      let item: ProjectAssistantConversation | null = null;
      for (const preferred of new Set([selectedConversationId, stored].filter((id): id is string => !!id))) {
        try {
          const candidate = await assistantApi.globalConversation(preferred, read.signal);
          if (candidate.scope_version === 3 && candidate.project_id === projectId) {
            item = candidate;
            break;
          }
        } catch (cause) {
          if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        }
      }
      if (!item) item = (await assistantApi.globalConversations(projectId, 0, read.signal)).items[0] ?? null;
      if (read.signal.aborted || generation !== request.current) return;
      if (item) remember(item);
      else setConversation(null);
    })()
      .catch((cause) => {
        if (!read.signal.aborted && generation === request.current) setError(errorMessage(cause));
      })
      .finally(() => {
        if (!read.signal.aborted && generation === request.current) setLoading(false);
      });
    return () => read.abort();
  }, [accountId, projectId, selectedConversationId, open, revision]);
  async function create() {
    if (busy || sending || !accountId || readOnly) return;
    setBusy(true);
    setError('');
    const generation = ++request.current;
    controller.current?.abort();
    const scope = `assistant-project-new:${projectId}`;
    try {
      const key = await requestAttempt(scope, { project_id: projectId }, attemptStorage());
      const item = await assistantApi.createGlobalConversation(projectId, key);
      clearAttempt(scope, attemptStorage());
      if (alive.current && generation === request.current) remember(item);
    } catch (cause) {
      if (alive.current) setError(errorMessage(cause));
      throw cause;
    } finally {
      if (alive.current) {
        setBusy(false);
        setLoading(false);
      }
    }
  }
  return (
    <XProvider theme={{ token: { fontSize: 13 } }}>
      <section
        className={`ai-assistant-panel${layout === 'standard' ? ' ai-assistant-standard' : ''}`}
        aria-label="AI 创作助手"
        style={open ? undefined : { display: 'none' }}
      >
        <header className="ai-assistant-header">
          <div className="ai-assistant-heading">
            <h2>AI 创作助手</h2>
            {layout !== 'standard' && <span>{conversation?.title ?? '项目对话'}</span>}
          </div>
          <Tooltip title="新对话">
            <Button
              type="text"
              size="small"
              aria-label="新对话"
              disabled={busy || sending || loading || !accountId || readOnly}
              loading={busy}
              icon={layout === 'standard' ? <Plus size={18} /> : <MessageSquarePlus size={16} />}
              onClick={() => {
                void create().catch(() => {});
              }}
            />
          </Tooltip>
          <Tooltip title="对话记录">
            <Button
              type="text"
              size="small"
              aria-label="对话记录"
              disabled={!accountId || busy || sending}
              icon={<History size={layout === 'standard' ? 18 : 16} />}
              onClick={() => setHistory(true)}
            />
          </Tooltip>
          <Tooltip title="关闭助手">
            <Button
              type="text"
              size="small"
              aria-label="关闭助手"
              icon={<X size={layout === 'standard' ? 18 : 16} />}
              onClick={onClose}
            />
          </Tooltip>
        </header>
        {error &&
          (layout === 'standard' ? (
            <StudioFeedback
              scope="chat"
              type="error"
              title="AI 创作助手"
              message={error}
              noticeKey={`assistant-panel:${accountId}`}
              actions={
                <Button size="small" onClick={() => setRevision((value) => value + 1)}>
                  重新读取
                </Button>
              }
            />
          ) : (
            <div className="ai-assistant-notice" role="alert">
              <span>{error}</span>
              <Button size="small" onClick={() => setRevision((value) => value + 1)}>
                重新读取
              </Button>
            </div>
          ))}
        {!accountId ? (
          <p className="ai-assistant-meta">登录后可以使用 AI 创作助手。</p>
        ) : loading ? (
          <div className="ai-assistant-log">
            <Skeleton active paragraph={{ rows: 5 }} />
          </div>
        ) : conversation?.project_id === projectId ? (
          <AssistantConversationBody
            key={`${accountId}:${projectId}:${conversation.id}`}
            conversation={conversation}
            accountId={accountId}
            projectId={projectId}
            executionTarget={props.executionTarget}
            layout={layout}
            readOnly={readOnly || conversation.status !== 'active'}
            contextPreview={props.contextPreview}
            mentionReferences={readOnly ? [] : props.mentionReferences}
            prepareContext={props.prepareContext}
            onOpenModelSettings={props.onOpenModelSettings}
            onSendingChange={setSending}
          />
        ) : !error ? (
          <div className="ai-assistant-empty">
            <p>在当前项目中聊想法、完善内容，创作成果会保存到这里。</p>
            <Button type="primary" loading={busy} disabled={readOnly} onClick={() => void create().catch(() => {})}>
              新建对话
            </Button>
          </div>
        ) : null}
        {history && accountId && (
          <AssistantHistory
            key={`${accountId}:${projectId}`}
            accountId={accountId}
            projectId={projectId}
            layout={layout}
            currentId={conversation?.id ?? ''}
            onNew={create}
            onClose={() => setHistory(false)}
            onUpdated={(item) => setConversation((old) => (old?.id === item.id ? item : old))}
            onDeleted={(id) => {
              if (id === conversation?.id) {
                setConversation(null);
                try {
                  if (accountId) accountStorage(accountId).removeItem(selectionKey);
                } catch {
                  /* A deleted selection is rechecked on reload. */
                }
                setRevision((value) => value + 1);
              }
            }}
            onSelect={(id) => {
              assistantApi
                .globalConversation(id)
                .then((item) => {
                  if (alive.current) remember(item);
                })
                .catch((cause) => {
                  if (alive.current) setError(errorMessage(cause));
                });
            }}
          />
        )}
      </section>
    </XProvider>
  );
}
