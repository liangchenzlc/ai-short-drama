import type { AssistantMentionReference, AssistantSendInput } from '../../api/types/assistant';

export interface AssistantDraft {
  version: 1; text: string; modelId?: string; skills: { id: string; content_version: string }[];
  contextKey?: string;
  includeContext?: boolean;
  references?: AssistantMentionReference[];
}
type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>;
const empty = (): AssistantDraft => ({ version: 1, text: '', skills: [] });
export const assistantDraftKey = (accountId: string, conversationId: string) => `short-drama:user:${accountId}:assistant-draft:v1:${conversationId}`;

export function readAssistantDraft(accountId: string, conversationId: string, storage: DraftStorage): AssistantDraft {
  try {
    const value: unknown = JSON.parse(storage.getItem(assistantDraftKey(accountId, conversationId)) ?? 'null');
    if (!value || typeof value !== 'object') return empty();
    const record = value as Record<string, unknown>;
    if (record.version !== 1 || typeof record.text !== 'string' || !Array.isArray(record.skills)) return empty();
    return { version: 1, text: record.text.slice(0, 32000),
      ...(typeof record.modelId === 'string' && /^\d+$/.test(record.modelId) ? { modelId: record.modelId } : {}),
      skills: record.skills.filter((item): item is { id: string; content_version: string } => !!item && typeof item === 'object'
        && typeof item.id === 'string' && typeof item.content_version === 'string').slice(0, 8)
        .map(({ id, content_version }) => ({ id, content_version })),
      ...(typeof record.contextKey === 'string' ? { contextKey: record.contextKey.slice(0, 512) } : {}),
      ...(typeof record.includeContext === 'boolean' ? { includeContext: record.includeContext } : {}),
      ...(Array.isArray(record.references) ? { references: record.references.filter((item): item is AssistantMentionReference => !!item && typeof item === 'object'
        && ['asset', 'shot', 'node'].includes(item.kind) && typeof item.id === 'string' && item.id.length > 0 && item.id.length <= 128
        && typeof item.name === 'string' && (item.revision === undefined || typeof item.revision === 'string' && /^\d+$/.test(item.revision)))
        .slice(0, 100).map(({ kind, id, name, revision }) => ({ kind, id, name: name.slice(0, 200), ...(revision ? { revision } : {}) })) } : {}),
    };
  } catch { return empty(); }
}

export function saveAssistantDraft(accountId: string, conversationId: string, draft: AssistantDraft, storage: DraftStorage): boolean {
  try {
    storage.setItem(assistantDraftKey(accountId, conversationId), JSON.stringify({ version: 1, text: draft.text,
      modelId: draft.modelId, skills: draft.skills.map(({ id, content_version }) => ({ id, content_version })),
      contextKey: draft.contextKey, includeContext: draft.includeContext,
      references: draft.references?.map(({ kind, id, name, revision }) => ({ kind, id, name, revision })),
    }));
    return true;
  } catch { return false; }
}

export interface AssistantPendingSend {
  version: 1; body: AssistantSendInput; key: string; draft: string;
}
type PendingStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const pendingError = '待核对的发送记录无法恢复，请先核对本会话的消息与任务，暂不能发送新请求。';
export const assistantPendingSendKey = (accountId: string, conversationId: string) => `short-drama:user:${accountId}:assistant-pending-send:v1:${conversationId}`;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value) && BigInt(value) <= 18446744073709551615n;
const only = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every(key => keys.includes(key));

