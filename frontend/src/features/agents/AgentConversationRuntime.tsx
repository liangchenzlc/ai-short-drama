import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Select, Skeleton, Tooltip } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentConversation, AgentModel, AgentRun, AgentSendInput, ConversationScope } from '../../api/types/agents';
import { agentRunLabel, isAgentRunActive, type CreationMode } from './agent-navigation';
import { agentTaskLabels, reviewParameterLabels, reviewTargetLabel } from './agent-events';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { useAgentRuntime } from './useAgentRuntime';
import { AgentContextComposer, type AgentContextController } from './AgentContextComposer';
import { AgentMessageBubble } from './AgentMessageBubble';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';
import { clearAgentPendingSend, readAgentDraft, readAgentPendingSend, saveAgentDraft, saveAgentPendingSend, type AgentPendingSend } from './agent-draft';
import { workflowStorage } from '../auth/account-storage';

export interface AgentNavigationIntent { id?: string; mode: CreationMode; generation: number; navigation: number }
const finiteNumber = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

export function AgentConversationRuntime({ conversation, scope, accountId, readOnly, beforeSend, navigationIntent, onRun, onOpenArtifact }: {
  conversation: AgentConversation; scope: ConversationScope; accountId: string; readOnly: boolean;
  beforeSend: () => Promise<boolean>; navigationIntent: () => AgentNavigationIntent; onRun: (run: AgentRun | null) => void;
  onOpenArtifact: (id: string, runId?: string, conversationId?: string) => void;
}) {
  const runtime = useAgentRuntime(conversation.id, scope);
  const [restored] = useState(() => readAgentDraft(accountId, conversation.id, workflowStorage()));
  const [restoredSend] = useState(() => readAgentPendingSend(accountId, conversation.id, workflowStorage()));
  const [draft, onDraft] = useState(restored.text);
  const [skillRefs, setSkillRefs] = useState(restored.skills);
  const [draftError, setDraftError] = useState('');
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState<string | undefined>(restored.modelId);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const inputContext = useRef<AgentContextController>(null);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextBlockReason, setContextBlockReason] = useState('');
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [busy, setBusy] = useState<'send' | 'save' | 'review' | 'stop' | ''>('');
  const [actionError, setActionError] = useState(restoredSend.error);
  const [uncertain, setUncertain] = useState<AgentPendingSend | null>(restoredSend.pending);
  const [newContent, setNewContent] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const composing = useRef(false);
  const locked = useRef(false);
  const alive = useRef(true);
  const current = useRef({ draft, beforeSend, navigationIntent, onDraft, onRun, readOnly, accessEnded: runtime.accessEnded });
  current.current = { draft, beforeSend, navigationIntent, onDraft, onRun, readOnly, accessEnded: runtime.accessEnded };
  const model = models.find(item => item.id === modelId);
  const ready = !!model && !runtime.accessEnded;
  const activeRun = isAgentRunActive(runtime.run?.status);
  const pendingReview = runtime.run?.status === 'waiting_review' ? runtime.run.review : null;
  const awaitingArtifacts = runtime.run?.awaiting_artifact_ids ?? [];
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    setDraftError(saveAgentDraft(accountId, conversation.id, { version: 1, text: draft, modelId, skills: skillRefs }, workflowStorage())
      ? '' : '浏览器未能保存对话草稿，请在离开前复制当前输入。');
  }, [accountId, conversation.id, draft, modelId, skillRefs]);
  useEffect(() => { if (!runtime.loading) current.current.onRun(runtime.run); }, [runtime.run, runtime.loading]);
  useLayoutEffect(() => {
    const node = transcript.current; if (!node) return;
    if (prepend.current) {
      node.scrollTop = prepend.current.top + node.scrollHeight - prepend.current.height; prepend.current = null;
    } else if (pinned.current) node.scrollTop = node.scrollHeight;
    else setNewContent(true);
  }, [runtime.messages, runtime.delta]);
  async function loadModels(signal?: AbortSignal) {
    setModelsLoading(true); setModelsError('');
    try {
      const page = await agentsApi.models(signal);
      if (!alive.current || signal?.aborted) return;
      setModels(page.items); setModelId(old => page.items.some(item => item.id === old) ? old : page.preferred_id ?? page.items[0]?.id);
    } catch (cause) { if (alive.current && !signal?.aborted) setModelsError(errorMessage(cause)); }
    finally { if (alive.current && !signal?.aborted) setModelsLoading(false); }
  }
  useEffect(() => { const controller = new AbortController(); void loadModels(controller.signal); return () => controller.abort(); }, []);
  function matches(intent: AgentNavigationIntent) {
    const now = current.current.navigationIntent();
    return alive.current && !current.current.readOnly && !current.current.accessEnded && now.id === intent.id && now.mode === intent.mode && now.generation === intent.generation && now.navigation === intent.navigation;
  }
  async function send(retry?: AgentPendingSend) {
    if (locked.current || contextBusy || (!retry && contextBlockReason) || readOnly || runtime.accessEnded || restoredSend.error || (!retry && (!ready || !current.current.draft.trim() || uncertain))) return;
    const intent = current.current.navigationIntent();
    if (intent.mode !== 'agent' || intent.id !== conversation.id) return;
    const submitted = retry?.draft ?? current.current.draft;
    let body: AgentSendInput;
    locked.current = true; setBusy('save'); setActionError('');
    const attemptScope = `agent-send:${conversation.id}`; const storage = attemptStorage();
    try {
      if (!retry && !inputContext.current) throw new Error('正在恢复消息上下文，请稍后发送。');
      body = retry?.body ?? { content: submitted.trim(), model_config_id: modelId, expected_scope: scope, ...inputContext.current!.snapshot() };
      if (!await current.current.beforeSend()) {
        if (matches(intent)) setActionError('当前作品尚未保存成功。对话草稿已保留，请处理保存提示后再发送。');
        return;
      }
      if (!matches(intent)) return;
      const key = retry?.key ?? await requestAttempt(attemptScope, body, storage);
      if (!matches(intent)) return;
      const pending: AgentPendingSend = { version: 1, body, key, draft: submitted };
      const recoveryStorage = workflowStorage();
      if (!saveAgentPendingSend(accountId, conversation.id, pending, recoveryStorage)) {
        const existing = readAgentPendingSend(accountId, conversation.id, recoveryStorage);
        if (existing.pending) setUncertain(existing.pending);
        if (matches(intent)) setActionError(existing.error || '浏览器无法保存待核对的发送记录，本次尚未发送。请允许本机存储后重试，草稿已保留。');
        return;
      }
      setBusy('send');
      try {
        const result = await agentsApi.send(conversation.id, body, key);
        const cleared = clearAgentPendingSend(accountId, conversation.id, key, recoveryStorage);
        if (cleared) clearAttempt(attemptScope, storage);
        if (!alive.current) return;
        setUncertain(cleared ? null : pending); runtime.accept(result);
        if (!cleared && matches(intent)) setActionError('消息已受理，但本机发送记录尚未清理。请使用原请求核对，暂不发送新请求。');
        if (matches(intent)) {
          inputContext.current?.sent(body.attachment_ids ?? []);
          if (current.current.draft === submitted) current.current.onDraft('');
        }
      } catch (cause) {
        const rejected = !retry && cause instanceof ApiError && !!cause.status && cause.status >= 400 && cause.status < 500;
        const cleared = rejected && clearAgentPendingSend(accountId, conversation.id, key, recoveryStorage);
        if (cleared) clearAttempt(attemptScope, storage);
        if (!alive.current) return;
        setUncertain(cleared ? null : pending);
        if (matches(intent)) setActionError(errorMessage(cause));
      }
    } catch (cause) { if (matches(intent)) setActionError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function control(action: 'stop' | 'approved' | 'rejected') {
    if (locked.current || readOnly || runtime.accessEnded || !runtime.run) return;
    const run = runtime.run; const review = run.review;
    const intent = current.current.navigationIntent();
    if (intent.mode !== 'agent') return;
    locked.current = true; setBusy(action === 'stop' ? 'stop' : 'review'); setActionError('');
    try {
      const result = action === 'stop' ? await agentsApi.stop(run.id, scope) : review ? await agentsApi.review(run.id, review, action, scope) : null;
      if (alive.current && result) { runtime.setRun(result); void runtime.reload(); }
    } catch (cause) { if (matches(intent)) { setActionError(errorMessage(cause)); void runtime.reload(); } }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  const calls = finiteNumber(runtime.run?.usage.decision_calls);
  const outputTokens = finiteNumber(runtime.run?.usage.output_tokens);
  const maxCalls = finiteNumber(runtime.run?.budget.decision_calls);
  return <div className="agent-runtime">
    {(activeRun || awaitingArtifacts.length > 0 || runtime.connection !== 'live' && runtime.connection !== 'connecting') && <div className="agent-runtime-status">
      <span role="status">{awaitingArtifacts.length ? '等待采用候选' : runtime.run ? agentRunLabel(runtime.run.status) : '可以开始对话'}</span>
      {!!runtime.queuedRuns.length && <span>已排队 {runtime.queuedRuns.length} 条补充要求</span>}
      {runtime.run?.waiting_reason && <span>{runtime.run.waiting_reason}</span>}
      <span className="agent-connection-state">{runtime.connection === 'live' ? '实时连接' : runtime.connection === 'connecting' ? '正在连接…' : runtime.connection === 'reconnecting' ? '正在恢复连接…' : '连接已中断'}</span>
      {activeRun && <Button size="small" disabled={readOnly || !!busy} loading={busy === 'stop'} onClick={() => void control('stop')}>停止运行</Button>}
      <Button size="small" type="text" disabled={!!busy} onClick={() => { void runtime.reload(); runtime.reconnect(); }}>核对状态</Button>
    </div>}
    {(runtime.error || actionError || draftError || restoredSend.error) && <Alert className="agent-runtime-error" type="error" showIcon message={actionError || runtime.error || draftError || restoredSend.error} />}
    <div ref={transcript} className="agent-transcript" aria-label="创作消息" tabIndex={0} onScroll={event => {
      const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      if (pinned.current) setNewContent(false);
    }}>
      {runtime.messages.length < runtime.total && <Button className="agent-older-messages" size="small" loading={runtime.olderLoading} onClick={() => {
        const node = transcript.current; if (node) prepend.current = { height: node.scrollHeight, top: node.scrollTop };
        void runtime.older();
      }}>载入更早消息</Button>}
      {runtime.loading ? <Skeleton active title={false} paragraph={{ rows: 5 }}/> : runtime.messages.length ? runtime.messages.map(message =>
        <AgentMessageBubble key={message.id} message={message} onOpenArtifact={onOpenArtifact}/>) : <div className="agent-runtime-empty"><h3>{runtime.accessEnded ? '对话暂不可访问' : '从本集作品开始'}</h3><p>{runtime.accessEnded ? '消息已从当前界面移除。恢复访问后可重新连接。' : '讨论故事方向，或描述想生成的内容。候选准备好后由你核对采用。'}</p></div>}
      {runtime.run?.status === 'running' && runtime.delta && <article className="agent-message is-assistant is-streaming"><div className="agent-message-author">Agent 正在回复</div><p>{runtime.delta}</p></article>}
      {pendingReview && <section className="agent-plan" aria-label="待确认创作计划">
        <h3>{pendingReview.title || '确认创作计划'}</h3><p>{pendingReview.summary}</p>
        <ol>{pendingReview.steps.map(step => <li key={step.id}><strong>{agentTaskLabels[step.kind] ?? '创作内容'}{step.count > 1 ? ` × ${step.count}` : ''}</strong>
          <p>{step.instructions}</p><span>{step.model_name || '使用服务端选定模型'} · {reviewTargetLabel(step)}</span>
          {!!reviewParameterLabels(step.parameters).length && <span>{reviewParameterLabels(step.parameters).join(' · ')}</span>}</li>)}</ol>
        <p className="agent-plan-fee">批准后会按计划调用生成服务，可能产生模型费用；结果仍需核对后采用。</p>
        <div><Button type="primary" disabled={readOnly || runtime.accessEnded || !!busy || runtime.queuedRuns.length > 0} loading={busy === 'review'} onClick={() => void control('approved')}>批准并生成</Button><Button disabled={readOnly || runtime.accessEnded || !!busy || runtime.queuedRuns.length > 0} onClick={() => void control('rejected')}>拒绝计划</Button></div>
      </section>}
      {!!awaitingArtifacts.length && <section className="agent-awaiting-artifacts" aria-label="待采用候选"><h3>候选已准备好</h3><p>核对并采用后，明确选择继续当前对话。采用作品不会自动继续其他对话。</p>{awaitingArtifacts.map((id, index) => <Button key={id} disabled={runtime.accessEnded} onClick={() => onOpenArtifact(id, runtime.run?.id, conversation.id)}>核对候选 {index + 1}</Button>)}</section>}
      {runtime.run?.status === 'failed' && <p className="agent-run-failed" role="status">{runtime.run.error?.message || '这次运行未完成，已有消息和候选仍保留。请核对模型、输入或原任务后继续。'}</p>}
    </div>
    {newContent && <Button className="agent-new-content" size="small" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; pinned.current = true; setNewContent(false); }}>查看最新消息</Button>}
    <div className="agent-composer">
      <Dialog open={modelSettingsOpen} title="选择 Agent 模型" className="agent-context-dialog" canClose={!busy} onClose={() => setModelSettingsOpen(false)}><div className="agent-context-dialog-body">
        <section className="agent-model-settings" aria-label="协作模型设置"><label htmlFor="agent-collaboration-model">协作模型</label>
          <div className="agent-model-picker"><Select aria-label="Agent 协作模型" id="agent-collaboration-model" value={modelId} loading={modelsLoading} disabled={!!busy} placeholder="选择文本模型" options={models.map(item => ({ value: item.id, label: item.name }))} onChange={setModelId}/></div>
          <p>选择只保存本次对话的模型。发送后，Agent 会根据你的要求协助创作。</p>
          {modelsError && <Alert type="error" message={modelsError}/>}<Button type="link" size="small" loading={modelsLoading} disabled={!!busy} onClick={() => void loadModels()}>重新载入模型</Button>
          {!modelsLoading && !models.length && <p>请先在 AI 配置中添加并启用文本模型。</p>}
        </section><Button type="primary" disabled={!!busy} onClick={() => setModelSettingsOpen(false)}>完成</Button>
      </div></Dialog>
      <AgentContextComposer ref={inputContext} conversation={conversation} scope={scope} initialSkills={restored.skills} onSkills={setSkillRefs} disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain} onBusy={setContextBusy} onBlockReason={setContextBlockReason}
        input={<><label htmlFor={`agent-message-${conversation.id}`} className="sr-only">创作要求</label>
      <textarea id={`agent-message-${conversation.id}`} rows={3} value={draft} maxLength={32000} disabled={readOnly} aria-describedby="agent-send-help" placeholder="说说你想调整的内容，或描述想制作的候选…"
        onChange={event => onDraft(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !composing.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); void send(); } }}/></>}
        modelControl={<Tooltip title={model ? `当前模型：${model.name}` : '选择模型'}><Button type="text" aria-label="选择模型" disabled={!!busy || contextBusy} icon={<Icon name="settings" size={18}/>} onClick={() => setModelSettingsOpen(true)}>模型</Button></Tooltip>}
        sendControl={<Button type="primary" aria-label="发送" loading={busy === 'send' || busy === 'save'} disabled={readOnly || !!busy || contextBusy || !!contextBlockReason || !ready || !draft.trim() || !!uncertain || !!restoredSend.error} onClick={() => void send()}>发送</Button>}/>
      {uncertain && <div className="agent-send-uncertain" role="status"><p>发送结果尚未确认，草稿已保留。先核对消息与运行状态，避免重复请求。</p>
        <Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>核对发送状态</Button>
        <Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>使用原请求重试</Button></div>}
      <div className="agent-send-row"><span id="agent-send-help">{busy === 'save' ? '正在保存并核对作品…' : activeRun ? '可以发送补充要求，Agent 会按当前进度处理。' : 'Enter 换行，Ctrl / ⌘ + Enter 发送。'}</span></div>
      {(calls !== null || outputTokens !== null || maxCalls !== null) && <p className="agent-usage">{calls !== null ? `已调用 ${calls} 次` : ''}{outputTokens !== null ? ` · 输出 ${outputTokens} token` : ''}{maxCalls !== null ? ` · 最多 ${maxCalls} 次文本协作` : ''}</p>}
    </div>
  </div>;
}
