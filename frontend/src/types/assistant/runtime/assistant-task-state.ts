import type { AssistantOperation, AssistantTask } from '@host/types/assistant/api';
import type { AssistantTracePage, AssistantTraceProjection } from '@host/types/assistant/trace';

export type RunTracePagination = Pick<AssistantTracePage, 'has_more' | 'next_before_seq'>;
export interface OperationPagination { afterId?: string; complete: boolean }

export interface TaskProjection extends AssistantTraceProjection {
  conversationId: string;
  snapshotCursor: number;
  operationSnapshotCursors: Record<string, number>;
  traceSnapshotCursors: Record<string, number>;
  deleted: boolean;
  tasks: Record<string, AssistantTask>;
  operations: Record<string, AssistantOperation>;
}
