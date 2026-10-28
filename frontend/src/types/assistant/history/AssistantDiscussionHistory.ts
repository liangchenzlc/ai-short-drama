import type { AssistantTaskAccepted } from '@host/types/assistant/api';
import type { AgentMessage } from '@host/types/assistant/engine';

export interface HistoryRequest {
  message: AgentMessage;
  mode: 'edit' | 'regenerate' | 'history';
}

export interface Props {
  accountId: string;
  conversationId: string;
  request: HistoryRequest | null;
  disabled: boolean;
  accessEnded: boolean;
  onClose: () => void;
  onAccepted: (value: AssistantTaskAccepted) => void;
  onBlocked: (value: boolean) => void;
  onBusy: (value: boolean) => void;
  onCorrect: (content: string) => void;
}
