import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Select, Skeleton, Tooltip } from 'antd';
import { ArrowUp, ArrowUpRight, Clapperboard, Square, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentModel } from '../../api/types/agents';
import type { AssistantConversation, AssistantMentionReference, AssistantMessageContext, AssistantReference, AssistantSendInput } from '../../api/types/assistant';
import { workflowStorage } from '../auth/account-storage';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { AgentContextComposer, type AgentContextController } from '../agents/AgentContextComposer';
import { isAgentRunActive } from '../agents/agent-navigation';
import { clearAssistantPendingSend, readAssistantDraft, readAssistantPendingSend, saveAssistantDraft, saveAssistantPendingSend, type AssistantPendingSend } from './assistant-draft';
import { AssistantMessage, AssistantReply } from './AssistantMessage';
import { AssistantMentionTextarea } from './AssistantMentionTextarea';
import { useAssistantRuntime } from './useAssistantRuntime';

export interface AssistantContextProps {
  contextPreview?: { key: string; label: string };
  mentionReferences?: readonly AssistantMentionReference[];
  prepareContext: (include: boolean, references?: readonly AssistantReference[]) => Promise<AssistantMessageContext | null>;
}

export function AssistantConversationBody({ conversation, accountId, readOnly, contextPreview, mentionReferences, prepareContext, onOpenModelSettings, onSendingChange }: AssistantContextProps & {
  conversation: AssistantConversation; accountId: string; readOnly: boolean;
  onOpenModelSettings: () => void; onSendingChange: (sending: boolean) => void;
}) {
  const runtime = useAssistantRuntime(conversation.id);
  const [restored] = useState(() => readAssistantDraft(accountId, conversation.id, workflowStorage()));
  const [restoredSend] = useState(() => readAssistantPendingSend(accountId, conversation.id, workflowStorage()));
  const [draft, setDraft] = useState(restored.text);
  const [skills, setSkills] = useState(restored.skills);
  const [draftError, setDraftError] = useState('');
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState<string | undefined>(restored.modelId);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [modelRevision, setModelRevision] = useState(0);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextBlockReason, setContextBlockReason] = useState('');
  const [includeContext, setIncludeContext] = useState(restored.contextKey === contextPreview?.key ? restored.includeContext ?? true : true);
  const [references, setReferences] = useState<AssistantMentionReference[]>(restored.contextKey === contextPreview?.key ? restored.references ?? [] : []);
  const [busy, setBusy] = useState<'save' | 'send' | 'stop' | ''>('');
  const [actionError, setActionError] = useState(restoredSend.error);
  const [uncertain, setUncertain] = useState<AssistantPendingSend | null>(restoredSend.pending);
  const [newContent, setNewContent] = useState(false);
  const inputContext = useRef<AgentContextController>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  const previousContextKey = useRef(contextPreview?.key);
  const current = useRef({ draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, references, accessEnded: runtime.accessEnded });
  current.current = { draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, references, accessEnded: runtime.accessEnded };
  const active = isAgentRunActive(runtime.run?.status);
  const model = models.find(item => item.id === modelId);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onSendingChange(!!busy); return () => onSendingChange(false); }, [busy, onSendingChange]);
  useEffect(() => {
    if (previousContextKey.current !== contextPreview?.key) { setIncludeContext(true); setReferences([]); previousContextKey.current = contextPreview?.key; }
  }, [contextPreview?.key]);
  useEffect(() => {
    setDraftError(saveAssistantDraft(accountId, conversation.id, { version: 1, text: draft, modelId, skills, contextKey: contextPreview?.key, includeContext, references }, workflowStorage())
      ? '' : '浏览器未能保存对话草稿，请在离开前复制当前输入。');
  }, [accountId, conversation.id, draft, modelId, skills, contextPreview?.key, includeContext, references]);
  useEffect(() => {
    const controller = new AbortController(); setModelsLoading(true); setModelsError('');
    agentsApi.models(controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setModels(page.items); setModelId(old => page.items.some(item => item.id === old) ? old : page.preferred_id ?? page.items[0]?.id);
    }).catch(cause => { if (!controller.signal.aborted) setModelsError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setModelsLoading(false); });
    return () => controller.abort();
  }, [modelRevision]);
  useLayoutEffect(() => {
    const node = transcript.current; if (!node) return;
    if (prepend.current) { node.scrollTop = prepend.current.top + node.scrollHeight - prepend.current.height; prepend.current = null; }
    else if (pinned.current) node.scrollTop = node.scrollHeight;
    else setNewContent(true);
  }, [runtime.messages, runtime.delta, runtime.run?.status]);
  async function send(retry?: AssistantPendingSend) {
    if (locked.current || contextBusy || readOnly || runtime.loading || runtime.accessEnded || restoredSend.error
      || !retry && (contextBlockReason || !model || !current.current.draft.trim() || uncertain)) return;
    const submitted = retry?.draft ?? current.current.draft;
    const captured = current.current;
    const matches = () => alive.current && !current.current.readOnly && !current.current.accessEnded
      && current.current.contextKey === captured.contextKey && current.current.includeContext === captured.includeContext
      && current.current.references === captured.references;
    const attemptScope = `assistant-send:${conversation.id}`;
    locked.current = true; setBusy('save'); setActionError('');
    try {
      if (!retry && !inputContext.current) throw new ApiError('正在恢复消息上下文，请稍后发送。', 'assistant_context_loading');
      const input = retry ? null : inputContext.current!.snapshot();
      let context: AssistantMessageContext | null = null;
      if (!retry) {
        try { context = await captured.prepareContext(captured.includeContext, captured.references.map(({ name: _name, ...reference }) => reference)); }
        catch (cause) {
          if (cause instanceof Error && !(cause instanceof ApiError) && /^(当前|画布|模型偏好|保存期间|作品|引用)/.test(cause.message)) throw new ApiError(cause.message, 'assistant_context_blocked');
          throw cause;
        }
      }
      if (context && (context.selected?.length ?? 0) > 16) throw new ApiError('一条消息最多引用 16 个对象，请减少选择后再发送。', 'assistant_context_limit');
      if (!matches()) { if (alive.current) setActionError('当前作品或引用已改变，消息草稿已保留，请核对后再次发送。'); return; }
      const body: AssistantSendInput = retry?.body ?? { content: submitted.trim(), model_config_id: modelId, context, ...input };
      const key = retry?.key ?? await requestAttempt(attemptScope, body, attemptStorage());
      if (!matches()) return;
      const pending: AssistantPendingSend = { version: 1, body, key, draft: submitted };
      const storage = workflowStorage();
      if (!saveAssistantPendingSend(accountId, conversation.id, pending, storage)) {
        const existing = readAssistantPendingSend(accountId, conversation.id, storage);
        setUncertain(existing.pending); throw new ApiError(existing.error || '浏览器无法保存待核对的发送记录，本次尚未发送。请允许本机存储后重试。', 'assistant_recovery_unavailable');
      }
      setBusy('send');
      try {
        const result = await assistantApi.send(conversation.id, body, key);
        const cleared = clearAssistantPendingSend(accountId, conversation.id, key, storage);
        if (cleared) clearAttempt(attemptScope, attemptStorage());
        if (!alive.current) {
          const saved = readAssistantDraft(accountId, conversation.id, storage);
          if (saved.text === submitted) saveAssistantDraft(accountId, conversation.id, { ...saved, text: '' }, storage);
        }
        if (!alive.current) return;
        runtime.accept(result); setUncertain(cleared ? null : pending);
        if (!cleared) setActionError('消息已受理，但本机记录尚未清理。请使用原请求核对。');
        inputContext.current?.sent(body.attachment_ids ?? []);
        if (current.current.draft === submitted) setDraft('');
        pinned.current = true;
      } catch (cause) {
        const rejected = !retry && cause instanceof ApiError && !!cause.status && cause.status >= 400 && cause.status < 500;
        const cleared = rejected && clearAssistantPendingSend(accountId, conversation.id, key, storage);
        if (cleared) clearAttempt(attemptScope, attemptStorage());
        if (alive.current) { setUncertain(cleared ? null : pending); setActionError(errorMessage(cause)); }
      }
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function stop(runId: string) {
    if (locked.current || readOnly || runtime.accessEnded) return;
    locked.current = true; setBusy('stop'); setActionError('');
    try { const result = await assistantApi.stop(runId); if (alive.current) { runtime.setRun(result); void runtime.reload(); } }
    catch (cause) { if (alive.current) { setActionError(errorMessage(cause)); void runtime.reload(); } }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  const blocked = readOnly || runtime.loading || runtime.accessEnded || !!busy || contextBusy || !!contextBlockReason || !!uncertain || !!restoredSend.error || !model || !draft.trim();
  return <div className="ai-assistant-runtime">
    {runtime.error || actionError || draftError ? <div className="ai-assistant-notice" role="alert"><span>{actionError || runtime.error || draftError}</span><Button size="small" onClick={() => { runtime.reconnect(); }}>核对状态</Button></div> : null}
    {modelsError || !modelsLoading && !models.length ? <div className="ai-assistant-notice" role="status"><span>{modelsError || '请先添加并启用文本模型。'}</span><Button size="small" onClick={onOpenModelSettings}>模型配置</Button><Button size="small" onClick={() => setModelRevision(value => value + 1)}>重新读取</Button></div> : null}
    {runtime.connection === 'reconnecting' ? <p className="ai-assistant-connection" role="status">正在恢复连接，已发送的消息继续处理。</p> : null}
    <div ref={transcript} className="ai-assistant-log" aria-label="助手消息" tabIndex={0} onScroll={event => {
      const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      if (pinned.current) setNewContent(false);
    }}>
      {runtime.messages.length < runtime.total ? <Button size="small" loading={runtime.olderLoading} onClick={() => {
        const node = transcript.current; if (node) prepend.current = { height: node.scrollHeight, top: node.scrollTop }; void runtime.older();
      }}>载入更早消息</Button> : null}
      {runtime.loading ? <Skeleton active title={false} paragraph={{ rows: 5 }}/>
        : runtime.messages.length ? runtime.messages.map(message => <AssistantMessage key={message.id} message={message}/>)
          : <div className="ai-assistant-empty"><Clapperboard className="ai-assistant-empty-icon" aria-hidden="true"/><h3>{runtime.accessEnded ? '对话暂不可访问' : '把想法聊清楚'}</h3><p>{runtime.accessEnded ? '恢复登录和项目访问后可重新读取。' : '一起梳理故事、画面和创作方向。'}</p>{!runtime.accessEnded ? ['帮我梳理故事的节奏与冲突', '一起完善角色的动机与关系', '为当前作品提供创作建议'].map(text => <button key={text} className="ai-assistant-starter" type="button" disabled={readOnly} onClick={() => setDraft(text)}><span>{text}</span><ArrowUpRight size={15}/></button>) : null}</div>}
      {runtime.run?.status === 'running' ? runtime.delta ? <AssistantReply text={runtime.delta}/> : <p className="ai-assistant-meta" role="status">正在思考…</p> : null}
      {runtime.run?.status === 'failed' ? <p className="ai-assistant-failed" role="status">{runtime.run.error?.message || '这次回复未完成，已有消息仍保留。请核对模型和输入后继续。'}</p> : null}
      {runtime.run?.status === 'cancelled' ? <p className="ai-assistant-meta">这一条已经停下了。</p> : null}
    </div>
    {newContent ? <Button className="ai-assistant-new-content" size="small" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; pinned.current = true; setNewContent(false); }}>查看最新消息</Button> : null}
    <footer className="ai-assistant-composer">
      <div className="ai-assistant-chips">
        {contextPreview && includeContext ? <span className="ai-assistant-chip" title={contextPreview.label}>{contextPreview.label}<button type="button" aria-label="这条消息不带当前作品" disabled={!!busy} onClick={() => setIncludeContext(false)}><X size={12}/></button></span>
          : contextPreview ? <button className="ai-assistant-chip" type="button" disabled={!!busy} onClick={() => setIncludeContext(true)}>添加当前作品</button> : null}
        {references.map(reference => <span key={`${reference.kind}:${reference.id}`} className="ai-assistant-chip">{reference.name}<button type="button" aria-label={`移除引用${reference.name}`} disabled={!!busy} onClick={() => setReferences(old => old.filter(item => item !== reference))}><X size={12}/></button></span>)}
      </div>
      <AgentContextComposer ref={inputContext} conversation={conversation} attachmentApi={assistantApi} dialogClassName="ai-assistant-context-dialog" initialSkills={restored.skills} onSkills={setSkills}
        disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain} onBusy={setContextBusy} onBlockReason={setContextBlockReason}
        input={<div className="ai-assistant-input"><AssistantMentionTextarea projectId={conversation.project_id} references={mentionReferences} value={draft} disabled={readOnly || runtime.accessEnded} onChange={setDraft} onSubmit={() => { if (!blocked) void send(); }} onReference={async reference => {
          if (uncertain || busy) { setActionError('请先核对原消息，再添加新的引用。'); return false; }
          if (reference.kind === 'asset') return await inputContext.current?.reference('asset', reference.id) ?? false;
          if (references.length >= 16 && !references.some(item => item.kind === reference.kind && item.id === reference.id)) { setActionError('一条消息最多引用 16 个对象。'); return false; }
          setReferences(old => old.some(item => item.kind === reference.kind && item.id === reference.id) ? old : [...old, reference]); return true;
        }}/></div>}
        modelControl={<Select className="ai-assistant-model" variant="borderless" size="small" aria-label="助手模型" value={modelId} loading={modelsLoading} disabled={!!busy || contextBusy || !!uncertain} placeholder="选择模型" options={models.map(item => ({ value: item.id, label: item.name }))} onChange={setModelId}/>}
        sendControl={<Tooltip title="Enter 发送 · Shift + Enter 换行"><Button className="ai-assistant-send" shape="circle" type="primary" aria-label="发送" loading={busy === 'send' || busy === 'save'} disabled={blocked} icon={<ArrowUp size={16}/>} onClick={() => void send()}/></Tooltip>}/>
      {readOnly ? <span className="ai-assistant-meta">当前项目只读，可以查看已有对话。</span> : null}
      {busy === 'save' ? <span className="ai-assistant-meta" role="status">正在保存并核对作品…</span> : null}
      {active && runtime.run ? <div className="ai-assistant-run-state" role="status"><span>{runtime.run.status === 'queued' ? '消息已排队' : '正在回复，可继续发送补充消息'}</span><Button size="small" icon={<Square size={12}/>} disabled={readOnly || !!busy} loading={busy === 'stop'} onClick={() => void stop(runtime.run!.id)}>停止</Button></div> : null}
      {runtime.queuedRuns.length ? <div className="ai-assistant-queued" aria-label="排队消息">{runtime.queuedRuns.filter(run => run.id !== runtime.run?.id).map(run => <div key={run.id}><span>排队位置 {run.queue_position || '等待中'}</span><Button type="text" size="small" disabled={readOnly || !!busy} onClick={() => void stop(run.id)}>取消排队</Button></div>)}</div> : null}
      {uncertain ? <div className="ai-assistant-uncertain" role="status"><p>发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。</p><Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>核对状态</Button><Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>使用原请求核对</Button></div> : null}
    </footer>
  </div>;
}
