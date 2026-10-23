import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Dropdown, Popover, Select, Skeleton, Tooltip } from 'antd';
import { Bubble, Prompts, Welcome } from '@ant-design/x';
import { ArrowUpRight, BookOpen, Clapperboard, Library, LockKeyhole, Plus, X } from 'lucide-react';
import { assistantApi } from '../../api/modules/assistant';
import { agentsApi } from '../../api/modules/agents';
import { ApiError, errorMessage } from '../../api/http';
import type { AgentModel } from '../../api/types/agents';
import type { GlobalAssistantConversation, AssistantMentionReference, AssistantMessageContext, AssistantReference, AssistantTaskSendInput, AssistantControlInput, TaskExecutionTarget } from '../../api/types/assistant';
import { workflowStorage } from '../auth/account-storage';
import { attemptStorage, clearAttempt, requestAttempt } from '../generations/attempt';
import { AgentContextComposer, type AgentContextController } from '../agents/AgentContextComposer';
import { isAgentRunActive } from '../agents/agent-navigation';
import { clearAssistantPendingAction, clearAssistantPendingSend, readAssistantDraft, readAssistantPendingAction, readAssistantPendingSend, saveAssistantDraft, saveAssistantPendingAction, saveAssistantPendingSend, type AssistantPendingAction, type AssistantPendingSend } from './assistant-draft';
import { AssistantReply } from './AssistantMessage';
import { AssistantMentionTextarea } from './AssistantMentionTextarea';
import { useAssistantRuntime } from './useAssistantRuntime';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { timelineBubbles } from './AgentTimeline';
import { materialIdentity, mergeMaterials, type MessageMaterial } from './assistant-materials';
import { inspectMaterials, subjectSelectionFor } from './assistant-material-service';
import { AssistantSkillPicker, type AssistantSkillChoice } from './AssistantSkillPicker';
import { ensureSkillTokens, removeSkillToken, skillToken } from './assistant-skill-token';
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
  const [queuedStalled, setQueuedStalled] = useState(false);
  const queuedRunId = runtime.run?.status === 'queued' ? runtime.run.id : null;
  useEffect(() => {
    if (!queuedRunId) { setQueuedStalled(false); return; }
    const timer = setTimeout(() => setQueuedStalled(true), 20_000);
    return () => { clearTimeout(timer); setQueuedStalled(false); };
  }, [queuedRunId]);
  const [historyRequest, setHistoryRequest] = useState<HistoryRequest | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyPending, setHistoryPending] = useState(false);
  const [undoRequest, setUndoRequest] = useState<string | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoPending, setUndoPending] = useState(false);
  const [restored] = useState(() => readAssistantDraft(accountId, conversation.id, workflowStorage()));
  const [restoredSend] = useState(() => readAssistantPendingSend(accountId, conversation.id, workflowStorage()));
  const [restoredControl] = useState(() => readAssistantPendingAction(accountId, conversation.id, 'control', workflowStorage()));
  const [draft, setDraft] = useState(() => ensureSkillTokens(restored.text, restored.skills));
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
  const [materials, setMaterials] = useState<MessageMaterial[]>([]);
  const [taskId, setTaskId] = useState(restored.taskId ?? '');
  const [independent, setIndependent] = useState(restored.newTask ?? false);
  const task = independent ? null : runtime.tasks.find(item => item.id === taskId && item.status !== 'succeeded') ?? runtime.task;
  const selectedSkills = skillsDirty ? skills : task?.skills ?? [];
  const [composerVersion, setComposerVersion] = useState(0);
  const [pendingControl, setPendingControl] = useState<Extract<AssistantPendingAction, { kind: 'control' }> | null>(restoredControl.pending?.kind === 'control' ? restoredControl.pending : null);
  const autoResume = useRef<string | null>(null);
  const [busy, setBusy] = useState<'save' | 'send' | 'stop' | 'control' | ''>('');
  const [actionError, setActionError] = useState(restoredSend.error || restoredControl.error);
  const [uncertain, setUncertain] = useState<AssistantPendingSend | null>(restoredSend.pending);
  const [newContent, setNewContent] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const skillEntry = useRef<HTMLButtonElement>(null);
  const skillList = useRef<HTMLDivElement>(null);
  const inputContext = useRef<AgentContextController>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  const previousContextKey = useRef(contextPreview?.key);
  const current = useRef({ draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, materials, accessEnded: runtime.accessEnded });
  current.current = { draft, readOnly, prepareContext, includeContext, contextKey: contextPreview?.key, materials, accessEnded: runtime.accessEnded };
  const active = isAgentRunActive(runtime.run?.status);
  const model = models.find(item => item.id === modelId);
  const previousTask = useRef(task?.id);
  useEffect(() => {
    if (!task && previousTask.current && !skillsDirty) { setSkills([]); setComposerVersion(value => value + 1); }
    previousTask.current = task?.id;
  }, [task?.id, skillsDirty]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!busy && !uncertain && pendingControl && autoResume.current === pendingControl.key) {
      void control(pendingControl.targetId, pendingControl.body, pendingControl);
    }
  }, [busy, uncertain, pendingControl]);
  useEffect(() => { onSendingChange(!!busy || historyBusy || undoBusy); return () => onSendingChange(false); }, [busy, historyBusy, undoBusy, onSendingChange]);
  useEffect(() => {
    if (previousContextKey.current !== contextPreview?.key) { setIncludeContext(false); previousContextKey.current = contextPreview?.key; }
  }, [contextPreview?.key]);
  useEffect(() => {
    setDraftError(saveAssistantDraft(accountId, conversation.id, { version: 1, text: draft, modelId, skills, skillsDirty, contextKey: contextPreview?.key, includeContext, taskId, newTask: independent }, workflowStorage())
      ? '' : '浏览器未能保存对话草稿，请在离开前复制当前输入。');
  }, [accountId, conversation.id, draft, modelId, skills, skillsDirty, contextPreview?.key, includeContext, taskId, independent]);
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
      || !retry && (stopTaskId || contextBlockReason || !task && !model || !current.current.draft.trim() || uncertain || pendingControl)) return;
    const submitted = retry?.draft ?? current.current.draft;
    const captured = current.current;
    const matches = () => alive.current && !current.current.readOnly && !current.current.accessEnded
      && current.current.contextKey === captured.contextKey && current.current.includeContext === captured.includeContext
      && current.current.materials === captured.materials;
    const attemptScope = `assistant-send:${conversation.id}`;
    locked.current = true; setBusy('save'); setActionError('');
    try {
      if (!retry && !inputContext.current) throw new ApiError('正在恢复消息上下文，请稍后发送。', 'assistant_context_loading');
      const input = retry ? null : inputContext.current!.snapshot();
      let context: AssistantMessageContext | null = null;
      if (!retry) {
        try { if (captured.materials.length) context = await captured.prepareContext(false, captured.materials.map(({ kind, id, revision }) => ({ kind: kind as AssistantReference['kind'], id, revision }))); }
        catch (cause) {
          if (cause instanceof Error && !(cause instanceof ApiError) && /^(当前|画布|模型偏好|保存期间|作品|引用)/.test(cause.message)) throw new ApiError(cause.message, 'assistant_context_blocked');
          throw cause;
        }
      }
      if (context && (context.selected?.length ?? 0) > 16) throw new ApiError('一条消息最多引用 16 个对象，请减少选择后再发送。', 'assistant_context_limit');
      const selection = retry ? null : await subjectSelectionFor(task?.id ?? null, captured.materials);
      if (!matches()) { if (alive.current) setActionError('当前作品或素材已变化，消息草稿已保留，请再次发送。'); return; }
      const body: AssistantTaskSendInput = (retry?.body as AssistantTaskSendInput | undefined) ?? {
        content: submitted.trim(), ...input,
        ...(task ? { task_id: task.id } : { new_task: true, model_config_id: modelId }),
        ...(selection ? { subject_selection: selection } : {}),
        ...(!task ? { execution_target: executionTarget ?? { mode: 'standard', project_id: projectId } } : {}),
      };
      if (!retry) {
        if (skillsDirty) body.skills = skills.map(({ id, content_version }) => ({ id, content_version }));
        else delete body.skills;
      }
      const key = retry?.key ?? await requestAttempt(attemptScope, body, attemptStorage());
      if (!matches()) return;
      const resumeBody: AssistantControlInput | null = !retry && task && ['stopped', 'paused_error', 'paused_budget'].includes(task.status) ? { kind: 'resume', expected_control_seq: task.control_seq } : null;
      const resume = retry?.resume ?? (resumeBody && task ? { version: 1 as const, kind: 'control' as const, targetId: task.id, body: resumeBody, key: await requestAttempt(`assistant-control:${task.id}:resume`, resumeBody, attemptStorage()) } : undefined);
      if (!matches()) return;
      const pending: AssistantPendingSend = { version: 1, body, key, draft: submitted, ...(resume ? { resume } : {}) };
      const storage = workflowStorage();
      if (!saveAssistantPendingSend(accountId, conversation.id, pending, storage)) {
        const existing = readAssistantPendingSend(accountId, conversation.id, storage);
        setUncertain(existing.pending); throw new ApiError(existing.error || '浏览器无法保存发送记录，本次尚未发送。请允许本机存储后重试。', 'assistant_recovery_unavailable');
      }
      setBusy('send');
      try {
        const result = await assistantApi.sendTask(conversation.id, body, key);
        const continuation = resume ? { ...resume, body: { ...resume.body, message_id: result.message.id } } : null;
        if (continuation && !saveAssistantPendingAction(accountId, conversation.id, continuation, storage)) throw new ApiError('消息已保存，但继续回复的记录尚未保留。请刷新状态后继续。', 'assistant_recovery_unavailable');
        const cleared = clearAssistantPendingSend(accountId, conversation.id, key, storage);
        if (cleared) clearAttempt(attemptScope, attemptStorage());
        if (!alive.current) {
          const saved = readAssistantDraft(accountId, conversation.id, storage);
          if (saved.text === submitted) saveAssistantDraft(accountId, conversation.id, { ...saved, text: '' }, storage);
        }
        if (!alive.current) return;
        if (continuation) { setPendingControl(continuation); autoResume.current = continuation.key; }
        runtime.accept(result); setUncertain(cleared ? null : pending);
        setTaskId(result.task.id); setIndependent(false); setMaterials([]);
        setSkillsDirty(false);
        if (!cleared) setActionError('消息已受理，但本机记录尚未清理，可以直接发送下一条。');
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
    if (locked.current || readOnly || runtime.accessEnded || restoredControl.error || uncertain) return false;
    const initialResume = !!retry && autoResume.current === retry.key;
    if (initialResume) autoResume.current = null;
    locked.current = true; setBusy(body.kind === 'stop' ? 'stop' : 'control'); setActionError('');
    const scope = `assistant-control:${controlTaskId}:${body.kind}`;
    try {
      const request: Extract<AssistantPendingAction, { kind: 'control' }> = retry ?? { version: 1, kind: 'control', targetId: controlTaskId, body, key: await requestAttempt(scope, body, attemptStorage()) };
      const storage = workflowStorage();
      if (!saveAssistantPendingAction(accountId, conversation.id, request, storage)) throw new ApiError('浏览器无法保存停止/继续的记录，本次尚未执行。请允许本机存储后重试。', 'assistant_recovery_unavailable');
      setPendingControl(request);
      try {
        const result = await assistantApi.controlTask(request.targetId, request.body, request.key);
        const cleared = clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage);
        if (cleared) clearAttempt(scope, attemptStorage());
        if (alive.current) {
          setPendingControl(cleared ? null : request); runtime.acceptControl(result);
          if (!cleared) setActionError('操作已受理，但本机记录尚未清理，可以继续。');
          if (request.body.kind === 'redirect' && current.current.draft === request.body.content) setDraft('');
        }
      } catch (cause) {
        if ((!retry || initialResume) && cause instanceof ApiError && cause.status && cause.status >= 400 && cause.status < 500
          && clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage)) {
          clearAttempt(scope, attemptStorage()); if (alive.current) setPendingControl(null);
        }
        throw cause;
      }
      return true;
    }
    catch (cause) { if (alive.current) { setActionError(errorMessage(cause)); void runtime.reload(); } return false; }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  async function interject() {
    const id = stopTaskId;
    const content = current.current.draft.trim();
    if (!id || !content) return;
    await control(id, { kind: 'redirect', content });
  }
  const stop = (id: string) => {
    const actual = runtime.tasks.some(item => item.id === id) ? id : [runtime.run, ...runtime.queuedRuns].find(item => item?.id === id)?.task_id;
    if (actual) return control(actual, { kind: 'stop' });
  };
  /** 素材卡片/分镜卡片与上传文件走同一条素材行：添加后进输入框顶部，发送后清空。 */
  async function addMaterials(selected: readonly AssistantMentionReference[]) {
    if (locked.current || uncertain || pendingControl || readOnly) return false;
    locked.current = true; setBusy('save'); setActionError('');
    try {
      const context = await current.current.prepareContext(false, selected.map(({ name: _name, ...reference }) => reference));
      if (!context?.selected?.length) throw new ApiError('当前素材尚未保存或已不可用。', 'assistant_context_blocked');
      const incoming = await inspectMaterials(projectId, context, selected.map(({ kind, id, name, revision }) => ({ kind, id, name, ...(revision ? { revision } : {}) })));
      if (alive.current) setMaterials(previous => mergeMaterials(previous, incoming));
      return true;
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)); return false; }
    finally { locked.current = false; if (alive.current) setBusy(''); }
  }
  function newTask() {
    setIndependent(true); setTaskId(''); setMaterials([]); setSkills([]);
    setSkillsDirty(false);
    setComposerVersion(value => value + 1);
  }
  /** 正文是方法选择的唯一入口：只有删掉原本存在的 /名称 才卸载该方法。 */
  function changeDraft(next: string) {
    const previous = draft;
    setDraft(next);
    const dropped = skills.filter(skill => previous.includes(skillToken(skill)) && !next.includes(skillToken(skill)));
    if (dropped.length) { setSkills(previousSkills => previousSkills.filter(skill => !dropped.some(item => item.id === skill.id))); setSkillsDirty(true); }
  }
  /** 从 Skill 面板增删方法时同步正文，避免出现看不见的已加载方法。 */
  function changeSkills(next: AssistantSkillChoice[]) {
    const removed = selectedSkills.filter(skill => !next.some(old => old.id === skill.id));
    let text = draft;
    removed.forEach(skill => { text = removeSkillToken(text, skillToken(skill)); });
    text = ensureSkillTokens(text, next);
    setSkills(next); setSkillsDirty(true);
    if (text !== draft) setDraft(text);
  }
  const contextDisabled = readOnly || !!busy || historyBusy || historyPending || undoBusy || undoPending || !!uncertain || !!pendingControl || !!restoredControl.error;
  const stopTaskId = active ? runtime.run?.task_id ?? task?.id : task && ['queued', 'running', 'waiting_media'].includes(task.status) ? task.id : undefined;
  const stopping = busy === 'stop' || runtime.tasks.some(item => item.id === stopTaskId && item.control_state === 'stop_pending');
  const blocked = readOnly || runtime.loading || runtime.accessEnded || !!busy || historyBusy || historyPending || undoBusy || undoPending || contextBusy || !!contextBlockReason || !!uncertain || !!pendingControl || !!restoredSend.error || !!restoredControl.error || !!stopTaskId || !task && !model || !draft.trim();
  const taskRun = task ? [runtime.run, ...runtime.queuedRuns].find(run => run?.task_id === task.id) : undefined;
  const taskModelName = taskRun?.model_name || '沿用当前任务模型';
  const modelControl = task
    ? <Tooltip title="当前任务沿用此模型，任务结束后可切换。" trigger={['hover', 'focus', 'click']}>
        <Button type="text" size="small" className="ai-assistant-model ai-assistant-model-locked" aria-label={`助手模型：${taskModelName}，当前任务已锁定`} icon={<LockKeyhole size={12} aria-hidden="true"/>}>
          <span className="ai-assistant-model-name">{taskModelName}</span>
        </Button>
      </Tooltip>
    : <Select className="ai-assistant-model" classNames={layout === 'standard' ? { popup: { root: 'standard-assistant-select-popup standard-creation-popup' } } : undefined} variant="borderless" size="small" aria-label="助手模型" value={modelId} loading={modelsLoading} disabled={runtime.loading || !!busy || contextBusy || !!uncertain || readOnly || runtime.accessEnded} placeholder="选择模型" options={models.map(item => ({ value: item.id, label: item.name }))} onChange={setModelId}/>;
  const toolsDisabled = contextDisabled || contextBusy || runtime.loading || runtime.accessEnded;
  const canvasMode = executionTarget?.mode === 'infinite_canvas';
  function closeSkills() { setSkillsOpen(false); requestAnimationFrame(() => skillEntry.current?.focus()); }
  const toolControl = <div className="ai-assistant-composer-tools">
    <Dropdown open={addOpen} onOpenChange={open => { setAddOpen(open); if (open) setSkillsOpen(false); }} trigger={['click']} placement="topLeft"
      overlayClassName={layout === 'standard' ? 'standard-assistant-menu standard-creation-menu standard-creation-popup' : undefined}
      disabled={toolsDisabled || !!contextBlockReason} menu={{ items: [
        { key: 'image', label: '图片' }, { key: 'text', label: '文本' },
        { key: 'canvas', label: <span>从当前画布选择{!canvasMode && <small className="ai-assistant-menu-hint">仅画布模式可用</small>}</span>, disabled: !canvasMode },
        { key: 'assets', label: '从资产库选择' },
      ], onClick: ({ key }) => {
        setAddOpen(false);
        if (key === 'image' || key === 'text') inputContext.current?.openUpload(key);
        else if (key === 'assets') inputContext.current?.openAssets();
        else if (key === 'canvas') {
          if (mentionReferences?.length) void addMaterials(mentionReferences);
          else setActionError('请先在画布中选中需要引用的节点。');
        }
      } }}>
      <Button type="text" aria-label="添加附件" aria-expanded={addOpen} disabled={toolsDisabled || !!contextBlockReason} icon={<Plus size={17}/>}/>
    </Dropdown>
    <Popover open={skillsOpen} onOpenChange={open => { setSkillsOpen(open); if (open) setAddOpen(false); }} afterOpenChange={open => { if (open) skillList.current?.querySelector('input')?.focus(); }} trigger="click" placement="top" destroyOnHidden
      classNames={{ root: `assistant-skill-popover${layout === 'standard' ? ' standard-assistant-popover standard-creation-popup' : ''}` }}
      content={<div ref={skillList} onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeSkills(); }
        if (event.key === 'Tab') {
          const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input:not([disabled]),button:not([disabled]),a[href]')].filter(node => node.getClientRects().length > 0);
          if (event.shiftKey && document.activeElement === controls[0] || !event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); closeSkills(); }
        }
      }}><AssistantSkillPicker selected={selectedSkills} activations={task?.skill_activations} mode={task?.execution_target?.mode ?? executionTarget?.mode} disabled={toolsDisabled} onChange={changeSkills}/></div>}>
      <Button ref={skillEntry} type="text" aria-label="选择 Skill" aria-expanded={skillsOpen} disabled={toolsDisabled} icon={<Library size={17}/>} onKeyDown={event => { if (skillsOpen && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeSkills(); } }}/>
    </Popover>
  </div>;
  const queuedRuns = runtime.queuedRuns.filter(run => run.id !== runtime.run?.id);
  const reconnecting = runtime.connection === 'reconnecting' && !uncertain && !runtime.error;
  return <div className="ai-assistant-runtime">
    {runtime.error ? layout === 'standard'
      ? <StudioFeedback scope="chat" type="error" title="AI 创作助手" message={runtime.error} noticeKey={`assistant-connection:${conversation.id}`} actions={<Button size="small" onClick={() => runtime.reconnect()}>重新连接</Button>}/>
      : <div className="ai-assistant-notice" role="alert"><span>{runtime.error}</span><Button size="small" onClick={() => { runtime.reconnect(); }}>重新连接</Button></div> : null}
    {actionError || draftError ? layout === 'standard'
      ? <StudioFeedback scope="chat" type="error" title="AI 创作助手" message={actionError || draftError} noticeKey={`assistant-error:${conversation.id}`} actions={<Button size="small" onClick={() => { setActionError(''); setDraftError(''); }}>知道了</Button>}/>
      : <div className="ai-assistant-notice" role="alert"><span>{actionError || draftError}</span><Button size="small" onClick={() => { setActionError(''); setDraftError(''); }}>知道了</Button></div> : null}
    {modelsError || !modelsLoading && !models.length ? layout === 'standard'
      ? <StudioFeedback scope="chat" type={modelsError ? 'error' : 'warning'} quiet={!modelsError} persistent title="助手模型" message={modelsError || '请先添加并启用文本模型。'} noticeKey={`assistant-model:${conversation.id}`} actions={<><Button size="small" onClick={onOpenModelSettings}>模型配置</Button><Button size="small" onClick={() => setModelRevision(value => value + 1)}>重新读取</Button></>}/>
      : <div className="ai-assistant-notice" role="status"><span>{modelsError || '请先添加并启用文本模型。'}</span><Button size="small" onClick={onOpenModelSettings}>模型配置</Button><Button size="small" onClick={() => setModelRevision(value => value + 1)}>重新读取</Button></div> : null}
    <div ref={transcript} className="ai-assistant-log" aria-label="助手消息" tabIndex={0} onScroll={event => {
      const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
      if (pinned.current) setNewContent(false);
    }}>
      {runtime.messages.length < runtime.total || runtime.hasMoreTasks ? <Button size="small" loading={runtime.olderLoading} onClick={() => {
        const node = transcript.current; if (node) prepend.current = { height: node.scrollHeight, top: node.scrollTop };
        if (runtime.messages.length < runtime.total) void runtime.older(); else void runtime.olderTasks();
      }}>载入更早</Button> : null}
      {runtime.loading ? <Skeleton active title={false} paragraph={{ rows: 5 }}/>
        : !runtime.messages.length && !active ? <div className="ai-assistant-empty"><Welcome variant="borderless" icon={<Clapperboard size={26} aria-hidden="true"/>} title={runtime.accessEnded ? '对话暂不可访问' : '把想法聊清楚'} description={runtime.accessEnded ? '恢复登录和项目访问后可重新读取。' : '一起梳理故事、画面和创作方向。'}/>{!runtime.accessEnded ? <Prompts vertical items={['帮我梳理故事的节奏与冲突', '一起完善角色的动机与关系', '为当前作品提供创作建议'].map(text => ({ key: text, label: <button className="ai-assistant-starter" type="button" disabled={readOnly} onClick={() => setDraft(text)}><span>{text}</span><ArrowUpRight size={15}/></button>, disabled: readOnly }))}/> : null}</div> : null}
      {!runtime.loading && (runtime.messages.length > 0 || active) ? <Bubble.List className="ai-assistant-bubbles" autoScroll={false} items={[
        ...timelineBubbles({
          messages: runtime.messages,
          operations: runtime.operations,
          layout,
          onUndo: setUndoRequest,
          undoDisabled: contextDisabled || runtime.accessEnded,
          historyDisabled: contextDisabled || runtime.loading || runtime.accessEnded,
          onHistory: layout === 'standard' ? (message, mode) => { if (message.task_id) setHistoryRequest({ message, mode }); } : undefined,
        }),
        ...(active && runtime.run && (runtime.delta || !reconnecting && !stopping) ? [{ key: `stream:${runtime.run.id}`, role: 'assistant', placement: 'start' as const, variant: 'borderless' as const, className: 'ai-assistant-bubble-reply', content: <article className="ai-assistant-turn ai-assistant-streaming" data-run-id={runtime.run.id}>{runtime.delta ? <AssistantReply text={runtime.delta}/> : <AssistantLoading label={runtime.run.status === 'queued' ? '等待回复' : '正在回复'}/>}</article> }] : []),
      ]}/> : null}
      {queuedStalled && runtime.connection === 'live' && <div className="ai-assistant-connection ai-assistant-stalled" role="status"><span>消息已提交，但迟迟未被调度。请确认后端调度进程正在运行。</span><Button size="small" onClick={() => runtime.reconnect()}>重新连接</Button></div>}
      {runtime.run?.status === 'failed' ? <p className="ai-assistant-failed" role="status">{runtime.run.error?.message || '这次回复未完成，已有消息仍保留。请检查模型配置后重试。'}</p> : null}
      {stopping && <AssistantLoading label="当前工具完整结束后停止"/>}
      {reconnecting && <div className="ai-assistant-connection"><AssistantLoading label="正在恢复连接"/><Button type="text" size="small" onClick={() => runtime.reconnect()}>重新连接</Button></div>}
      {queuedRuns.length > 0 && <div className="ai-assistant-queued" aria-label="排队消息">{queuedRuns.map(run => <div key={run.id}><span>排队位置 {run.queue_position || '等待中'}</span><Button type="text" size="small" disabled={readOnly || !!busy} onClick={() => void stop(run.id)}>取消排队</Button></div>)}</div>}
      {runtime.run?.status === 'cancelled' ? <p className="ai-assistant-meta">已中断，已生成的内容已保留；可以补充要求继续。</p> : null}
    </div>
    {newContent ? <Button className="ai-assistant-new-content" size="small" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; pinned.current = true; setNewContent(false); }}>查看最新消息</Button> : null}
    <footer className="ai-assistant-composer">
      {contextBlockReason ? <p className="ai-assistant-meta" role="status">{contextBlockReason}</p> : null}
      <AgentContextComposer key={composerVersion} ref={inputContext} conversation={{ id: conversation.id, project_id: projectId }} layout={layout} attachmentApi={assistantApi} dialogClassName={`ai-assistant-context-dialog${layout === 'standard' ? ' standard-creation-dialog standard-assistant-dialog' : ''}`} materials={materials.map(item => ({ key: materialIdentity(item), name: item.name, kind: item.kind, thumbnail: null, onRemove: () => setMaterials(previous => previous.filter(candidate => candidate !== item)) }))} initialSkills={composerVersion ? [] : restored.skills} onSkills={setSkills} onSkillsEdited={() => setSkillsDirty(true)}
        presentation="assistant" skillsManaged
        disabled={readOnly || runtime.accessEnded || !!busy || !!uncertain} onBusy={setContextBusy} onBlockReason={setContextBlockReason}
        input={<div className="ai-assistant-input"><AssistantMentionTextarea layout={layout} projectId={projectId} references={mentionReferences} highlights={[...skills.map(skillToken), ...materials.map(item => `@${item.name}`)]} value={draft} disabled={readOnly || runtime.accessEnded} modelControl={modelControl} toolControl={toolControl} onChange={changeDraft} onSubmit={() => { if (!blocked) void send(); }}
          action={{ generating: !!stopTaskId, stopping, submitting: busy === 'send' || busy === 'save', disabled: stopTaskId ? contextDisabled || runtime.loading || runtime.accessEnded || stopping : blocked,
            canInterject: !!stopTaskId && !!draft.trim() && !readOnly && !runtime.accessEnded && !contextBusy && !uncertain && !pendingControl,
            interjecting: busy === 'control',
            onInterject: () => { void interject(); },
            onStop: () => { if (stopTaskId) void stop(stopTaskId); } }}
          skillMode={task?.execution_target?.mode ?? executionTarget?.mode}
          onBrowseSkills={() => { setAddOpen(false); setSkillsOpen(true); }}
          onSkill={skill => {
            if (contextDisabled || runtime.loading || runtime.accessEnded) return false;
            if (selectedSkills.length >= 8 && !selectedSkills.some(item => item.id === skill.id)) { setActionError('一个任务最多手动指定 8 项方法，请先移除其他方法。'); return false; }
            setSkills([...selectedSkills.filter(item => item.id !== skill.id), { id: skill.id, name: skill.name, content_version: skill.content_version }]); setSkillsDirty(true); return true;
          }}
          onReference={async reference => {
          if (uncertain || busy) { setActionError('上一条消息的结果还没确认，确认后再添加新素材。'); return false; }
          return addMaterials([reference]);
        }}/></div>}
        modelControl={null} sendControl={null}/>
      {readOnly ? <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'}>当前项目只读，可以查看已有对话。</span> : null}
      {pendingControl && !busy && <StudioFeedback scope="chat" type="warning" persistent title="操作结果待确认" message="这次操作可能已生效，结果暂时无法确认。为避免重复执行，请先刷新状态。" noticeKey={`assistant-control:${pendingControl.targetId}`} actions={<Button size="small" disabled={readOnly || runtime.accessEnded} onClick={() => void control(pendingControl.targetId, pendingControl.body, pendingControl)}>重发这次操作</Button>}/>}
      {busy === 'save' ? <span className={layout === 'standard' ? 'sr-only' : 'ai-assistant-meta'} role="status">正在保存…</span> : null}
      {uncertain ? layout === 'standard'
        ? <StudioFeedback scope="chat" type="warning" persistent title="发送结果待确认" message="消息可能已提交，但结果暂时无法确认。为避免重复生成和计费，请先刷新状态。" noticeKey={`assistant-pending:${conversation.id}`} actions={<div className="ai-assistant-uncertain"><Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>刷新状态</Button><Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>重发原消息</Button></div>}/>
        : <div className="ai-assistant-uncertain" role="status"><p>消息可能已提交，但结果暂时无法确认。为避免重复生成和计费，请先刷新状态。</p><Button size="small" disabled={!!busy} onClick={() => void runtime.reload()}>刷新状态</Button><Button size="small" disabled={readOnly || runtime.accessEnded || !!busy || contextBusy} onClick={() => void send(uncertain)}>重发原消息</Button></div> : null}
    </footer>
    {layout === 'standard' && <AssistantDiscussionHistory accountId={accountId} conversationId={conversation.id} request={historyRequest} disabled={readOnly || !!busy || undoBusy || undoPending && !historyPending || !!uncertain || !!pendingControl} accessEnded={runtime.accessEnded} onClose={() => setHistoryRequest(null)} onAccepted={runtime.accept} onBlocked={setHistoryPending} onBusy={setHistoryBusy} onCorrect={content => { newTask(); setDraft(`纠正此前讨论：${content}`); }}/>}
    {layout === 'standard' && <AssistantBatchUndo accountId={accountId} conversationId={conversation.id} operationId={undoRequest} disabled={readOnly || !!busy || historyBusy || historyPending && !undoPending || !!uncertain || !!pendingControl} accessEnded={runtime.accessEnded} onClose={() => setUndoRequest(null)} onBusy={setUndoBusy} onBlocked={setUndoPending} onUpdated={() => void runtime.reload()}/>}

  </div>;
}

function AssistantLoading({ label }: { label: string }) {
  return <p className="ai-assistant-meta ai-assistant-loading" role="status"><span>{label}</span><span className="ai-assistant-loading-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></p>;
}
