import type { AgentSendInput } from '../../api/types/agents';

export interface AgentDraft {
  version: 1; text: string; modelId?: string; skills: { id: string; content_version: string }[];
}
type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>;
const empty = (): AgentDraft => ({ version: 1, text: '', skills: [] });
export const agentDraftKey = (accountId: string, conversationId: string) => `short-drama:user:${accountId}:agent-draft:v1:${conversationId}`;

export function readAgentDraft(accountId: string, conversationId: string, storage: DraftStorage): AgentDraft {
  try {
    const value: unknown = JSON.parse(storage.getItem(agentDraftKey(accountId, conversationId)) ?? 'null');
    if (!value || typeof value !== 'object') return empty();
    const record = value as Record<string, unknown>;
    if (record.version !== 1 || typeof record.text !== 'string' || !Array.isArray(record.skills)) return empty();
    return { version: 1, text: record.text.slice(0, 32000),
      ...(typeof record.modelId === 'string' && /^\d+$/.test(record.modelId) ? { modelId: record.modelId } : {}),
      skills: record.skills.filter((item): item is { id: string; content_version: string } => !!item && typeof item === 'object'
        && typeof item.id === 'string' && typeof item.content_version === 'string').slice(0, 8)
        .map(({ id, content_version }) => ({ id, content_version })),
    };
  } catch { return empty(); }
}

export function saveAgentDraft(accountId: string, conversationId: string, draft: AgentDraft, storage: DraftStorage): boolean {
  try {
    storage.setItem(agentDraftKey(accountId, conversationId), JSON.stringify({ version: 1, text: draft.text,
      modelId: draft.modelId, skills: draft.skills.map(({ id, content_version }) => ({ id, content_version })) }));
    return true;
  } catch { return false; }
}

export interface AgentPendingSend {
  version: 1; body: AgentSendInput; key: string; draft: string;
}
type PendingStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const pendingError = '待核对的发送记录无法恢复，请先核对本会话的消息与任务，暂不能发送新请求。';
export const agentPendingSendKey = (accountId: string, conversationId: string) => `short-drama:user:${accountId}:agent-pending-send:v1:${conversationId}`;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value) && BigInt(value) <= 18446744073709551615n;
const only = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every(key => keys.includes(key));

function pendingBody(value: unknown): value is AgentSendInput {
  if (!record(value) || !only(value, ['content', 'model_config_id', 'expected_scope', 'attachment_ids', 'skills', 'video_audio'])
    || typeof value.content !== 'string' || !value.content.trim() || value.content.length > 32000
    || value.model_config_id !== undefined && !identifier(value.model_config_id)) return false;
  const scope = value.expected_scope;
  if (!record(scope) || !only(scope, ['stage', 'subject_type', 'subject_id', 'task_type']) || !identifier(scope.subject_id)) return false;
  const tasks: Record<string, string[]> = {
    'source:episode': ['writing'], 'assets:episode': ['extraction', 'batch'], 'assets:asset': ['creation', 'image'],
    'storyboard:episode': ['planning', 'batch'], 'storyboard:shot': ['creation', 'image', 'video'],
  };
  if (typeof scope.stage !== 'string' || typeof scope.subject_type !== 'string' || typeof scope.task_type !== 'string'
    || !tasks[`${scope.stage}:${scope.subject_type}`]?.includes(scope.task_type)) return false;
  if (value.attachment_ids !== undefined && (!Array.isArray(value.attachment_ids) || value.attachment_ids.length > 16
    || !value.attachment_ids.every(identifier) || new Set(value.attachment_ids).size !== value.attachment_ids.length)) return false;
  if (value.skills !== undefined && (!Array.isArray(value.skills) || value.skills.length > 8 || !value.skills.every(skill =>
    record(skill) && only(skill, ['id', 'content_version']) && typeof skill.id === 'string'
    && (identifier(skill.id) || /^builtin:[a-z][a-z0-9_-]{0,119}$/.test(skill.id)) && identifier(skill.content_version)))) return false;
  return value.video_audio === undefined || value.video_audio === 'include' || value.video_audio === 'visual_only';
}

function pendingRecord(value: unknown): value is AgentPendingSend {
  return record(value) && value.version === 1 && typeof value.key === 'string' && /^[A-Za-z0-9_:-]{1,64}$/.test(value.key)
    && typeof value.draft === 'string' && value.draft.length <= 32000 && pendingBody(value.body);
}

export function readAgentPendingSend(accountId: string, conversationId: string, storage: PendingStorage): { pending: AgentPendingSend | null; error: string } {
  try {
    const raw = storage.getItem(agentPendingSendKey(accountId, conversationId));
    if (raw === null) return { pending: null, error: '' };
    const value: unknown = JSON.parse(raw);
    return pendingRecord(value) ? { pending: value, error: '' } : { pending: null, error: pendingError };
  } catch { return { pending: null, error: pendingError }; }
}

export function saveAgentPendingSend(accountId: string, conversationId: string, pending: AgentPendingSend, storage: PendingStorage): boolean {
  if (!pendingRecord(pending)) return false;
  const restored = readAgentPendingSend(accountId, conversationId, storage);
  if (restored.error || restored.pending && (restored.pending.key !== pending.key || restored.pending.draft !== pending.draft
    || JSON.stringify(restored.pending.body) !== JSON.stringify(pending.body))) return false;
  try {
    storage.setItem(agentPendingSendKey(accountId, conversationId), JSON.stringify({ version: 1, body: pending.body, key: pending.key, draft: pending.draft }));
    return true;
  } catch { return false; }
}

export function clearAgentPendingSend(accountId: string, conversationId: string, key: string, storage: PendingStorage): boolean {
  const restored = readAgentPendingSend(accountId, conversationId, storage);
  if (restored.error || restored.pending && restored.pending.key !== key) return false;
  try { storage.removeItem(agentPendingSendKey(accountId, conversationId)); return true; }
  catch { return false; }
}
