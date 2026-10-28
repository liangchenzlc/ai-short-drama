import type { AssistantUndoInput } from '@host/types/assistant/api';

export interface UndoDraft {
  version: 1;
  operationId: string;
  body: AssistantUndoInput;
  key: string;
  pending: boolean;
}

export type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
