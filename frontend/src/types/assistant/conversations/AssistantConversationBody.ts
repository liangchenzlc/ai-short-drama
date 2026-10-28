import type {
  AssistantMentionReference,
  AssistantMessageContext,
  AssistantReference,
  ProjectAssistantConversation,
  TaskExecutionTarget,
} from '@host/types/assistant/api';

export interface AssistantContextProps {
  contextPreview?: { key: string; label: string };
  mentionReferences?: readonly AssistantMentionReference[];
  prepareContext: (
    include: boolean,
    references?: readonly AssistantReference[],
  ) => Promise<AssistantMessageContext | null>;
}

export interface AssistantConversationProps extends AssistantContextProps {
  conversation: ProjectAssistantConversation;
  accountId: string;
  projectId: string;
  executionTarget?: TaskExecutionTarget;
  layout?: 'standard';
  readOnly: boolean;
  onOpenModelSettings: () => void;
  onSendingChange: (sending: boolean) => void;
}
