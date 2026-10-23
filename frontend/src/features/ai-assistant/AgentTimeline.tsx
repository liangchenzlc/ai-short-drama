import type { ReactNode } from 'react';
import { Bubble } from '@ant-design/x';
import type { AgentMessage } from '../../api/types/agents';
import type { AssistantOperation } from '../../api/types/assistant';
import { AgentArtifactCard } from './AgentArtifactCard';
import { AssistantMessage } from './AssistantMessage';
import { ThinkBlock } from './ThinkBlock';
import { buildTimeline } from './agent-timeline';

type BubbleItems = NonNullable<React.ComponentProps<typeof Bubble.List>['items']>;

export interface TimelineProps {
  messages: readonly AgentMessage[];
  operations: readonly AssistantOperation[];
  layout?: 'standard';
  onUndo?: (id: string) => void;
  undoDisabled?: boolean;
  historyDisabled?: boolean;
  onHistory?: (message: AgentMessage, mode: 'edit' | 'regenerate' | 'history') => void;
}

/**
 * Turns the ordered timeline into chat rows: narration keeps its bubble, consecutive
 * steps fold into one block, artifacts stand alone. Silent steps never reach here.
 */
export function timelineBubbles({ messages, operations, layout, onUndo, undoDisabled, historyDisabled, onHistory }: TimelineProps): BubbleItems {
  return buildTimeline(messages, operations).map(entry => {
    if (entry.kind === 'message') {
      const { message } = entry;
      const user = message.role === 'user';
      return {
        key: entry.key,
        role: message.role,
        placement: user ? 'end' as const : 'start' as const,
        variant: 'borderless' as const,
        className: user ? 'ai-assistant-bubble-user' : 'ai-assistant-bubble-reply',
        content: <AssistantMessage message={message} layout={layout} historyDisabled={historyDisabled}
          onHistory={!user && onHistory ? mode => onHistory(message, mode) : undefined}/>,
      };
    }
    return {
      key: entry.key,
      role: 'assistant',
      placement: 'start' as const,
      variant: 'borderless' as const,
      className: 'ai-assistant-bubble-reply',
      content: entry.kind === 'steps'
        ? <ThinkBlock operations={entry.operations}/>
        : <AgentArtifactCard operation={entry.operation} layout={layout} onUndo={onUndo} undoDisabled={undoDisabled}/>,
    } as { key: string; role: string; placement: 'start'; variant: 'borderless'; className: string; content: ReactNode };
  }) as BubbleItems;
}