function pendingBody(value: unknown): value is AssistantSendInput {
  if (!record(value) || !only(value, ['content', 'model_config_id', 'context', 'attachment_ids', 'skills', 'video_audio'])
    || typeof value.content !== 'string' || !value.content.trim() || value.content.length > 32000
    || value.model_config_id !== undefined && !identifier(value.model_config_id)) return false;
  const context = value.context;
  if (context !== undefined && context !== null) {
    if (!record(context) || !only(context, ['kind', 'id', 'revision', 'include_document', 'stage', 'storyboard_revision', 'selected'])
      || !['episode', 'canvas'].includes(String(context.kind)) || typeof context.id !== 'string' || !context.id || context.id.length > 128
      || context.include_document !== undefined && typeof context.include_document !== 'boolean'
      || !identifier(context.revision) || context.kind === 'episode' && !identifier(context.id)
      || context.stage !== undefined && !['source', 'assets', 'storyboard', 'assembly'].includes(String(context.stage))
      || context.storyboard_revision !== undefined && !identifier(context.storyboard_revision)) return false;
    if (context.kind === 'canvas' && (context.stage !== undefined || context.storyboard_revision !== undefined)) return false;
    if (context.selected !== undefined && (!Array.isArray(context.selected) || context.selected.length > 16 || !context.selected.every(item =>
      record(item) && only(item, ['kind', 'id', 'revision']) && ['asset', 'shot', 'node'].includes(String(item.kind))
      && typeof item.id === 'string' && !!item.id && item.id.length <= 128 && (item.kind === 'node' || identifier(item.id))
      && (item.kind === 'node') === (context.kind === 'canvas')
      && (item.revision === undefined || identifier(item.revision))))) return false;
    if (Array.isArray(context.selected) && new Set(context.selected.map(item => `${item.kind}:${item.id}`)).size !== context.selected.length) return false;
  }
  if (value.attachment_ids !== undefined && (!Array.isArray(value.attachment_ids) || value.attachment_ids.length > 16
    || !value.attachment_ids.every(identifier) || new Set(value.attachment_ids).size !== value.attachment_ids.length)) return false;
  if (value.skills !== undefined && (!Array.isArray(value.skills) || value.skills.length > 8 || !value.skills.every(skill =>
    record(skill) && only(skill, ['id', 'content_version']) && typeof skill.id === 'string'
    && (identifier(skill.id) || /^builtin:[a-z][a-z0-9_-]{0,119}$/.test(skill.id)) && identifier(skill.content_version)))) return false;
  return value.video_audio === undefined || value.video_audio === 'include' || value.video_audio === 'visual_only';
}

function pendingRecord(value: unknown): value is AssistantPendingSend {
  return record(value) && value.version === 1 && typeof value.key === 'string' && /^[A-Za-z0-9_:-]{1,64}$/.test(value.key)
    && typeof value.draft === 'string' && value.draft.length <= 32000 && pendingBody(value.body);
}

export function readAssistantPendingSend(accountId: string, conversationId: string, storage: PendingStorage): { pending: AssistantPendingSend | null; error: string } {
  try {
    const raw = storage.getItem(assistantPendingSendKey(accountId, conversationId));
    if (raw === null) return { pending: null, error: '' };
    const value: unknown = JSON.parse(raw);
    return pendingRecord(value) ? { pending: value, error: '' } : { pending: null, error: pendingError };
  } catch { return { pending: null, error: pendingError }; }
}

export function saveAssistantPendingSend(accountId: string, conversationId: string, pending: AssistantPendingSend, storage: PendingStorage): boolean {
  if (!pendingRecord(pending)) return false;
  const restored = readAssistantPendingSend(accountId, conversationId, storage);
  if (restored.error || restored.pending && (restored.pending.key !== pending.key || restored.pending.draft !== pending.draft
    || JSON.stringify(restored.pending.body) !== JSON.stringify(pending.body))) return false;
  try {
    storage.setItem(assistantPendingSendKey(accountId, conversationId), JSON.stringify({ version: 1, body: pending.body, key: pending.key, draft: pending.draft }));
    return true;
  } catch { return false; }
}

export function clearAssistantPendingSend(accountId: string, conversationId: string, key: string, storage: PendingStorage): boolean {
  const restored = readAssistantPendingSend(accountId, conversationId, storage);
  if (restored.error || restored.pending && restored.pending.key !== key) return false;
  try { storage.removeItem(assistantPendingSendKey(accountId, conversationId)); return true; }
  catch { return false; }
}
