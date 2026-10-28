import type {
  AssistantSubjectReference,
  AssistantTaskSendInput,
  TaskExecutionTarget,
  TaskMediaLimits,
} from '@host/types/assistant/api';
import type {
  AssistantDraft,
  AssistantPendingAction,
  AssistantPendingSend,
  DraftStorage,
  PendingStorage,
} from '@host/types/assistant/recovery/assistant-draft';
const empty = (): AssistantDraft => ({ version: 1, text: '', skills: [] });
export const assistantDraftKey = (accountId: string, conversationId: string) =>
  `short-drama:user:${accountId}:assistant-draft:v1:${conversationId}`;

export function readAssistantDraft(accountId: string, conversationId: string, storage: DraftStorage): AssistantDraft {
  try {
    const value: unknown = JSON.parse(storage.getItem(assistantDraftKey(accountId, conversationId)) ?? 'null');
    if (!value || typeof value !== 'object') return empty();
    const record = value as Record<string, unknown>;
    if (record.version !== 1 || typeof record.text !== 'string' || !Array.isArray(record.skills)) return empty();
    return {
      version: 1,
      text: record.text.slice(0, 32000),
      ...(typeof record.modelId === 'string' && /^\d+$/.test(record.modelId) ? { modelId: record.modelId } : {}),
      skills: record.skills
        .filter(
          (item): item is { id: string; content_version: string; name?: string } =>
            !!item &&
            typeof item === 'object' &&
            typeof item.id === 'string' &&
            typeof item.content_version === 'string',
        )
        .slice(0, 8)
        .map(({ id, content_version, name }) => ({
          id,
          content_version,
          ...(typeof name === 'string' ? { name: name.slice(0, 120) } : {}),
        })),
      ...(typeof record.skillsDirty === 'boolean' ? { skillsDirty: record.skillsDirty } : {}),
      ...(identifier(record.taskId) ? { taskId: record.taskId } : {}),
      ...(typeof record.newTask === 'boolean' ? { newTask: record.newTask } : {}),
      ...(record.executionTarget === null || validTarget(record.executionTarget)
        ? {
            executionTarget: record.executionTarget as TaskExecutionTarget | null,
          }
        : {}),
      ...(Array.isArray(record.memoryProjectIds) && record.memoryProjectIds.every(identifier)
        ? { memoryProjectIds: record.memoryProjectIds.slice(0, 16) }
        : {}),
      ...(validLimits(record.mediaLimits) ? { mediaLimits: record.mediaLimits as unknown as TaskMediaLimits } : {}),
      ...(typeof record.settingsDirty === 'boolean' ? { settingsDirty: record.settingsDirty } : {}),
      ...(record.materials !== undefined
        ? Array.isArray(record.materials) && record.materials.length <= 16 && record.materials.every(validDraftMaterial)
          ? { materials: record.materials }
          : { materialsInvalid: true }
        : {}),
    };
  } catch {
    return empty();
  }
}

export function saveAssistantDraft(
  accountId: string,
  conversationId: string,
  draft: AssistantDraft,
  storage: DraftStorage,
): boolean {
  try {
    storage.setItem(
      assistantDraftKey(accountId, conversationId),
      JSON.stringify({
        version: 1,
        text: draft.text,
        modelId: draft.modelId,
        skills: draft.skills.map(({ id, content_version, name }) => ({
          id,
          content_version,
          ...(name ? { name: name.slice(0, 120) } : {}),
        })),
        skillsDirty: draft.skillsDirty,
        taskId: draft.taskId,
        newTask: draft.newTask,
        executionTarget: draft.executionTarget,
        memoryProjectIds: draft.memoryProjectIds,
        mediaLimits: draft.mediaLimits,
        settingsDirty: draft.settingsDirty,
        materials: draft.materials?.map(
          ({ project_id, source_kind, source_id, kind, id, revision, content_revision, checksum, name }) => ({
            project_id,
            source_kind,
            source_id,
            kind,
            id,
            revision,
            ...(content_revision ? { content_revision } : {}),
            ...(checksum ? { checksum } : {}),
            name: name.slice(0, 120),
          }),
        ),
      }),
    );
    return true;
  } catch {
    return false;
  }
}
const pendingError = '待核对的发送记录无法恢复，请先核对本会话的消息与任务，暂不能发送新请求。';
export const assistantPendingSendKey = (accountId: string, conversationId: string) =>
  `short-drama:user:${accountId}:assistant-pending-send:v1:${conversationId}`;
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const identifier = (value: unknown): value is string =>
  typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value) && BigInt(value) <= 18446744073709551615n;
