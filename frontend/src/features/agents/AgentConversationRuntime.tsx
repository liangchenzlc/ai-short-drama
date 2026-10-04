import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Dropdown, Select, Skeleton, Tooltip } from 'antd';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentConversation, AgentMessageMode, AgentModel, AgentRun, AgentSendInput } from '../../api/types/agents';
import { confirmAction } from '../../components/ui/confirm';
import { agentRunLabel, isAgentRunActive, type CreationMode } from './agent-navigation';
import { agentTaskLabels, modelCanCollaborate, reviewParameterLabels, reviewTargetLabel } from './agent-events';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { useAgentRuntime } from './useAgentRuntime';
import { AgentMediaTaskComposer, AgentMediaTaskError, type AgentMediaTaskController } from './AgentMediaTaskComposer';
import { AgentContextComposer, type AgentContextController } from './AgentContextComposer';
import { AgentMessageBubble } from './AgentMessageBubble';
import { Dialog } from '../../components/ui/Dialog';
import { Icon } from '../../components/ui/Icon';

export interface AgentNavigationIntent { id?: string; mode: CreationMode; generation: number }
interface PendingSend { body: AgentSendInput; key: string; draft: string; intent: AgentNavigationIntent }
const finiteNumber = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

export function AgentConversationRuntime({ conversation, draft, onDraft, readOnly, beforeSend, navigationIntent, onRun, onOpenArtifact }: {
  conversation: AgentConversation; draft: string; onDraft: (value: string) => void; readOnly: boolean;
  beforeSend: () => Promise<boolean>; navigationIntent: () => AgentNavigationIntent; onRun: (run: AgentRun | null) => void;
  onOpenArtifact: (id: string, runId?: string, conversationId?: string) => void;
}) {
  const runtime = useAgentRuntime(conversation.id);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState<string>();
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [operation, setOperation] = useState<AgentMessageMode>('discuss');
  const [taskKind, setTaskKind] = useState<'auto' | 'image' | 'video'>('auto');
  const [taskBlockReason, setTaskBlockReason] = useState('');
  const mediaTask = useRef<AgentMediaTaskController>(null);
  const inputContext = useRef<AgentContextController>(null);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextBlockReason, setContextBlockReason] = useState('');
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [busy, setBusy] = useState<'send' | 'save' | 'verify' | 'inputs' | 'review' | 'stop' | ''>('');
  const [actionError, setActionError] = useState('');
  const [uncertain, setUncertain] = useState<PendingSend | null>(null);
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
  const ready = modelCanCollaborate(model) && !runtime.accessEnded;
  const activeRun = isAgentRunActive(runtime.run?.status);
  const pendingReview = runtime.run?.status === 'waiting_review' ? runtime.run.review : null;
  const awaitingArtifacts = runtime.run?.awaiting_artifact_ids ?? [];
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
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
    return alive.current && !current.current.readOnly && !current.current.accessEnded && now.id === intent.id && now.mode === intent.mode && now.generation === intent.generation;
  }
  async function verify() {
    if (locked.current || readOnly || activeRun || runtime.accessEnded || !model) return;
    const intent = current.current.navigationIntent();
    const config = model;
    if (!await confirmAction('能力校验最多会向这个文本模型发送 2 次请求，可能产生模型服务费用。校验工具协作与继续对话能力后，才能用于 Agent 创作。', { title: '校验 Agent 能力', confirmText: '开始校验' }) || !matches(intent)) return;
    locked.current = true; setBusy('verify'); setActionError('');
    try {
      const result = await agentsApi.verifyModel(config.id, config.row_version);
      if (alive.current) setModels(old => old.map(item => item.id === result.id ? result : item));
    } catch (cause) { if (matches(intent)) setActionError(`${errorMessage(cause)} 可以重新载入模型状态，核对校验结果。`); }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function declareInputs(input: { image: boolean; audio: boolean }) {
    if (locked.current || readOnly || activeRun || runtime.accessEnded || !model) return;
    const config = model;
    locked.current = true; setBusy('inputs'); setActionError('');
    try {
      const result = await agentsApi.updateModelInputs(config.id, { row_version: config.row_version, ...input });
      if (alive.current) setModels(previous => previous.map(item => item.id === result.id ? result : item));
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function send(retry?: PendingSend) {
    if (locked.current || contextBusy || (!retry && contextBlockReason) || readOnly || activeRun || !ready || (!retry && (!current.current.draft.trim() || uncertain))) return;
    const intent = current.current.navigationIntent();
    if (intent.mode !== 'agent' || intent.id !== conversation.id) return;
    const submitted = retry?.draft ?? current.current.draft;
    let body: AgentSendInput;
    locked.current = true; setBusy('save'); setActionError('');
    const scope = `agent-send:${conversation.id}`; const storage = attemptStorage();
    try {
      if (!retry && !inputContext.current) throw new Error('正在恢复消息上下文，请稍后发送。');
      body = retry?.body ?? { content: submitted.trim(), mode: operation, model_config_id: modelId, ...inputContext.current!.snapshot() };
      if (!await current.current.beforeSend()) {
        if (matches(intent)) setActionError('当前作品尚未保存成功。对话草稿已保留，请处理保存提示后再发送。');
        return;
      }
      if (!matches(intent)) return;
      if (!retry && operation === 'generate' && taskKind !== 'auto') {
        if (!mediaTask.current) throw new AgentMediaTaskError('正在准备媒体任务，请稍后重试。');
        const prepared = await mediaTask.current.prepare(submitted);
        if (!matches(intent) || !await confirmAction(prepared.confirmation, { title: '确认生成任务', confirmText: '确认并生成', className: 'agent-task-confirmation', danger: false }) || !matches(intent)) return;
        body = { ...body, task: prepared.task };
      }
      const key = retry?.key ?? await requestAttempt(scope, body, storage);
      if (!matches(intent)) return;
      const pending = { body, key, draft: submitted, intent };
      setBusy('send');
      try {
        const result = await agentsApi.send(conversation.id, body, key);
        if (!alive.current) return;
        clearAttempt(scope, storage); setUncertain(null); runtime.accept(result);
        if (matches(intent)) {
          inputContext.current?.sent(body.attachment_ids ?? []);
          if (current.current.draft === submitted) current.current.onDraft('');
        }
      } catch (cause) {
        if (!alive.current) return;
        if (!(cause instanceof ApiError) || !cause.status || cause.status >= 500) setUncertain(pending);
        if (matches(intent)) setActionError(errorMessage(cause));
      }
    } catch (cause) { if (matches(intent)) setActionError(cause instanceof AgentMediaTaskError ? cause.message : errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function control(action: 'stop' | 'approved' | 'rejected') {
    if (locked.current || readOnly || runtime.accessEnded || !runtime.run) return;
    const run = runtime.run; const review = run.review;
    const intent = current.current.navigationIntent();
    if (intent.mode !== 'agent') return;
    locked.current = true; setBusy(action === 'stop' ? 'stop' : 'review'); setActionError('');
    try {
      const result = action === 'stop' ? await agentsApi.stop(run.id) : review ? await agentsApi.review(run.id, review, action) : null;
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
      <span className="agent-connection-state">{runtime.connection === 'live' ? '实时连接' : runtime.connection === 'connecting' ? '正在连接…' : runtime.connection === 'reconnecting' ? '正在恢复连接…' : '连接已中断'}</span>
      {activeRun && <Button size="small" disabled={readOnly || !!busy} loading={busy === 'stop'} onClick={() => void control('stop')}>停止运行</Button>}
      <Button size="small" type="text" disabled={!!busy} onClick={() => { void runtime.reload(); runtime.reconnect(); }}>核对状态</Button>
    </div>}
    {(runtime.error || actionError) && <Alert className="agent-runtime-error" type="error" showIcon message={actionError || runtime.error} />}
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
        <div><Button type="primary" disabled={readOnly || runtime.accessEnded || !!busy} loading={busy === 'review'} onClick={() => void control('approved')}>批准并生成</Button><Button disabled={readOnly || runtime.accessEnded || !!busy} onClick={() => void control('rejected')}>拒绝计划</Button></div>
      </section>}
      {!!awaitingArtifacts.length && <section className="agent-awaiting-artifacts" aria-label="待采用候选"><h3>候选已准备好</h3><p>核对并采用后，明确选择继续当前对话。采用作品不会自动继续其他对话。</p>{awaitingArtifacts.map((id, index) => <Button key={id} disabled={runtime.accessEnded} onClick={() => onOpenArtifact(id, runtime.run?.id, conversation.id)}>核对候选 {index + 1}</Button>)}</section>}
      {runtime.run?.status === 'failed' && <p className="agent-run-failed" role="status">这次运行未完成，已有消息和候选仍保留。核对内容后，可以发起新的对话请求。</p>}
    </div>
    {newContent && <Button className="agent-new-content" size="small" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; pinned.current = true; setNewContent(false); }}>查看最新消息</Button>}
    <div className="agent-composer">
      <Dialog open={modelSettingsOpen} title="Agent 模型与执行设置" className="agent-context-dialog" canClose={!busy} onClose={() => setModelSettingsOpen(false)}><div className="agent-context-dialog-body">
      <section className="agent-model-settings" aria-label="协作模型设置">
        <label htmlFor="agent-collaboration-model">协作模型</label>
        <div className="agent-model-picker"><Select aria-label="Agent 协作模型" value={modelId} loading={modelsLoading} disabled={!!busy || activeRun}
          id="agent-collaboration-model"
          placeholder="选择文本模型" options={models.map(item => ({ value: item.id, label: item.name }))} onChange={setModelId}/>
          <Button size="small" disabled={readOnly || !!busy || activeRun || runtime.accessEnded || !model} loading={busy === 'verify'} onClick={() => void verify()}>校验能力</Button></div>
        <p>校验最多 2 次文本调用，可能收费。{modelCanCollaborate(model) ? `已通过协作校验；${model?.streaming === 'verified' ? '实时输出已验证。' : '实时输出未单独校验。'}` : '须通过工具协作与继续对话校验后才能发送。'}</p>
        {modelsError && <Alert type="error" message={modelsError}/>}<Button type="link" size="small" loading={modelsLoading} disabled={!!busy} onClick={() => void loadModels()}>重新载入模型状态</Button>
        {!modelsLoading && !models.length && <p>请先在 AI 配置中添加可用的文本模型。</p>}
        {model && <div className="agent-model-inputs"><p>模型输入能力：{model.input_capabilities?.evidence === 'model_family' ? '依据模型系列声明' : model.input_capabilities?.evidence === 'declared' ? '由你声明兼容能力' : '目前仅文字'}。声明能力不会发起模型请求。</p>
          <Checkbox disabled={readOnly || !!busy || activeRun} checked={!!model.input_capabilities?.image} onChange={event => void declareInputs({ image: event.target.checked, audio: !!model.input_capabilities?.audio })}>模型支持图片与视频采样帧</Checkbox>
          <Checkbox disabled={readOnly || !!busy || activeRun || model.protocol === 'openai_responses.v1'} checked={!!model.input_capabilities?.audio} onChange={event => void declareInputs({ image: !!model.input_capabilities?.image, audio: event.target.checked })}>模型支持音频输入（Chat 协议）</Checkbox>
        </div>}
      </section>
      {operation === 'generate' && <div className="agent-task-kind"><label>生成方式<Select aria-label="生成方式" value={taskKind} disabled={readOnly || runtime.accessEnded || !!busy || activeRun}
        options={[{ value: 'auto', label: '由对话制定计划' }, { value: 'image', label: '指定图片任务' }, { value: 'video', label: '指定视频任务' }]} onChange={value => { setTaskKind(value); setTaskBlockReason(''); }}/></label>
        {taskKind !== 'auto' && <AgentMediaTaskComposer key={taskKind} ref={mediaTask} conversation={conversation} kind={taskKind} disabled={readOnly || runtime.accessEnded || !!busy || activeRun} onBlockReason={setTaskBlockReason}/>}</div>}
      <Button type="primary" disabled={!!busy} onClick={() => setModelSettingsOpen(false)}>完成</Button></div></Dialog>
      <AgentContextComposer ref={inputContext} conversation={conversation} model={model} disabled={readOnly || runtime.accessEnded || !!busy || activeRun || !!uncertain} onBusy={setContextBusy} onBlockReason={setContextBlockReason}
        input={<><label htmlFor={`agent-message-${conversation.id}`} className="sr-only">创作要求</label>
      <textarea id={`agent-message-${conversation.id}`} rows={3} value={draft} maxLength={operation === 'generate' && taskKind !== 'auto' ? 4000 : 32000} disabled={readOnly} aria-describedby="agent-send-help" placeholder={operation === 'generate' && taskKind !== 'auto' ? '描述这项图片或视频任务的生成要求…' : '说说你想调整的故事、角色或镜头…'}
        onChange={event => onDraft(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !composing.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); void send(); } }}/></>}
        modelControl={<Tooltip title={model ? `当前模型：${model.name}` : '选择模型'}><Button type="text" aria-label="选择模型" disabled={!!busy || contextBusy || activeRun} icon={<Icon name="settings" size={18}/>} onClick={() => setModelSettingsOpen(true)}>模型</Button></Tooltip>}
        sendControl={<div className="agent-send-group"><Button type="primary" aria-label="发送" loading={busy === 'send' || busy === 'save'} disabled={readOnly || !!busy || contextBusy || !!contextBlockReason || activeRun || !ready || !draft.trim() || !!uncertain || operation === 'generate' && taskKind !== 'auto' && (!!taskBlockReason || draft.trim().length > 4000)} onClick={() => void send()}>发送</Button>
          <Dropdown trigger={['click']} disabled={readOnly || !!busy || activeRun || !!uncertain} menu={{ selectable: true, selectedKeys: [operation], items: [{ key: 'discuss', label: '讨论方向' }, { key: 'generate', label: '生成作品' }], onClick: ({ key }) => { setOperation(key as AgentMessageMode); if (key === 'generate') setModelSettingsOpen(true); } }}>
            <Button type="primary" aria-label="选择发送用途" disabled={readOnly || !!busy || activeRun || !!uncertain} icon={<Icon name="more" size={16}/>}/>
          </Dropdown></div>}/>
      {uncertain && <div className="agent-send-uncertain" role="status"><p>发送结果尚未确认，草稿已保留。先核对消息与运行状态，避免重复请求。</p>
        <Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>核对发送状态</Button>
        <Button size="small" disabled={readOnly || !!busy || activeRun || !ready} onClick={() => void send(uncertain)}>使用原请求重试</Button></div>}
      <div className="agent-send-row"><span id="agent-send-help">{busy === 'save' ? '正在保存并核对作品…' : activeRun ? '本段对话正在运行，草稿可继续编辑。' : operation === 'discuss' ? 'Enter 换行，Ctrl / ⌘ + Enter 发送。' : taskKind === 'auto' ? '先生成计划，批准后制作候选。' : '发送前确认对象与费用；生成要求最多 4000 字。'}</span>
      </div>
      {(calls !== null || outputTokens !== null || maxCalls !== null) && <p className="agent-usage">{calls !== null ? `已调用 ${calls} 次` : ''}{outputTokens !== null ? ` · 输出 ${outputTokens} token` : ''}{maxCalls !== null ? ` · 最多 ${maxCalls} 次文本协作` : ''}</p>}
    </div>
  </div>;
}
