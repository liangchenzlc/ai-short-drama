import type { CreationDraftValue, UserCreationDraft } from '@host/types/creation/drafts/requests';
import type { CreationDraftSnapshot } from '@host/types/creation/shared/sessions/creation-draft-session';

/** 生成面板只依赖草稿操作契约，不依赖持久化会话的内部状态。 */
export interface PrivateDraft extends CreationDraftSnapshot {
  session: { getSnapshot(): CreationDraftSnapshot };
  edit(patch: Partial<CreationDraftValue>): void;
  flush(): Promise<UserCreationDraft | null>;
  retry(): Promise<UserCreationDraft | null>;
  reload(): Promise<void>;
}