const only = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every((key) => keys.includes(key));
function validTarget(value: unknown): value is TaskExecutionTarget {
  return (
    record(value) &&
    only(value, ['mode', 'project_id', 'canvas_id']) &&
    identifier(value.project_id) &&
    ((value.mode === 'standard' && value.canvas_id == null) ||
      (value.mode === 'infinite_canvas' &&
        typeof value.canvas_id === 'string' &&
        value.canvas_id.length > 0 &&
        value.canvas_id.length <= 128))
  );
}
function validLimits(value: unknown): value is Record<'images' | 'videos' | 'audio', number | null> {
  return (
    record(value) &&
    only(value, ['images', 'videos', 'audio']) &&
    ['images', 'videos', 'audio'].every(
      (key) =>
        value[key] == null || (Number.isInteger(value[key]) && Number(value[key]) >= 0 && Number(value[key]) <= 1000),
    )
  );
}

const subjectKey = (value: unknown): value is string =>
  typeof value === 'string' && /^subject:[0-9a-f]{64}$/.test(value);
const subjectFields = [
  'project_id',
  'source_kind',
  'source_id',
  'kind',
  'id',
  'revision',
  'content_revision',
  'checksum',
];
function validDraftMaterial(value: unknown): value is AssistantSubjectReference & { name: string } {
  if (!record(value) || typeof value.name !== 'string' || value.name.length > 120) return false;
  const { name: _name, ...reference } = value;
  return validSubjectReference(reference);
}
export function validSubjectReference(value: unknown): value is AssistantSubjectReference {
  if (
    !record(value) ||
    !only(value, subjectFields) ||
    !identifier(value.project_id) ||
    !identifier(value.revision) ||
    typeof value.source_id !== 'string' ||
    !value.source_id ||
    value.source_id.length > 128 ||
    typeof value.id !== 'string' ||
    !value.id ||
    value.id.length > 128 ||
    !['asset', 'shot', 'node', 'image_group', 'image_slot', 'sound_line'].includes(String(value.kind)) ||
    (value.checksum != null && (typeof value.checksum !== 'string' || !/^[0-9a-f]{64}$/.test(value.checksum))) ||
    (value.content_revision != null && (!identifier(value.content_revision) || value.kind !== 'node'))
  )
    return false;
  return value.kind === 'node'
    ? value.source_kind === 'canvas'
    : value.source_kind === 'project' &&
        value.source_id === value.project_id &&
        (value.kind === 'sound_line' ? /^[a-zA-Z0-9_-]{1,64}$/.test(value.id) : identifier(value.id));
}
function pendingBody(value: unknown): value is AssistantTaskSendInput {
  if (
    !record(value) ||
    !only(value, [
      'content',
      'model_config_id',
      'attachment_ids',
      'skills',
      'video_audio',
      'task_id',
      'new_task',
      'execution_target',
      'memory_project_ids',
      'media_limits',
      'subject_selection',
      'attachment_selection',
      'script_reference',
    ]) ||
    typeof value.content !== 'string' ||
    !value.content.trim() ||
    value.content.length > 32000 ||
    (value.script_reference != null && !validScriptReference(value.script_reference)) ||
    (value.model_config_id !== undefined && !identifier(value.model_config_id)) ||
    (value.task_id !== undefined && !identifier(value.task_id)) ||
    (value.new_task !== undefined && typeof value.new_task !== 'boolean') ||
    (value.new_task === true && value.task_id !== undefined) ||
    (value.execution_target !== undefined && value.execution_target !== null && !validTarget(value.execution_target)) ||
    (value.memory_project_ids !== undefined &&
      (!Array.isArray(value.memory_project_ids) ||
        value.memory_project_ids.length > 16 ||
        !value.memory_project_ids.every(identifier))) ||
    (value.media_limits !== undefined && !validLimits(value.media_limits))
  )
    return false;
  if (value.attachment_selection !== undefined) {
    const selection = value.attachment_selection;
    if (
      !record(selection) ||
      !only(selection, ['expected_input_revision', 'retain_ids']) ||
      !nonnegativeVersion(selection.expected_input_revision) ||
      !Array.isArray(selection.retain_ids) ||
      selection.retain_ids.length > 16 ||
      !selection.retain_ids.every(identifier) ||
      new Set(selection.retain_ids).size !== selection.retain_ids.length
    )
      return false;
  }
  if (value.subject_selection !== undefined) {
    const selection = value.subject_selection;
    if (
      !record(selection) ||
      !only(selection, ['expected_input_revision', 'retain_keys', 'subjects']) ||
      !nonnegativeVersion(selection.expected_input_revision) ||
      !Array.isArray(selection.retain_keys) ||
      !selection.retain_keys.every(subjectKey) ||
      new Set(selection.retain_keys).size !== selection.retain_keys.length ||
      !Array.isArray(selection.subjects) ||
      !selection.subjects.every(validSubjectReference) ||
      selection.subjects.length + selection.retain_keys.length > 16 ||
      new Set(
        selection.subjects.map(
          (item) => `${item.project_id}:${item.source_kind}:${item.source_id}:${item.kind}:${item.id}`,
        ),
      ).size !== selection.subjects.length
    )
      return false;
  }
  if (
    value.attachment_ids !== undefined &&
    (!Array.isArray(value.attachment_ids) ||
      value.attachment_ids.length > 16 ||
      !value.attachment_ids.every(identifier) ||
      new Set(value.attachment_ids).size !== value.attachment_ids.length)
  )
    return false;
  if (
    value.skills !== undefined &&
    (!Array.isArray(value.skills) ||
      value.skills.length > 8 ||
      !value.skills.every(
        (skill) =>
          record(skill) &&
          only(skill, ['id', 'content_version']) &&
          typeof skill.id === 'string' &&
          skill.id.length <= 80 &&
          (identifier(skill.id) ||
            /^builtin:[a-z][a-z0-9_-]*$/.test(skill.id) ||
            /^[a-z][a-z0-9_-]*\.v[1-9]\d*$/.test(skill.id)) &&
          identifier(skill.content_version),
      ))
  )
    return false;
  return value.video_audio === undefined || value.video_audio === 'include' || value.video_audio === 'visual_only';
}

