import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage } from '@host/types/assistant/engine';

export type TimelineEntry =
  | { kind: 'message'; key: string; message: AgentMessage }
  | { kind: 'steps'; key: string; operations: AssistantOperation[] }
  | { kind: 'artifact'; key: string; operation: AssistantOperation };
