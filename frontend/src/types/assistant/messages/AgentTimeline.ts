import type { Bubble } from '@ant-design/x';
import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage, AgentRun } from '@host/types/assistant/engine';
import type { AssistantTraceBlock } from '@host/types/assistant/trace';
import type { StreamingTextPart } from '@host/types/assistant/runtime/agent-events';
import type { TraceExpansionProps } from '@host/types/assistant/messages/trace';

export type BubbleItems = NonNullable<React.ComponentProps<typeof Bubble.List>['items']>;

export interface TimelineProps extends TraceExpansionProps {
  messages: readonly AgentMessage[];
  operations: readonly AssistantOperation[];
  traceBlocks?: readonly AssistantTraceBlock[];
  runs?: readonly AgentRun[];
  streamingParts?: readonly StreamingTextPart[];
  streamingRunId?: string;
  conversationId?: string;
  layout?: 'standard';
  onUndo?: (id: string) => void;
  undoDisabled?: boolean;
  historyDisabled?: boolean;
  onHistory?: (message: AgentMessage, mode: 'edit' | 'regenerate' | 'history') => void;
}