function validScriptReference(value: unknown): boolean {
  if (
    !record(value) ||
    !only(value, ['project_id', 'script_id', 'content_version', 'start', 'end']) ||
    !identifier(value.project_id) ||
    (value.script_id !== null && !identifier(value.script_id)) ||
    !identifier(value.content_version)
  )
    return false;
  return (
    (value.start == null && value.end == null) ||
    (Number.isSafeInteger(value.start) &&
      Number.isSafeInteger(value.end) &&
      (value.start as number) >= 0 &&
      (value.end as number) > (value.start as number))
  );
}

function pendingRecord(value: unknown): value is AssistantPendingSend {
  return (
    record(value) &&
    only(value, ['version', 'body', 'key', 'draft']) &&
    value.version === 1 &&
    typeof value.key === 'string' &&
    /^[A-Za-z0-9_:-]{1,64}$/.test(value.key) &&
    typeof value.draft === 'string' &&
    value.draft.length <= 32000 &&
    pendingBody(value.body)
  );
}

export function readAssistantPendingSend(
  accountId: string,
  conversationId: string,
  storage: PendingStorage,
): { pending: AssistantPendingSend | null; error: string } {
  try {
    const raw = storage.getItem(assistantPendingSendKey(accountId, conversationId));
    if (raw === null) return { pending: null, error: '' };
    const value: unknown = JSON.parse(raw);
    return pendingRecord(value) ? { pending: value, error: '' } : { pending: null, error: pendingError };
  } catch {
    return { pending: null, error: pendingError };
  }
}

export function saveAssistantPendingSend(
  accountId: string,
  conversationId: string,
  pending: AssistantPendingSend,
  storage: PendingStorage,
): boolean {
  if (!pendingRecord(pending)) return false;
  const restored = readAssistantPendingSend(accountId, conversationId, storage);
  // 未知受理结果必须保留原请求体和幂等键；当前输入不能覆盖仍待核对的发送。
  if (
    restored.error ||
    (restored.pending &&
      (restored.pending.key !== pending.key ||
        restored.pending.draft !== pending.draft ||
        JSON.stringify(restored.pending.body) !== JSON.stringify(pending.body)))
  )
    return false;
  try {
    storage.setItem(
      assistantPendingSendKey(accountId, conversationId),
      JSON.stringify({
        version: 1,
        body: pending.body,
        key: pending.key,
        draft: pending.draft,
      }),
    );
    return true;
  } catch {
    return false;
  }
}

