import type { DiscussionRevisionInput } from '@host/types/assistant/api';

export interface DiscussionDraft {
  version: 1;
  messageId: string;
  body: DiscussionRevisionInput;
  key: string;
  pending: boolean;
}

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
