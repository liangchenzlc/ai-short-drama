import { ApiError } from '@host/api/http';
import { subjectSelectionFor } from '@host/features/assistant/references/assistant-material-service';
import { attemptStorage, requestAttempt } from '@host/features/shared/async/request-receipt';
import type {
  AssistantMessageContext,
  AssistantReference,
  AssistantTaskSendInput,
} from '@host/types/assistant/api';
import type { PrepareAssistantSendInput } from '@host/types/assistant/composer/send';
import type { AssistantPendingSend } from '@host/types/assistant/recovery/assistant-draft';
/** 重试必须复用原始载荷和幂等键；重新收集当前输入会把一次核对变成第二条消息。 */
export async function prepareAssistantSend({
  retry,
  inputContext,
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
}: PrepareAssistantSendInput) {
  if (!retry && !inputContext) throw new ApiError('正在恢复消息上下文，请稍后发送。', 'assistant_context_loading');
  const input = retry ? null : inputContext!.snapshot();
  let context: AssistantMessageContext | null = null;
  if (!retry) {
    try {
      if (captured.materials.length)
        context = await captured.prepareContext(
          false,
          captured.materials.map(({ kind, id, revision }) => ({
            kind: kind as AssistantReference['kind'],
            id,
            revision,
          })),
        );
    } catch (cause) {
      if (
        cause instanceof Error &&
        !(cause instanceof ApiError) &&
        /^(当前|画布|模型偏好|保存期间|作品|引用)/.test(cause.message)
      )
        throw new ApiError(cause.message, 'assistant_context_blocked');
      throw cause;
    }
  }
  if (context && (context.selected?.length ?? 0) > 16)
    throw new ApiError('一条消息最多引用 16 个对象，请减少选择后再发送。', 'assistant_context_limit');
  const selection = retry ? null : await subjectSelectionFor(task?.id ?? null, captured.materials);
  if (!matches()) throw new ApiError('当前作品或素材已变化，消息草稿已保留，请再次发送。', 'assistant_context_changed');
  const body: AssistantTaskSendInput = (retry?.body as AssistantTaskSendInput | undefined) ?? {
    content: submitted.trim(),
    ...input,
    ...(layout === 'standard' ? { script_reference: null } : {}),
    ...(task ? { task_id: task.id } : { new_task: true, model_config_id: modelId }),
    ...(selection ? { subject_selection: selection } : {}),
    ...(!task
      ? {
          execution_target: executionTarget ?? {
            mode: 'standard',
            project_id: projectId,
          },
        }
      : {}),
  };
  if (!retry) {
    if (skillsDirty)
      body.skills = skills.map(({ id, content_version }) => ({
        id,
        content_version,
      }));
    else delete body.skills;
  }
  const key = retry?.key ?? (await requestAttempt(attemptScope, body, attemptStorage()));
  if (!matches()) return null;
  const pending: AssistantPendingSend & { body: AssistantTaskSendInput } = {
    version: 1,
    body,
    key,
    draft: submitted,
  };
  return pending;
}