export function clearAssistantPendingSend(
  accountId: string,
  conversationId: string,
  key: string,
  storage: PendingStorage,
): boolean {
  const restored = readAssistantPendingSend(accountId, conversationId, storage);
  if (restored.error || (restored.pending && restored.pending.key !== key)) return false;
  try {
    storage.removeItem(assistantPendingSendKey(accountId, conversationId));
    return true;
  } catch {
    return false;
  }
}
export const assistantPendingActionKey = (
  accountId: string,
  conversationId: string,
  kind: AssistantPendingAction['kind'],
) => `short-drama:user:${accountId}:assistant-pending-${kind}:v1:${conversationId}`;
const nonnegativeVersion = (value: unknown) =>
  typeof value === 'string' && /^(0|[1-9]\d{0,19})$/.test(value) && BigInt(value) <= 18446744073709551615n;
function pendingAction(value: unknown): value is AssistantPendingAction {
  if (
    !record(value) ||
    !only(value, ['version', 'kind', 'targetId', 'key', 'body']) ||
    value.version !== 1 ||
    !identifier(value.targetId) ||
    typeof value.key !== 'string' ||
    !/^[A-Za-z0-9_:-]{1,64}$/.test(value.key) ||
    !record(value.body)
  )
    return false;
  const body = value.body;
  if (value.kind === 'delete')
    return (
      only(body, ['row_version', 'clear_derived_memory', 'memory_selections']) &&
      identifier(body.row_version) &&
      typeof body.clear_derived_memory === 'boolean' &&
      (body.memory_selections === undefined ||
        (Array.isArray(body.memory_selections) &&
          body.memory_selections.length <= 200 &&
          body.memory_selections.every(
            (item) =>
              record(item) &&
              only(item, ['id', 'content_version']) &&
              identifier(item.id) &&
              identifier(item.content_version),
          ) &&
          new Set(body.memory_selections.map((item) => (item as { id: string }).id)).size ===
            body.memory_selections.length &&
          (body.clear_derived_memory || body.memory_selections.length === 0)))
    );
  return value.kind === 'control' && only(body, ['kind']) && body.kind === 'stop';
}
export function readAssistantPendingAction(
  accountId: string,
  conversationId: string,
  kind: AssistantPendingAction['kind'],
  storage: PendingStorage,
): { pending: AssistantPendingAction | null; error: string } {
  const error = '待核对的操作记录无法恢复，请先核对本会话状态，暂不能提交新的操作。';
  try {
    const raw = storage.getItem(assistantPendingActionKey(accountId, conversationId, kind));
    if (raw === null) return { pending: null, error: '' };
    const value: unknown = JSON.parse(raw);
    return pendingAction(value) && value.kind === kind ? { pending: value, error: '' } : { pending: null, error };
  } catch {
    return { pending: null, error };
  }
}
export function saveAssistantPendingAction(
  accountId: string,
  conversationId: string,
  pending: AssistantPendingAction,
  storage: PendingStorage,
): boolean {
  if (!pendingAction(pending)) return false;
  const current = readAssistantPendingAction(accountId, conversationId, pending.kind, storage);
  if (current.error || (current.pending && JSON.stringify(current.pending) !== JSON.stringify(pending))) return false;
  try {
    storage.setItem(assistantPendingActionKey(accountId, conversationId, pending.kind), JSON.stringify(pending));
    return true;
  } catch {
    return false;
  }
}
export function clearAssistantPendingAction(
  accountId: string,
  conversationId: string,
  kind: AssistantPendingAction['kind'],
  key: string,
  storage: PendingStorage,
): boolean {
  const current = readAssistantPendingAction(accountId, conversationId, kind, storage);
  if (current.error || (current.pending && current.pending.key !== key)) return false;
  try {
    storage.removeItem(assistantPendingActionKey(accountId, conversationId, kind));
    return true;
  } catch {
    return false;
  }
}
