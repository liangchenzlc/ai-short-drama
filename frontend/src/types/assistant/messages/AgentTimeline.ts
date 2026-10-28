import type { Bubble } from '@ant-design/x';
import type { AssistantOperation } from '@host/types/assistant/api';
import type { AgentMessage } from '@host/types/assistant/engine';

export type BubbleItems = NonNullable<React.ComponentProps<typeof Bubble.List>['items']>;

export interface TimelineProps {
  messages: readonly AgentMessage[];
  operations: readonly AssistantOperation[];
  layout?: 'standard';
  onUndo?: (id: string) => void;
  undoDisabled?: boolean;
  historyDisabled?: boolean;
  onHistory?: (message: AgentMessage, mode: 'edit' | 'regenerate' | 'history') => void;
}
