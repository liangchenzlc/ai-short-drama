import { AssistantMessage } from '@host/features/assistant/messages/AssistantMessage';
import { ThinkBlock } from '@host/features/assistant/messages/ThinkBlock';
import { buildTimeline } from '@host/features/assistant/messages/agent-timeline';
import { AgentArtifactCard } from '@host/features/assistant/operations/AgentArtifactCard';
import type { BubbleItems, TimelineProps } from '@host/types/assistant/messages/AgentTimeline';
import type { ReactNode } from 'react';
/**
 * Turns the ordered timeline into chat rows: narration keeps its bubble, consecutive
 * steps fold into one block, artifacts stand alone. Silent steps never reach here.
 */
export function timelineBubbles({
  messages,
  operations,
  layout,
  onUndo,
  undoDisabled,
  historyDisabled,
  onHistory,
}: TimelineProps): BubbleItems {
  return buildTimeline(messages, operations).map((entry) => {
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
        entry.kind === 'steps' ? (
          <ThinkBlock operations={entry.operations} />
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
