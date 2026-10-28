import type { DiscussionDraft, StorageLike } from '@host/types/assistant/recovery/assistant-history-draft';
const keyFor = (accountId: string, conversationId: string) =>
  `short-drama:user:${accountId}:assistant-history:v1:${conversationId}`;
const identifier = (value: unknown) =>
  typeof value === 'string' && /^[1-9]\d{0,19}$/.test(value) && BigInt(value) <= 18446744073709551615n;
function valid(value: unknown): value is DiscussionDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const draft = value as Partial<DiscussionDraft>;
  const body = draft.body;
  return (
    draft.version === 1 &&
    identifier(draft.messageId) &&
    typeof draft.pending === 'boolean' &&
    typeof draft.key === 'string' &&
    /^[A-Za-z0-9_:-]{1,64}$/.test(draft.key) &&
    !!body &&
    identifier(body.row_version) &&
    ['edit', 'regenerate'].includes(body.kind) &&
    (body.kind === 'regenerate'
      ? body.content === undefined
      : typeof body.content === 'string' && body.content.length <= 32000) &&
    Object.keys(draft).every((key) => ['version', 'messageId', 'body', 'key', 'pending'].includes(key)) &&
    Object.keys(body).every((key) => ['kind', 'row_version', 'content'].includes(key))
  );
}
export function readDiscussionDraft(accountId: string, conversationId: string, storage: StorageLike) {
  try {
    const raw = storage.getItem(keyFor(accountId, conversationId));
    if (raw === null) return { draft: null, error: '' };
    const value: unknown = JSON.parse(raw);
    if (valid(value)) return { draft: value, error: '' };
  } catch {
    /* Preserve the source record for explicit recovery. */
  }
  return {
    draft: null,
    error: '历史修订草稿无法恢复，原记录已保留。请先核对会话，再明确清除。',
  };
}
export function saveDiscussionDraft(
  accountId: string,
  conversationId: string,
  draft: DiscussionDraft,
  storage: StorageLike,
) {
  if (!valid(draft)) return false;
  const previous = readDiscussionDraft(accountId, conversationId, storage);
  if (
    previous.error ||
    (previous.draft?.pending &&
      (previous.draft.messageId !== draft.messageId ||
        previous.draft.key !== draft.key ||
        JSON.stringify(previous.draft.body) !== JSON.stringify(draft.body)))
  )
    return false;
  try {
    storage.setItem(keyFor(accountId, conversationId), JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}
export function clearDiscussionDraft(accountId: string, conversationId: string, storage: StorageLike, key?: string) {
  const previous = readDiscussionDraft(accountId, conversationId, storage);
  if (key && (previous.error || (previous.draft && previous.draft.key !== key))) return false;
  try {
    storage.removeItem(keyFor(accountId, conversationId));
    return true;
  } catch {
    return false;
  }
}
