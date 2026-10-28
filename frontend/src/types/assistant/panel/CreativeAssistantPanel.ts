import type { TaskExecutionTarget } from '@host/types/assistant/api';
import type { AssistantContextProps } from '@host/types/assistant/conversations/AssistantConversationBody';

export interface CreativeAssistantPanelProps extends AssistantContextProps {
  accountId?: string;
  layout?: 'standard';
  projectId: string;
  projectTitle: string;
  executionTarget?: TaskExecutionTarget;
  open?: boolean;
  readOnly?: boolean;
  onClose: () => void;
  selectedConversationId?: string | null;
  onConversationChange?: (id: string) => void;
  onOpenModelSettings: () => void;
}
