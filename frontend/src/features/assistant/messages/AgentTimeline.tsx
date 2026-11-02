import { AssistantMessage } from '@host/features/assistant/messages/AssistantMessage';
import { ToolExecutionBlock } from '@host/features/assistant/messages/ToolExecutionBlock';
import { AssistantTraceBlock } from '@host/features/assistant/messages/AssistantTraceBlock';
import { buildTimeline } from '@host/features/assistant/messages/agent-timeline';
import { AgentArtifactCard } from '@host/features/assistant/operations/AgentArtifactCard';
import { OperationResults } from '@host/features/assistant/operations/AssistantOperationResults';
import type { BubbleItems, TimelineProps } from '@host/types/assistant/messages/AgentTimeline';
import type { TraceBlockProps } from '@host/types/assistant/messages/trace';
import type { ReactNode } from 'react';

export function traceOperationResults({ operations, layout, onUndo, undoDisabled }: Pick<TimelineProps, 'operations' | 'layout' | 'onUndo' | 'undoDisabled'>): TraceBlockProps['renderResults'] {
  const operationsById = new Map(operations.map(operation => [operation.id, operation]));
  return block => {
    const operation = block.operation_id ? operationsById.get(block.operation_id) : undefined;
    return operation?.visibility === 'artifact'
      ? <OperationResults operation={operation} layout={layout} onUndo={onUndo} undoDisabled={undoDisabled} />
      : null;
  };
}
/**
 * Turns the ordered timeline into chat rows: narration keeps its bubble, consecutive
 * steps fold into one block, artifacts stand alone. Silent steps remain readable.
 */
export function timelineBubbles({
  messages,
  operations,
  traceBlocks = [],
  runs,
  conversationId = '',
  layout,
  onUndo,
  undoDisabled,
  historyDisabled,
  onHistory,
}: TimelineProps, renderResults = traceOperationResults({ operations, layout, onUndo, undoDisabled })): BubbleItems {
  return buildTimeline(messages, operations, traceBlocks, runs).map((entry) => {
    if (entry.kind === 'message') {
      const { message } = entry;
      const user = message.role === 'user';
      return {
        key: entry.key,
        role: message.role,
        placement: user ? ('end' as const) : ('start' as const),
        variant: 'borderless' as const,
        className: user ? 'ai-assistant-bubble-user' : 'ai-assistant-bubble-reply',
        content: (
          <AssistantMessage
            message={message}
            layout={layout}
            historyDisabled={historyDisabled}
            onHistory={!user && onHistory ? (mode) => onHistory(message, mode) : undefined}
          />
        ),
      };
    }
    return {
      key: entry.key,
      role: 'assistant',
      placement: 'start' as const,
      variant: 'borderless' as const,
      className: 'ai-assistant-bubble-reply',
      content:
        entry.kind === 'trace' ? <AssistantTraceBlock block={entry.block} blocks={traceBlocks} conversationId={conversationId} renderResults={renderResults} /> : entry.kind === 'steps' ? (
          <ToolExecutionBlock operations={entry.operations} />
        ) : (
          <AgentArtifactCard operation={entry.operation} layout={layout} onUndo={onUndo} undoDisabled={undoDisabled} />
        ),
    } as {
      key: string;
      role: string;
      placement: 'start';
      variant: 'borderless';
      className: string;
      content: ReactNode;
    };
  }) as BubbleItems;
}
