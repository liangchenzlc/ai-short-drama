import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage } from '@host/types/assistant/engine';
import type { AssistantTraceBlock } from '@host/types/assistant/trace';

export type TimelineEntry =
  | { kind: 'message'; key: string; message: AgentMessage }
  | { kind: 'steps'; key: string; operations: AssistantOperation[] }
  | { kind: 'trace'; key: string; block: AssistantTraceBlock }
  | { kind: 'artifact'; key: string; operation: AssistantOperation };
