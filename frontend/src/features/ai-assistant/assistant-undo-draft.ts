import type { AssistantUndoInput } from '../../api/types/assistant';

export interface UndoDraft { version: 1; operationId: string; body: AssistantUndoInput; key: string; pending: boolean }
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const keyFor = (account: string, conversation: string) => `short-drama:user:${account}:assistant-undo:v1:${conversation}`;
function valid(value: unknown): value is UndoDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const draft = value as Partial<UndoDraft>;
  return draft.version === 1 && typeof draft.operationId === 'string' && /^[1-9]\d{0,19}$/.test(draft.operationId)
    && BigInt(draft.operationId) <= 18446744073709551615n && typeof draft.pending === 'boolean'
    && typeof draft.key === 'string' && /^[A-Za-z0-9_:-]{1,64}$/.test(draft.key)
    && !!draft.body && typeof draft.body.preview_hash === 'string' && /^[a-f0-9]{64}$/.test(draft.body.preview_hash)
    && Array.isArray(draft.body.selected_keys) && draft.body.selected_keys.length <= 200
    && draft.body.selected_keys.every(key => typeof key === 'string' && key.length > 0 && key.length <= 240)
    && new Set(draft.body.selected_keys).size === draft.body.selected_keys.length
    && (!draft.pending || draft.body.selected_keys.length > 0)
    && Object.keys(draft).every(key => ['version', 'operationId', 'body', 'key', 'pending'].includes(key))
    && Object.keys(draft.body).every(key => ['preview_hash', 'selected_keys'].includes(key));
}
export function readUndoDraft(account: string, conversation: string, storage: Store) {
  try {
    const raw = storage.getItem(keyFor(account, conversation));
    if (raw === null) return { draft: null, error: '' };
    const value: unknown = JSON.parse(raw);
    if (valid(value)) return { draft: value, error: '' };
  } catch { /* Keep damaged or inaccessible records for explicit recovery. */ }
  return { draft: null, error: '撤销记录无法恢复，原记录已保留。请核对后明确清除。' };
}
export function saveUndoDraft(account: string, conversation: string, draft: UndoDraft, storage: Store) {
  if (!valid(draft)) return false;
  const old = readUndoDraft(account, conversation, storage);
  if (old.error || old.draft?.pending && (old.draft.operationId !== draft.operationId || old.draft.key !== draft.key || JSON.stringify(old.draft.body) !== JSON.stringify(draft.body))) return false;
  try { storage.setItem(keyFor(account, conversation), JSON.stringify(draft)); return true; } catch { return false; }
}
export function clearUndoDraft(account: string, conversation: string, storage: Store, key?: string) {
  const old = readUndoDraft(account, conversation, storage);
  if (key && (old.error || old.draft && old.draft.key !== key)) return false;
  try { storage.removeItem(keyFor(account, conversation)); return true; } catch { return false; }
}
