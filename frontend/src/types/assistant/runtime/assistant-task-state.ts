import type { AssistantOperation, AssistantTask } from '@host/types/assistant/api';

export interface TaskProjection {
  conversationId: string;
  snapshotCursor: number;
  deleted: boolean;
  tasks: Record<string, AssistantTask>;
  operations: Record<string, AssistantOperation>;
}
