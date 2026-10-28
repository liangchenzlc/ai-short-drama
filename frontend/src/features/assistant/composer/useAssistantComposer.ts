import { assistantApi } from '@host/api/assistant/conversations/requests';
import { ApiError, errorMessage } from '@host/api/http';
import { prepareAssistantSend } from '@host/features/assistant/composer/prepare-send';
import { useAssistantModels } from '@host/features/assistant/composer/useAssistantModels';
import {
  clearAssistantPendingAction,
  clearAssistantPendingSend,
  readAssistantDraft,
  readAssistantPendingAction,
  readAssistantPendingSend,
  saveAssistantDraft,
  saveAssistantPendingAction,
  saveAssistantPendingSend,
} from '@host/features/assistant/recovery/assistant-draft';
import { inspectMaterials } from '@host/features/assistant/references/assistant-material-service';
import {
  materialIdentity,
  materialReference,
  mergeMaterials,
} from '@host/features/assistant/references/assistant-materials';
import { isAgentRunActive } from '@host/features/assistant/runtime/agent-navigation';
import { isAssistantAdmissionRejection } from '@host/features/assistant/runtime/assistant-receipt';
import { useAssistantRuntime } from '@host/features/assistant/runtime/useAssistantRuntime';
import { ensureSkillTokens, removeSkillToken, skillToken } from '@host/features/assistant/skills/assistant-skill-token';
import { workflowStorage } from '@host/features/identity/storage/account-storage';
import { attemptStorage, clearAttempt, requestAttempt } from '@host/features/shared/async/request-receipt';
import type { AssistantControlInput, AssistantMentionReference } from '@host/types/assistant/api';
import type { AgentContextController } from '@host/types/assistant/attachments/AgentContextComposer';
import type { AssistantConversationProps } from '@host/types/assistant/conversations/AssistantConversationBody';
import type { HistoryRequest } from '@host/types/assistant/history/AssistantDiscussionHistory';
import type { AssistantPendingAction, AssistantPendingSend } from '@host/types/assistant/recovery/assistant-draft';
import type { MessageMaterial } from '@host/types/assistant/references/assistant-materials';
import type { AssistantSkillChoice } from '@host/types/assistant/skills/AssistantSkillPicker';
import { useEffect, useRef, useState } from 'react';

