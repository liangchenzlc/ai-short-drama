import type { AssistantOperation } from '@host/types/assistant/api';

export type Row = {
  key: string;
  label: string;
  count: number;
  operation: AssistantOperation;
};
