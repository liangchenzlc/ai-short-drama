import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Popover, Select, Skeleton, Tooltip } from 'antd';
import { Bubble, Prompts, Welcome } from '@ant-design/x';
import { ArrowUp, ArrowUpRight, BookOpen, Clapperboard, Plus, Settings2, Square, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentModel } from '../../api/types/agents';
import type { GlobalAssistantConversation, AssistantMentionReference, AssistantMessageContext, AssistantReference, AssistantTaskSendInput, AssistantControlInput, TaskExecutionTarget, TaskMediaLimits } from '../../api/types/assistant';
import { workflowStorage } from '../auth/account-storage';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { AgentContextComposer, type AgentContextController } from '../agents/AgentContextComposer';
import { isAgentRunActive } from '../agents/agent-navigation';
import { clearAssistantPendingAction, clearAssistantPendingSend, readAssistantDraft, readAssistantPendingAction, readAssistantPendingSend, saveAssistantDraft, saveAssistantPendingAction, saveAssistantPendingSend, type AssistantPendingAction, type AssistantPendingSend } from './assistant-draft';
import { AssistantMessage, AssistantReply } from './AssistantMessage';
import { AssistantMentionTextarea } from './AssistantMentionTextarea';
import { useAssistantRuntime } from './useAssistantRuntime';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { Dialog } from '../../components/ui/Dialog';
import { AssistantTaskTrace } from './AssistantTaskTrace';
import { AssistantSubjects, type AssistantSubjectsController } from './AssistantSubjects';
import { AssistantAttachments, type AssistantAttachmentsController } from './AssistantAttachments';
import { AssistantTaskSettings } from './AssistantTaskSettings';
import { AssistantSkillPicker, type AssistantSkillChoice } from './AssistantSkillPicker';
import type { AssistantAttachmentDraft, AssistantSubjectDraft } from '../../api/types/assistant';
import { AssistantDiscussionHistory, type HistoryRequest } from './AssistantDiscussionHistory';
import { AssistantBatchUndo } from './AssistantBatchUndo';

export interface AssistantContextProps {
  contextPreview?: { key: string; label: string };
  mentionReferences?: readonly AssistantMentionReference[];
  prepareContext: (include: boolean, references?: readonly AssistantReference[]) => Promise<AssistantMessageContext | null>;
}