/** 集中维护会话输入、恢复记录与幂等操作；视图只绑定当前状态和事件。 */
export function useAssistantComposer({
  conversation,
  accountId,
  projectId,
  executionTarget,
  layout,
  readOnly,
  contextPreview,
  prepareContext,
  onSendingChange,
}: AssistantConversationProps) {
  const runtime = useAssistantRuntime(conversation.id);
  const pinned = useRef(true);
  const [historyRequest, setHistoryRequest] = useState<HistoryRequest | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyPending, setHistoryPending] = useState(false);
  const [undoRequest, setUndoRequest] = useState<string | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoPending, setUndoPending] = useState(false);
  const [restored] = useState(() => readAssistantDraft(accountId, conversation.id, workflowStorage()));
  const [restoredSend] = useState(() => readAssistantPendingSend(accountId, conversation.id, workflowStorage()));
  const [restoredControl] = useState(() =>
    readAssistantPendingAction(accountId, conversation.id, 'control', workflowStorage()),
  );
  const [draft, setDraft] = useState(() => ensureSkillTokens(restored.text, restored.skills));
  const [skills, setSkills] = useState(restored.skills);
  const [skillsDirty, setSkillsDirty] = useState(restored.skillsDirty ?? restored.skills.length > 0);
  const [draftError, setDraftError] = useState('');
  const { models, modelId, setModelId, modelsLoading, modelsError, setModelRevision } = useAssistantModels(
    restored.modelId,
  );
  const [contextBusy, setContextBusy] = useState(false);
  const [contextBlockReason, setContextBlockReason] = useState('');
  const [materials, setMaterials] = useState<MessageMaterial[]>([]);
  const [restoreMaterials, setRestoreMaterials] = useState(restored.materials ?? []);
  const [restoringMaterials, setRestoringMaterials] = useState((restored.materials?.length ?? 0) > 0);
  const [materialsError, setMaterialsError] = useState(
    restored.materialsInvalid ? '本机引用记录无法读取，请移除后重新选择引用。' : '',
  );
  const [materialsRevision, setMaterialsRevision] = useState(0);
  const [taskId, setTaskId] = useState(restored.taskId ?? '');
  const [independent, setIndependent] = useState(restored.newTask ?? false);
  const task = independent
    ? null
    : (runtime.tasks.find((item) => item.id === taskId && item.status !== 'succeeded') ?? runtime.task);
  const selectedSkills = skillsDirty ? skills : (task?.skills ?? []);
  const [composerVersion, setComposerVersion] = useState(0);
  const [pendingControl, setPendingControl] = useState<Extract<AssistantPendingAction, { kind: 'control' }> | null>(
    restoredControl.pending?.kind === 'control' ? restoredControl.pending : null,
  );
  const [busy, setBusy] = useState<'save' | 'send' | 'stop' | ''>('');
  const [actionError, setActionError] = useState(restoredSend.error || restoredControl.error);
  const [uncertain, setUncertain] = useState<AssistantPendingSend | null>(restoredSend.pending);
  const inputContext = useRef<AgentContextController>(null);
  const locked = useRef(false);
  const alive = useRef(true);
  const current = useRef({
    draft,
    readOnly,
    prepareContext,
    contextKey: contextPreview?.key,
    materials,
    accessEnded: runtime.accessEnded,
  });
  current.current = {
    draft,
    readOnly,
    prepareContext,
    contextKey: contextPreview?.key,
    materials,
    accessEnded: runtime.accessEnded,
  };
  const active = isAgentRunActive(runtime.run?.status);
  const model = models.find((item) => item.id === modelId);
  const previousTask = useRef(task?.id);
  useEffect(() => {
    if (!task && previousTask.current && !skillsDirty) {
      setSkills([]);
      setComposerVersion((value) => value + 1);
    }
    previousTask.current = task?.id;
  }, [task?.id, skillsDirty]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!restoreMaterials.length) {
      setRestoringMaterials(false);
      return;
    }
    const controller = new AbortController();
    setRestoringMaterials(true);
    setMaterialsError('');
    assistantApi
      .inspectSubjects(
        restoreMaterials.map(({ name: _name, ...reference }) => reference),
        controller.signal,
      )
      .then((items) => {
        if (controller.signal.aborted) return;
        if (
          items.length !== restoreMaterials.length ||
          items.some(
            (item) =>
              !restoreMaterials.some(
                (original) =>
                  materialIdentity(original) === materialIdentity(item) &&
                  original.revision === item.revision &&
                  (!original.checksum || original.checksum === item.checksum),
              ),
          )
        ) {
          setMaterialsError('引用版本已变化，请移除旧引用并重新选择。当前输入已保留。');
          return;
        }
        setMaterials(
          items.map((item) => ({
            ...item,
            name:
              restoreMaterials.find((original) => materialIdentity(original) === materialIdentity(item))?.name ??
              item.id,
            project_name: '已恢复引用',
            source_name: item.source_id,
          })),
        );
        setRestoreMaterials([]);
      })
      .catch((cause) => {
        if (!controller.signal.aborted)
          setMaterialsError(`引用恢复未完成：${errorMessage(cause)} 请重试读取，或移除后重新选择引用。`);
      })
      .finally(() => {
        if (!controller.signal.aborted) setRestoringMaterials(false);
      });
    return () => controller.abort();
  }, [restoreMaterials, materialsRevision]);
  useEffect(() => {
    onSendingChange(!!busy || historyBusy || undoBusy);
    return () => onSendingChange(false);
  }, [busy, historyBusy, undoBusy, onSendingChange]);
  useEffect(() => {
    if (restored.materialsInvalid && materialsError) return;
    setDraftError(
      saveAssistantDraft(
        accountId,
        conversation.id,
        {
          version: 1,
          text: draft,
          modelId,
          skills,
          skillsDirty,
          taskId,
          newTask: independent,
          materials: restoreMaterials.length
            ? restoreMaterials
            : materials.map((item) => ({
                ...materialReference(item),
                name: item.name,
              })),
        },
        workflowStorage(),
      )
        ? ''
        : '浏览器未能保存对话草稿，请在离开前复制当前输入。',
    );
  }, [
    accountId,
    conversation.id,
    draft,
    modelId,
    skills,
    skillsDirty,
    taskId,
    independent,
    materials,
    restoreMaterials,
    materialsError,
    restored.materialsInvalid,
  ]);
  async function send(retry?: AssistantPendingSend) {
    if (
      locked.current ||
      historyBusy ||
      historyPending ||
      undoBusy ||
      undoPending ||
      contextBusy ||
      restoringMaterials ||
      materialsError ||
      readOnly ||
      runtime.loading ||
      runtime.accessEnded ||
      restoredSend.error ||
      restoredControl.error ||
      (!retry &&
        (stopping ||
          contextBlockReason ||
          (!task && !model) ||
          !current.current.draft.trim() ||
          uncertain ||
          pendingControl))
    )
      return;
    const submitted = retry?.draft ?? current.current.draft;
    const captured = current.current;
    const matches = () =>
      alive.current &&
      !current.current.readOnly &&
      !current.current.accessEnded &&
      current.current.contextKey === captured.contextKey &&
      current.current.materials === captured.materials;
    const attemptScope = `assistant-send:${conversation.id}`;
    locked.current = true;
    setBusy('save');
    setActionError('');
    try {
      const pending = await prepareAssistantSend({
        retry,
        inputContext: inputContext.current,
        context: captured,
        task,
        submitted,
        layout,
        modelId,
        projectId,
        executionTarget,
        skillsDirty,
        skills,
        attemptScope,
        matches,
      });
      if (!pending) return;
      const { body, key } = pending;
      const storage = workflowStorage();
      if (!saveAssistantPendingSend(accountId, conversation.id, pending, storage)) {
        const existing = readAssistantPendingSend(accountId, conversation.id, storage);
        setUncertain(existing.pending);
        throw new ApiError(
          existing.error || '浏览器无法保存发送记录，本次尚未发送。请允许本机存储后重试。',
          'assistant_recovery_unavailable',
        );
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
        runtime.accept(result);
        setUncertain(cleared ? null : pending);
        setTaskId(result.task.id);
        setIndependent(false);
        setMaterials([]);
        setSkillsDirty(false);
        if (!cleared) setActionError('消息已受理，但本机记录尚未清理，可以直接发送下一条。');
        inputContext.current?.sent(body.attachment_ids ?? []);
        setSkills([]);
        setComposerVersion((value) => value + 1);
        if (current.current.draft === submitted) setDraft('');
        pinned.current = true;
      } catch (cause) {
        const rejected = cause instanceof ApiError && isAssistantAdmissionRejection(cause);
        const cleared = rejected && clearAssistantPendingSend(accountId, conversation.id, key, storage);
        if (cleared) clearAttempt(attemptScope, attemptStorage());
        if (alive.current) {
          setUncertain(cleared ? null : pending);
          setActionError(errorMessage(cause));
        }
      }
    } catch (cause) {
      if (alive.current) setActionError(errorMessage(cause));
    } finally {
      locked.current = false;
      if (alive.current) setBusy('');
    }
  }
  async function control(controlTaskId: string, body: AssistantControlInput, retry = pendingControl) {
    if (locked.current || readOnly || runtime.accessEnded || restoredControl.error || uncertain) return false;
    locked.current = true;
    setBusy('stop');
    setActionError('');
    const scope = `assistant-control:${controlTaskId}:${body.kind}`;
    try {
      const request: Extract<AssistantPendingAction, { kind: 'control' }> = retry ?? {
        version: 1,
        kind: 'control',
        targetId: controlTaskId,
        body,
        key: await requestAttempt(scope, body, attemptStorage()),
      };
      const storage = workflowStorage();
      if (!saveAssistantPendingAction(accountId, conversation.id, request, storage))
        throw new ApiError(
          '浏览器无法保存停止/继续的记录，本次尚未执行。请允许本机存储后重试。',
          'assistant_recovery_unavailable',
        );
      setPendingControl(request);
      try {
        const result = await assistantApi.controlTask(request.targetId, request.body, request.key);
        const cleared = clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage);
        if (cleared) clearAttempt(scope, attemptStorage());
        if (alive.current) {
          setPendingControl(cleared ? null : request);
          runtime.acceptControl(result);
          if (!cleared) setActionError('操作已受理，但本机记录尚未清理，可以继续。');
        }
      } catch (cause) {
        if (
          cause instanceof ApiError &&
          isAssistantAdmissionRejection(cause) &&
          clearAssistantPendingAction(accountId, conversation.id, 'control', request.key, storage)
        ) {
          clearAttempt(scope, attemptStorage());
          if (alive.current) setPendingControl(null);
        }
        throw cause;
      }
      return true;
    } catch (cause) {
      if (alive.current) {
        setActionError(errorMessage(cause));
        void runtime.reload();
      }
      return false;
    } finally {
      locked.current = false;
      if (alive.current) setBusy('');
    }
  }
  async function interject() {
    await send();
  }
  const stop = (id: string) => {
    const actual = runtime.tasks.some((item) => item.id === id)
      ? id
      : [runtime.run, ...runtime.queuedRuns].find((item) => item?.id === id)?.task_id;
    if (actual) return control(actual, { kind: 'stop' });
  };
  /** 素材卡片/分镜卡片与上传文件走同一条素材行：添加后进输入框顶部，发送后清空。 */
  async function addMaterials(selected: readonly AssistantMentionReference[]) {
    if (locked.current || uncertain || pendingControl || readOnly) return false;
    locked.current = true;
    setBusy('save');
    setActionError('');
    try {
      const context = await current.current.prepareContext(
        false,
        selected.map(({ name: _name, ...reference }) => reference),
      );
      if (!context?.selected?.length) throw new ApiError('当前素材尚未保存或已不可用。', 'assistant_context_blocked');
      const incoming = await inspectMaterials(
        projectId,
        context,
        selected.map(({ kind, id, name, revision }) => ({
          kind,
          id,
          name,
          ...(revision ? { revision } : {}),
        })),
      );
      if (alive.current) setMaterials((previous) => mergeMaterials(previous, incoming));
      return true;
    } catch (cause) {
      if (alive.current) setActionError(errorMessage(cause));
      return false;
    } finally {
      locked.current = false;
      if (alive.current) setBusy('');
    }
  }
  function newTask() {
    setIndependent(true);
    setTaskId('');
    if (!skillsDirty) setSkills([]);
    setSkillsDirty(true);
  }
  /** 正文是方法选择的唯一入口：只有删掉原本存在的 /名称 才卸载该方法。 */
  function changeDraft(next: string) {
    const previous = draft;
    setDraft(next);
    const dropped = skills.filter((skill) => previous.includes(skillToken(skill)) && !next.includes(skillToken(skill)));
    if (dropped.length) {
      setSkills((previousSkills) => previousSkills.filter((skill) => !dropped.some((item) => item.id === skill.id)));
      setSkillsDirty(true);
    }
  }
  /** 从 Skill 面板增删方法时同步正文，避免出现看不见的已加载方法。 */
  function changeSkills(next: AssistantSkillChoice[]) {
    const removed = selectedSkills.filter((skill) => !next.some((old) => old.id === skill.id));
    let text = draft;
    removed.forEach((skill) => {
      text = removeSkillToken(text, skillToken(skill));
    });
    text = ensureSkillTokens(text, next);
    setSkills(next);
    setSkillsDirty(true);
    if (text !== draft) setDraft(text);
  }
  const contextDisabled =
    readOnly ||
    !!busy ||
    historyBusy ||
    historyPending ||
    undoBusy ||
    undoPending ||
    !!uncertain ||
    !!pendingControl ||
    !!restoredControl.error ||
    restoringMaterials ||
    !!materialsError;
  const stopTaskId = active
    ? (runtime.run?.task_id ?? task?.id)
    : task && ['queued', 'running', 'waiting_media'].includes(task.status)
      ? task.id
      : undefined;
  const stopping =
    busy === 'stop' || runtime.tasks.some((item) => item.id === stopTaskId && item.control_state === 'stop_pending');
  const blocked =
    readOnly ||
    runtime.loading ||
    runtime.accessEnded ||
    !!busy ||
    historyBusy ||
    historyPending ||
    undoBusy ||
    undoPending ||
    contextBusy ||
    restoringMaterials ||
    !!materialsError ||
    !!contextBlockReason ||
    !!uncertain ||
    !!pendingControl ||
    !!restoredSend.error ||
    !!restoredControl.error ||
    stopping ||
    (!task && !model) ||
    !draft.trim();
  const taskRun = task ? [runtime.run, ...runtime.queuedRuns].find((run) => run?.task_id === task.id) : undefined;
  const taskModelName = taskRun?.model_name || '沿用当前任务模型';

  return {
    runtime,
    pinned,
    historyRequest,
    setHistoryRequest,
    historyBusy,
    setHistoryBusy,
    historyPending,
    setHistoryPending,
    undoRequest,
    setUndoRequest,
    undoBusy,
    setUndoBusy,
    undoPending,
    setUndoPending,
    restored,
    draft,
    setDraft,
    skills,
    setSkills,
    setSkillsDirty,
    draftError,
    setDraftError,
    models,
    modelId,
    setModelId,
    modelsLoading,
    modelsError,
    setModelRevision,
    contextBusy,
    setContextBusy,
    contextBlockReason,
    setContextBlockReason,
    materials,
    setMaterials,
    restoreMaterials,
    setRestoreMaterials,
    restoringMaterials,
    materialsError,
    setMaterialsError,
    setMaterialsRevision,
    task,
    selectedSkills,
    composerVersion,
    pendingControl,
    busy,
    actionError,
    setActionError,
    uncertain,
    inputContext,
    active,
    send,
    control,
    interject,
    stop,
    addMaterials,
    newTask,
    changeDraft,
    changeSkills,
    contextDisabled,
    stopTaskId,
    stopping,
    blocked,
    taskModelName,
  };
}
