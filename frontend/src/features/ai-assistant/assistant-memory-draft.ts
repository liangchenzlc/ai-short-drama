import type { AssistantMemory, AssistantMemoryCreate } from '../../api/types/assistant-memory';

export interface MemoryDraft {
  version: 1; item: AssistantMemory | null; values: AssistantMemoryCreate; key: string; pending: boolean;
}
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const keyFor = (accountId: string) => `assistant-memory-draft:v1:${encodeURIComponent(accountId)}`;
function valid(value: unknown): value is MemoryDraft {
  if (!value || typeof value !== 'object') return false;
  const draft = value as Partial<MemoryDraft>;
  const body = draft.values;
  return draft.version === 1 && typeof draft.key === 'string' && /^[A-Za-z0-9_:-]{1,64}$/.test(draft.key)
    && typeof draft.pending === 'boolean' && !!body
    && ['user', 'conversation', 'project'].includes(body.scope_type)
    && ['preference', 'correction', 'setting', 'decision', 'reference'].includes(body.kind)
    && typeof body.memory_key === 'string' && typeof body.title === 'string' && typeof body.content === 'string'
    && typeof body.pinned === 'boolean' && Array.isArray(body.aliases) && body.aliases.every(item => typeof item === 'string')
    && Array.isArray(body.tags) && body.tags.every(item => typeof item === 'string')
    && (body.scope_type !== 'conversation' || typeof body.conversation_id === 'string' && /^[1-9]\d*$/.test(body.conversation_id))
    && (body.scope_type !== 'project' || typeof body.project_id === 'string' && /^[1-9]\d*$/.test(body.project_id))
    && (draft.item === null || !!draft.item && typeof draft.item.id === 'string' && /^[1-9]\d*$/.test(draft.item.id)
      && typeof draft.item.content_version === 'string' && /^[1-9]\d*$/.test(draft.item.content_version));
}
export function readMemoryDraft(accountId: string, storage: StorageLike | null): { draft: MemoryDraft | null; error: string } {
  try {
    const raw = storage?.getItem(keyFor(accountId));
    if (!raw) return { draft: null, error: storage ? '' : '浏览器存储不可用，无法保存记忆草稿。' };
    const value: unknown = JSON.parse(raw);
    if (valid(value)) return { draft: value, error: '' };
  } catch { /* Keep the original record for explicit recovery. */ }
  return { draft: null, error: '记忆草稿无法读取，原记录已保留。请明确清除后再编辑。' };
}
export function saveMemoryDraft(accountId: string, draft: MemoryDraft, storage: StorageLike | null): boolean {
  if (!storage || !valid(draft)) return false;
  const prior = readMemoryDraft(accountId, storage);
  if (prior.error || prior.draft?.pending && (
    prior.draft.key !== draft.key || JSON.stringify(prior.draft.values) !== JSON.stringify(draft.values)
    || prior.draft.item?.id !== draft.item?.id || prior.draft.item?.content_version !== draft.item?.content_version
  )) return false;
  try { storage.setItem(keyFor(accountId), JSON.stringify(draft)); return true; } catch { return false; }
}
export function clearMemoryDraft(accountId: string, storage: StorageLike | null): boolean {
  if (!storage) return false;
  try { storage.removeItem(keyFor(accountId)); return true; } catch { return false; }
}