export function AssistantConversationBody({ conversation, accountId, projectId, executionTarget, layout, readOnly, contextPreview, mentionReferences, prepareContext, onOpenModelSettings, onSendingChange }: AssistantContextProps & {
  conversation: GlobalAssistantConversation; accountId: string; projectId: string; executionTarget?: TaskExecutionTarget; layout?: 'standard'; readOnly: boolean;
  onOpenModelSettings: () => void; onSendingChange: (sending: boolean) => void;
}) {
  const runtime = useAssistantRuntime(conversation.id);
  const [historyRequest, setHistoryRequest] = useState<HistoryRequest | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyPending, setHistoryPending] = useState(false);
  const [undoRequest, setUndoRequest] = useState<string | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoPending, setUndoPending] = useState(false);
  const [restored] = useState(() => readAssistantDraft(accountId, conversation.id, workflowStorage()));
  const [restoredSend] = useState(() => readAssistantPendingSend(accountId, conversation.id, workflowStorage()));
  const [restoredControl] = useState(() => readAssistantPendingAction(accountId, conversation.id, 'control', workflowStorage()));
  const [draft, setDraft] = useState(restored.text);
  const [skills, setSkills] = useState(restored.skills);
  const [skillsDirty, setSkillsDirty] = useState(restored.skillsDirty ?? restored.skills.length > 0);
  const [draftError, setDraftError] = useState('');
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState<string | undefined>(restored.modelId);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [modelRevision, setModelRevision] = useState(0);
  const [contextBusy, setContextBusy] = useState(false);
  const [contextBlockReason, setContextBlockReason] = useState('');
  const [includeContext, setIncludeContext] = useState(false);
  const [references, setReferences] = useState<AssistantMentionReference[]>(restored.contextKey === contextPreview?.key ? restored.references ?? [] : []);
  const [selectionDirty, setSelectionDirty] = useState((restored.references?.length ?? 0) > 0);
  const [subjectDraft, setSubjectDraft] = useState<AssistantSubjectDraft | null>(restored.subjectDraft ?? null);
  const subjectController = useRef<AssistantSubjectsController>(null);
  const [attachmentDraft, setAttachmentDraft] = useState<AssistantAttachmentDraft | null>(restored.attachmentDraft ?? null);
  const attachmentController = useRef<AssistantAttachmentsController>(null);
  const [taskId, setTaskId] = useState(restored.taskId ?? '');
  const [independent, setIndependent] = useState(restored.newTask ?? false);
  const task = independent ? null : runtime.tasks.find(item => item.id === taskId && item.status !== 'succeeded') ?? runtime.task;
  const selectedSkills = skillsDirty ? skills : task?.skills ?? [];
  const [target, setTarget] = useState<TaskExecutionTarget | null>(restored.executionTarget ?? null);
  const [memoryProjects, setMemoryProjects] = useState<string[]>(restored.memoryProjectIds ?? []);
  const [mediaLimits, setMediaLimits] = useState<TaskMediaLimits>(restored.mediaLimits ?? { images: null, videos: null, audio: null });
  const [settingsDirty, setSettingsDirty] = useState(restored.settingsDirty ?? false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [composerVersion, setComposerVersion] = useState(0);
  const [pendingControl, setPendingControl] = useState<Extract<AssistantPendingAction, { kind: 'control' }> | null>(restoredControl.pending?.kind === 'control' ? restoredControl.pending : null);
  const [busy, setBusy] = useState<'save' | 'send' | 'stop' | 'control' | ''>('');
  const [actionError, setActionError] = useState(restoredSend.error || restoredControl.error);
  const [uncertain, setUncertain] = useState<AssistantPendingSend | null>(restoredSend.pending);
  const [newContent, setNewContent] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const runEntry = useRef<HTMLButtonElement>(null);
  const inputContext = useRef<AgentContextController>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  const previousContextKey = useRef(contextPreview?.key);
  const current = useRef({ draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, references, subjectDraft, attachmentDraft, accessEnded: runtime.accessEnded });
  current.current = { draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, references, subjectDraft, attachmentDraft, accessEnded: runtime.accessEnded };
  const active = isAgentRunActive(runtime.run?.status);
  const model = models.find(item => item.id === modelId);
  const previousTask = useRef(task?.id);
  useEffect(() => {
    if (!task && previousTask.current && !skillsDirty) { setSkills([]); setComposerVersion(value => value + 1); }
    previousTask.current = task?.id;
  }, [task?.id, skillsDirty]);
  useEffect(() => { if (!active && !runtime.queuedRuns.length && runtime.connection !== 'reconnecting') setRunDetailsOpen(false); }, [active, runtime.queuedRuns.length, runtime.connection]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onSendingChange(!!busy || historyBusy || undoBusy); return () => onSendingChange(false); }, [busy, historyBusy, undoBusy, onSendingChange]);
  useEffect(() => {
    if (previousContextKey.current !== contextPreview?.key) { setIncludeContext(false); setReferences([]); setSelectionDirty(false); previousContextKey.current = contextPreview?.key; }
  }, [contextPreview?.key]);
  useEffect(() => {
    setDraftError(saveAssistantDraft(accountId, conversation.id, { version: 1, text: draft, modelId, skills, skillsDirty, contextKey: contextPreview?.key, includeContext, references, subjectDraft, attachmentDraft, settingsDirty, taskId, newTask: independent, executionTarget: target, memoryProjectIds: memoryProjects, mediaLimits }, workflowStorage())
      ? '' : '浏览器未能保存对话草稿，请在离开前复制当前输入。');
  }, [accountId, conversation.id, draft, modelId, skills, skillsDirty, contextPreview?.key, includeContext, references, subjectDraft, attachmentDraft, settingsDirty, taskId, independent, target, memoryProjects, mediaLimits]);
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
    if (locked.current || historyBusy || historyPending || undoBusy || undoPending || contextBusy || readOnly || runtime.loading || runtime.accessEnded || restoredSend.error || restoredControl.error
      || !retry && (contextBlockReason || !task && !model || !current.current.draft.trim() || uncertain || pendingControl)) return;
    const submitted = retry?.draft ?? current.current.draft;
    const captured = current.current;
    const matches = () => alive.current && !current.current.readOnly && !current.current.accessEnded
      && current.current.contextKey === captured.contextKey && current.current.includeContext === captured.includeContext
      && current.current.references === captured.references && current.current.subjectDraft === captured.subjectDraft
      && current.current.attachmentDraft === captured.attachmentDraft;
    const attemptScope = `assistant-send:${conversation.id}`;
    locked.current = true; setBusy('save'); setActionError('');
    try {
      if (!retry && !inputContext.current) throw new ApiError('正在恢复消息上下文，请稍后发送。', 'assistant_context_loading');
      const input = retry ? null : inputContext.current!.snapshot();
      let context: AssistantMessageContext | null = null;
      if (!retry) {
        try { if (selectionDirty) context = await captured.prepareContext(false, captured.references.map(({ name: _name, ...reference }) => reference)); }
        catch (cause) {
          if (cause instanceof Error && !(cause instanceof ApiError) && /^(当前|画布|模型偏好|保存期间|作品|引用)/.test(cause.message)) throw new ApiError(cause.message, 'assistant_context_blocked');
          throw cause;
        }
      }
      if (context && (context.selected?.length ?? 0) > 16) throw new ApiError('一条消息最多引用 16 个对象，请减少选择后再发送。', 'assistant_context_limit');
      const selection = retry ? null : await subjectController.current?.prepare(context);
      const attachmentSelection = retry ? null : await attachmentController.current?.prepare();
      if (!matches()) { if (alive.current) setActionError('当前作品或引用已改变，消息草稿已保留，请核对后再次发送。'); return; }
      const body: AssistantTaskSendInput = (retry?.body as AssistantTaskSendInput | undefined) ?? {
        content: submitted.trim(), ...input,
        ...(task ? { task_id: task.id } : { new_task: true, model_config_id: modelId }),
        ...(selection ? { subject_selection: selection } : {}),
        ...(attachmentSelection ? { attachment_selection: attachmentSelection } : {}),
        ...(!task || settingsDirty ? { execution_target: target, memory_project_ids: memoryProjects, media_limits: mediaLimits } : {}),
      };
      if (!retry) {
        if (skillsDirty) body.skills = skills.map(({ id, content_version }) => ({ id, content_version }));
        else delete body.skills;
      }
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
        const result = await assistantApi.sendTask(conversation.id, body, key);
        const cleared = clearAssistantPendingSend(accountId, conversation.id, key, storage);
        if (cleared) clearAttempt(attemptScope, attemptStorage());
        if (!alive.current) {
          const saved = readAssistantDraft(accountId, conversation.id, storage);
          if (saved.text === submitted) saveAssistantDraft(accountId, conversation.id, { ...saved, text: '' }, storage);
        }
        if (!alive.current) return;
        runtime.accept(result); setUncertain(cleared ? null : pending);
        setTaskId(result.task.id); setIndependent(false); setSelectionDirty(false); setSubjectDraft(null); setAttachmentDraft(null); setSettingsDirty(false); setReferences([]);
        setSkillsDirty(false); setTarget(null); setMemoryProjects([]); setMediaLimits({ images: null, videos: null, audio: null });
        if (!cleared) setActionError('消息已受理，但本机记录尚未清理。请使用原请求核对。');
        inputContext.current?.sent(body.attachment_ids ?? []);
        setSkills([]); setComposerVersion(value => value + 1);
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
  async function control(controlTaskId: string, body: AssistantControlInput, retry = pendingControl) {
    if (locked.current || readOnly || runtime.accessEnded || restoredControl.error || uncertain) return;
    locked.current = true; setBusy(body.kind === 'stop' ? 'stop' : 'control'); setActionError('');
    const scope = `assistant-control:${controlTaskId}:${body.kind}`;
    try {
      const request: Extract<AssistantPendingAction, { kind: 'control' }> = retry ?? { version: 1, kind: 'control', targetId: controlTaskId, body, key: await requestAttempt(scope, body, attemptStorage()) };
      const storage = workflowStorage();
      if (!saveAssistantPendingAction(accountId, conversation.id, request, storage)) throw new ApiError('浏览器无法保存待核对的控制记录，本次尚未发送。请允许本机存储后重试。', 'assistant_recovery_unavailable');
      setPendingControl(request);
      try {
        const result = await assistantApi.controlTask(request.targetId, request.body, request.key);
        const cleared = clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage);
        if (cleared) clearAttempt(scope, attemptStorage());
        if (alive.current) {
          setPendingControl(cleared ? null : request); runtime.acceptControl(result);
          if (!cleared) setActionError('控制已受理，但本机记录尚未清理。请使用原请求核对。');
          if (request.body.kind === 'redirect' && current.current.draft === request.body.content) setDraft('');
        }
      } catch (cause) {
        if (!retry && cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500
          && clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage)) {
          clearAttempt(scope, attemptStorage()); if (alive.current) setPendingControl(null);
        }
        throw cause;
      }
    }
    catch (cause) { if (alive.current) { setActionError(errorMessage(cause)); void runtime.reload(); } }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  const stop = (id: string) => {
    const actual = runtime.tasks.some(item => item.id === id) ? id : [runtime.run, ...runtime.queuedRuns].find(item => item?.id === id)?.task_id;
    if (actual) return control(actual, { kind: 'stop' });
  };
  async function chooseReferences(selected: readonly AssistantMentionReference[]) {
    if (locked.current || uncertain || pendingControl || readOnly || !subjectController.current) return false;
    locked.current = true; setBusy('save'); setActionError('');
    try {
      await subjectController.current.addLocal(selected);
      if (alive.current) { setReferences([]); setSelectionDirty(false); }
      return true;
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)); return false; }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  function newTask() {
    setIndependent(true); setTaskId(''); setReferences([]); setSelectionDirty(false); setSubjectDraft(null); setSkills([]);
    setSkillsDirty(false);
    setAttachmentDraft(null);
    setTarget(null); setMemoryProjects([]); setMediaLimits({ images: null, videos: null, audio: null }); setSettingsDirty(false);
    setComposerVersion(value => value + 1);
  }
  const contextDisabled = readOnly || !!busy || historyBusy || historyPending || undoBusy || undoPending || !!uncertain || !!pendingControl || !!restoredControl.error;
  const contextChips = <div className="ai-assistant-chips" aria-label="本次消息引用">
        {mentionReferences?.length ? <button className="ai-assistant-chip" type="button" disabled={contextDisabled} onClick={() => void chooseReferences(mentionReferences)}><BookOpen size={14}/>选入当前主体</button> : null}
        {references.length > 2 ? <details className="ai-assistant-reference-details"><summary>另有 {references.length} 项引用</summary><div className="ai-assistant-chips">{references.map(reference => <span key={`${reference.kind}:${reference.id}`} className="ai-assistant-chip"><span>{reference.name}</span><button type="button" aria-label={`移除引用${reference.name}`} disabled={contextDisabled} onClick={() => setReferences(old => old.filter(item => item !== reference))}><X size={12}/></button></span>)}</div></details>
          : references.map(reference => <span key={`${reference.kind}:${reference.id}`} className="ai-assistant-chip"><span>{reference.name}</span><button type="button" aria-label={`移除引用${reference.name}`} disabled={contextDisabled} onClick={() => { setSelectionDirty(true); setReferences(old => old.filter(item => item !== reference)); }}><X size={12}/></button></span>)}
      </div>;
  const blocked = readOnly || runtime.loading || runtime.accessEnded || !!busy || historyBusy || historyPending || undoBusy || undoPending || contextBusy || !!contextBlockReason || !!uncertain || !!pendingControl || !!restoredSend.error || !!restoredControl.error || !task && !model || !draft.trim();
  const modelControl = <Select className="ai-assistant-model" classNames={layout === 'standard' ? { popup: { root: 'standard-assistant-select-popup standard-creation-popup' } } : undefined} variant="borderless" size="small" aria-label="助手模型" value={modelId} loading={modelsLoading} disabled={!!busy || contextBusy || !!uncertain || readOnly || runtime.accessEnded} placeholder="选择模型" options={models.map(item => ({ value: item.id, label: item.name }))} onChange={setModelId}/>;
  const sendControl = <Tooltip title="Enter 发送 · Shift + Enter 换行"><Button className="ai-assistant-send" shape="circle" type="primary" aria-label="发送" loading={busy === 'send' || busy === 'save'} disabled={blocked} icon={<ArrowUp size={16}/>} onClick={() => void send()}/></Tooltip>;
  const queuedRuns = runtime.queuedRuns.filter(run => run.id !== runtime.run?.id);
  const runStatus = active && runtime.run ? runtime.run.status === 'queued' ? '消息已排队' : '正在回复，可继续发送补充消息' : '';
  const reconnecting = runtime.connection === 'reconnecting' && !uncertain && !runtime.error;
  function closeRunDetails() { setRunDetailsOpen(false); requestAnimationFrame(() => runEntry.current?.focus()); }
  const runDetails = <div onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRunDetails(); } }}>
    {active && runtime.run && <div className="ai-assistant-run-state" role="status"><span>{runStatus}</span><Button size="small" icon={<Square size={12}/>} disabled={readOnly || !!busy} loading={busy === 'stop'} onClick={() => void stop(runtime.run!.id)}>停止</Button></div>}
    {queuedRuns.length > 0 && <div className="ai-assistant-queued" aria-label="排队消息">{queuedRuns.map(run => <div key={run.id}><span>排队位置 {run.queue_position || '等待中'}</span><Button type="text" size="small" disabled={readOnly || !!busy} onClick={() => void stop(run.id)}>取消排队</Button></div>)}</div>}
    {reconnecting && <><p role="status">正在恢复连接，已发送的消息继续处理。</p><Button size="small" onClick={() => runtime.reconnect()}>重新连接</Button></>}
  </div>;
  return <div className={`ai-assistant-runtime${toolsOpen ? ' is-standard-tools-open' : ''}`}>
    {runtime.error || actionError || draftError ? layout === 'standard'
      ? <StudioFeedback type="error" title="AI 创作助手" message={actionError || runtime.error || draftError} noticeKey={`assistant-error:${conversation.id}`} actions={<Button size="small" onClick={() => runtime.reconnect()}>核对状态</Button>}/>
      : <div className="ai-assistant-notice" role="alert"><span>{actionError || runtime.error || draftError}</span><Button size="small" onClick={() => { runtime.reconnect(); }}>核对状态</Button></div> : null}
    {modelsError || !modelsLoading && !models.length ? layout === 'standard'
      ? <StudioFeedback type={modelsError ? 'error' : 'warning'} quiet={!modelsError} persistent title="助手模型" message={modelsError || '请先添加并启用文本模型。'} noticeKey={`assistant-model:${conversation.id}`} actions={<><Button size="small" onClick={onOpenModelSettings}>模型配置</Button><Button size="small" onClick={() => setModelRevision(value => value + 1)}>重新读取</Button></>}/>
      : <div className="ai-assistant-notice" role="status"><span>{modelsError || '请先添加并启用文本模型。'}</span><Button size="small" onClick={onOpenModelSettings}>模型配置</Button><Button size="small" onClick={() => setModelRevision(value => value + 1)}>重新读取</Button></div> : null}
    {reconnecting && layout !== 'standard' ? <p className="ai-assistant-connection" role="status">正在恢复连接，已发送的消息继续处理。</p> : null}
    <div ref={transcript} className="ai-assistant-log" aria-label="助手消息" tabIndex={0} onScroll={event => {
      const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      if (pinned.current) setNewContent(false);
    }}>
      {runtime.messages.length < runtime.total ? <Button size="small" loading={runtime.olderLoading} onClick={() => {
        const node = transcript.current; if (node) prepend.current = { height: node.scrollHeight, top: node.scrollTop }; void runtime.older();
      }}>载入更早消息</Button> : null}
      {runtime.loading ? <Skeleton active title={false} paragraph={{ rows: 5 }}/>
        : !runtime.messages.length && !active ? <div className="ai-assistant-empty"><Welcome variant="borderless" icon={<Clapperboard size={26} aria-hidden="true"/>} title={runtime.accessEnded ? '对话暂不可访问' : '把想法聊清楚'} description={runtime.accessEnded ? '恢复登录和项目访问后可重新读取。' : '一起梳理故事、画面和创作方向。'}/>{!runtime.accessEnded ? <Prompts vertical items={['帮我梳理故事的节奏与冲突', '一起完善角色的动机与关系', '为当前作品提供创作建议'].map(text => ({ key: text, label: <button className="ai-assistant-starter" type="button" disabled={readOnly} onClick={() => setDraft(text)}><span>{text}</span><ArrowUpRight size={15}/></button>, disabled: readOnly }))}/> : null}</div> : null}
      {!runtime.loading && (runtime.messages.length > 0 || active) ? <Bubble.List className="ai-assistant-bubbles" autoScroll={false} items={[
        ...runtime.messages.flatMap(message => {
          const ownerTask = runtime.tasks.find(item => item.id === message.task_id);
          const inputState = runtime.inputs.find(item => item.message_id === message.id);
          const first = ownerTask && runtime.messages.find(item => item.task_id === ownerTask.id)?.id === message.id;
          return [{ key: message.id, role: message.role, placement: message.role === 'user' ? 'end' as const : 'start' as const, variant: 'borderless' as const, className: message.role === 'user' ? 'ai-assistant-bubble-user' : 'ai-assistant-bubble-reply', content: <><AssistantMessage message={message} layout={layout} historyDisabled={contextDisabled || runtime.loading || runtime.accessEnded} onHistory={layout === 'standard' && message.task_id ? mode => setHistoryRequest({ message, mode }) : undefined}/>{inputState && !inputState.applied_plan_revision && <small className="ai-assistant-meta">{ownerTask?.status === 'stopped' ? '已保留，明确继续后处理' : '已保存，等待任务边界处理'}</small>}</> },
            ...(first ? [{ key: `trace:${ownerTask.id}`, role: 'assistant', placement: 'start' as const, variant: 'borderless' as const, className: 'ai-assistant-bubble-reply', content: <AssistantTaskTrace task={ownerTask} operations={runtime.operations} layout={layout} onUndo={setUndoRequest} undoDisabled={contextDisabled || runtime.accessEnded}/> }] : [])];
        }),
        ...(runtime.run?.status === 'running' ? [{ key: `stream:${runtime.run.id}`, role: 'assistant', placement: 'start' as const, variant: 'borderless' as const, className: 'ai-assistant-bubble-reply', content: <article className="ai-assistant-turn ai-assistant-streaming" data-run-id={runtime.run.id}>{runtime.delta ? <AssistantReply text={runtime.delta}/> : <p className="ai-assistant-meta" role="status">正在回复…</p>}</article> }] : []),
      ]}/> : null}
      {runtime.run?.status === 'failed' ? <p className="ai-assistant-failed" role="status">{runtime.run.error?.message || '这次回复未完成，已有消息仍保留。请核对模型和输入后继续。'}</p> : null}
      {runtime.run?.status === 'cancelled' ? <p className="ai-assistant-meta">这一条已经停下了。</p> : null}
      {runtime.hasMoreTasks && <Button size="small" loading={runtime.olderLoading} onClick={() => void runtime.olderTasks()}>载入更早任务</Button>}
    </div>
    {newContent ? <Button className="ai-assistant-new-content" size="small" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; pinned.current = true; setNewContent(false); }}>查看最新消息</Button> : null}
    <footer className="ai-assistant-composer">
      <div className="ai-assistant-task-controls" aria-label="当前任务">
        <Select size="small" aria-label="消息所属任务" value={independent || !task ? 'new' : task.id} disabled={contextDisabled} options={[
          { value: 'new', label: '独立新任务' },
          ...runtime.tasks.filter(item => item.status !== 'succeeded').map(item => ({ value: item.id, label: item.goal })),
        ]} onChange={value => { if (value === 'new') newTask(); else { setIndependent(false); setTaskId(value); setReferences([]); setSelectionDirty(false); setSubjectDraft(null); setAttachmentDraft(null); setSkills([]); setSkillsDirty(false); setSettingsDirty(false); setTarget(null); setMemoryProjects([]); setMediaLimits({ images: null, videos: null, audio: null }); } }}/>
        <Tooltip title="参考主体、作品记忆和写入目标分别设置"><Button size="small" type="text" aria-label="任务设置" icon={<Settings2 size={14}/>} disabled={contextDisabled} onClick={() => {
          if (task && !settingsDirty) { setTarget(task.execution_target); setMemoryProjects(task.memory_project_ids); setMediaLimits(task.media_limits); }
          setSettingsOpen(true);
        }}/></Tooltip>
        {task && ['stopped', 'paused_error', 'paused_budget'].includes(task.status)
          ? <Button size="small" aria-label="明确继续" aria-busy={busy === 'control'} disabled={contextDisabled || !!pendingControl} loading={busy === 'control'} onClick={() => void control(task.id, { kind: 'resume', expected_control_seq: task.control_seq })}>明确继续</Button>
          : task && <Button size="small" aria-label="停止" aria-busy={busy === 'stop'} disabled={contextDisabled || !!pendingControl || task.control_state === 'stop_pending'} loading={busy === 'stop'} onClick={() => void stop(task.id)}>停止</Button>}
      </div>
      {task && <p className="ai-assistant-task-status" role="status">{task.control_state === 'stop_pending' ? '当前工具完整结束后停止' : task.control_state === 'redirect_pending' ? '当前工具完整结束后切换方向' : task.status === 'stopped' ? '任务已停止；补充会保留，明确继续才恢复' : task.status === 'paused_budget' ? '达到数量或执行上限，已暂停' : task.status === 'paused_error' ? '任务已暂停，请核对结果后明确继续' : `补充当前任务 · ${task.execution_target ? '已有明确写入目标' : '尚未指定写入目标'}`}</p>}
      {contextChips}
      <AssistantSubjects ref={subjectController} task={task ?? null} projectId={projectId} canvasId={executionTarget?.canvas_id} contextKey={contextPreview?.key} value={subjectDraft} onChange={setSubjectDraft} disabled={contextDisabled || runtime.loading || runtime.accessEnded} layout={layout} prepareContext={prepareContext}/>
      <AssistantAttachments ref={attachmentController} task={task ?? null} value={attachmentDraft} onChange={setAttachmentDraft} disabled={contextDisabled || runtime.loading || runtime.accessEnded}/>
      <AgentContextComposer key={composerVersion} ref={inputContext} conversation={{ id: conversation.id, project_id: projectId }} layout={layout} attachmentApi={assistantApi} dialogClassName={`ai-assistant-context-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} initialSkills={composerVersion ? [] : restored.skills} onSkills={setSkills} onSkillsEdited={() => setSkillsDirty(true)}
        skillPicker={layout === 'standard' ? onClose => <AssistantSkillPicker selected={selectedSkills} activations={task?.skill_activations} mode={(settingsDirty ? target : task?.execution_target)?.mode} disabled={contextDisabled} onChange={(next: AssistantSkillChoice[]) => { setSkills(next); setSkillsDirty(true); }} onClose={onClose}/> : undefined}
        skillSummary={selectedSkills.length > 0 && <div className="agent-loaded-skills assistant-selected-skills" aria-label="已加载技能">{selectedSkills.map(skill => <span key={skill.id}>{skill.name ?? skill.id} v{skill.content_version}<Button size="small" type="text" aria-label={`移除方法${skill.name ?? skill.id}`} disabled={contextDisabled} icon={<X size={12}/>} onClick={() => { setSkills(selectedSkills.filter(item => item.id !== skill.id)); setSkillsDirty(true); }}/></span>)}</div>}
        presentation="assistant" toolsOpen={toolsOpen}
        disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain} onBusy={setContextBusy} onBlockReason={setContextBlockReason}
        input={<div className="ai-assistant-input"><AssistantMentionTextarea layout={layout} projectId={projectId} references={mentionReferences} value={draft} disabled={readOnly || runtime.accessEnded} modelControl={modelControl} sendControl={sendControl} onChange={setDraft} onSubmit={() => { if (!blocked) void send(); }}
          skillMode={(settingsDirty ? target : task?.execution_target)?.mode}
          onBrowseSkills={layout === 'standard' ? () => inputContext.current?.openSkills() : undefined}
          onSkill={layout === 'standard' ? skill => {
            if (contextDisabled || runtime.loading || runtime.accessEnded) return false;
            if (selectedSkills.length >= 8 && !selectedSkills.some(item => item.id === skill.id)) { setActionError('一个任务最多手动指定 8 项方法，请先移除其他方法。'); return false; }
            setSkills([...selectedSkills.filter(item => item.id !== skill.id), { id: skill.id, name: skill.name, content_version: skill.content_version }]); setSkillsDirty(true); return true;
          } : undefined}
          onReference={async reference => {
          if (uncertain || busy) { setActionError('请先核对原消息，再添加新的引用。'); return false; }
          return chooseReferences([reference]);
        }}/></div>}
        modelControl={null} sendControl={null}/>
      <div className="standard-assistant-composer-extras">
        <Button type="text" size="small" className="standard-assistant-tools-toggle" aria-label="附件与技能" aria-expanded={toolsOpen}
          disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain || contextBusy} icon={<Plus size={13}/>} onClick={() => setToolsOpen(previous => !previous)}>附件与技能</Button>
        {layout === 'standard' && (active || queuedRuns.length > 0 || reconnecting) ? <Popover open={runDetailsOpen} onOpenChange={setRunDetailsOpen} trigger="click" placement="topRight" title="助手运行状态" classNames={{ root: 'standard-assistant-popover standard-creation-popup' }} content={runDetails}>
          <Button ref={runEntry} type="text" size="small" className="standard-assistant-tools-toggle" aria-label="助手运行状态" aria-expanded={runDetailsOpen} onKeyDown={event => { if (runDetailsOpen && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRunDetails(); } }}>{reconnecting ? '正在重连' : queuedRuns.length ? `${queuedRuns.length} 条排队` : runtime.run?.status === 'queued' ? '消息排队' : '正在回复'}</Button>
        </Popover> : <p className="standard-assistant-private">对话仅你可见</p>}
      </div>
      {readOnly ? <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'}>当前项目只读，可以查看已有对话。</span> : null}
      {pendingControl && !busy && <StudioFeedback type="warning" persistent title="任务控制待核对" message="控制结果尚未确认，请使用原请求核对。" noticeKey={`assistant-control:${pendingControl.targetId}`} actions={<Button size="small" disabled={readOnly || runtime.accessEnded} onClick={() => void control(pendingControl.targetId, pendingControl.body, pendingControl)}>使用原请求核对控制</Button>}/>}
      {busy === 'save' ? <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'} role="status">正在保存并核对作品…</span> : null}
      {layout === 'standard' && (runStatus || reconnecting) && <span className="sr-only" role="status">{runStatus || '正在恢复连接，已发送的消息继续处理。'}</span>}
      {active && runtime.run && layout !== 'standard' ? <div className="ai-assistant-run-state" role="status"><span>{runtime.run.status === 'queued' ? '消息已排队' : '正在回复，可继续发送补充消息'}</span><Button size="small" icon={<Square size={12}/>} disabled={readOnly || !!busy} loading={busy === 'stop'} onClick={() => void stop(runtime.run!.id)}>停止</Button></div> : null}
      {runtime.queuedRuns.length && layout !== 'standard' ? <div className="ai-assistant-queued" aria-label="排队消息">{runtime.queuedRuns.filter(run => run.id !== runtime.run?.id).map(run => <div key={run.id}><span>排队位置 {run.queue_position || '等待中'}</span><Button type="text" size="small" disabled={readOnly || !!busy} onClick={() => void stop(run.id)}>取消排队</Button></div>)}</div> : null}
      {uncertain ? layout === 'standard'
        ? <StudioFeedback type="warning" persistent title="助手发送待核对" message="发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。" noticeKey={`assistant-pending:${conversation.id}`} actions={<div className="ai-assistant-uncertain"><Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>核对状态</Button><Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>使用原请求核对</Button></div>}/>
        : <div className="ai-assistant-uncertain" role="status"><p>发送结果尚未确认，草稿已保留。请使用原请求核对，避免重复调用。</p><Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>核对状态</Button><Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>使用原请求核对</Button></div> : null}
    </footer>
    {layout === 'standard' && <AssistantDiscussionHistory accountId={accountId} conversationId={conversation.id} request={historyRequest} disabled={readOnly || !!busy || undoBusy || undoPending && !historyPending || !!uncertain || !!pendingControl} accessEnded={runtime.accessEnded} onClose={() => setHistoryRequest(null)} onAccepted={runtime.accept} onBlocked={setHistoryPending} onBusy={setHistoryBusy} onCorrect={content => { newTask(); setDraft(`纠正此前讨论：${content}`); }}/>}
    {layout === 'standard' && <AssistantBatchUndo accountId={accountId} conversationId={conversation.id} operationId={undoRequest} disabled={readOnly || !!busy || historyBusy || historyPending && !undoPending || !!uncertain || !!pendingControl} accessEnded={runtime.accessEnded} onClose={() => setUndoRequest(null)} onBusy={setUndoBusy} onBlocked={setUndoPending} onUpdated={() => void runtime.reload()}/>}
    {settingsOpen && <Dialog title="任务设置" className={layout === 'standard' ? 'standard-creation-dialog standard-assistant-dialog' : undefined} canClose={!busy} onClose={() => setSettingsOpen(false)}>
      <AssistantTaskSettings target={target} memoryProjects={memoryProjects} limits={mediaLimits} currentTarget={executionTarget} disabled={!!busy || !!uncertain || !!pendingControl}
        onTarget={value => { setTarget(value); setSettingsDirty(true); }} onMemory={value => { setMemoryProjects(value); setSettingsDirty(true); }} onLimits={value => { setMediaLimits(value); setSettingsDirty(true); }}>
        {task && <Button disabled={!draft.trim() || !!busy || !!pendingControl} loading={busy === 'control'} onClick={() => { setSettingsOpen(false); void control(task.id, { kind: 'redirect', content: draft.trim() }); }}>用当前输入改方向</Button>}
      </AssistantTaskSettings>
      <footer className="ai-assistant-history-footer"><Button onClick={() => setSettingsOpen(false)}>完成</Button></footer>
    </Dialog>}
  </div>;
}
