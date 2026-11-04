import type { AssistantTraceBlock } from '@host/types/assistant/trace';
import type { AssistantOperation } from '@host/types/assistant/api';
export interface TraceExpansionProps {
  expandedItems: ReadonlySet<string>;
  onExpandedChange: (ids: readonly string[], expanded: boolean) => void;
}
export interface TraceBlockProps extends TraceExpansionProps {
  block?: AssistantTraceBlock;
  operation?: AssistantOperation;
  conversationId: string;
}
