import type { AssistantTraceBlock } from '@host/types/assistant/trace';
import type { ReactNode } from 'react';
export interface TraceBlockProps {
  block: AssistantTraceBlock;
  conversationId: string;
  children?: ReactNode;
  renderResults: (block: AssistantTraceBlock) => ReactNode;
}
