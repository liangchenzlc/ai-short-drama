import type { AssistantPendingQuestion, AssistantQuestionAnswer, AssistantQuestionRequest, AssistantTask } from '@host/types/assistant/api';
import type { AgentRun } from '@host/types/assistant/engine';

export type AssistantAnswerableQuestion = AssistantPendingQuestion & { request: AssistantQuestionRequest };

export interface AssistantQuestionDraft {
  values: AssistantQuestionAnswer['values'];
  otherValues: Record<string, string>;
  otherSelected: string[];
  index: number;
  pending: { key: string; body: AssistantQuestionAnswer } | null;
}
export interface AssistantQuestionCardProps {
  accountId: string;
  conversationId: string;
  question: AssistantAnswerableQuestion;
  active: boolean;
  onResolved: (id: string) => void;
  disabled: boolean;
  refresh: () => Promise<AssistantPendingQuestion | null>;
}

export interface AssistantQuestionDockProps {
  accountId: string;
  conversationId: string;
  questions: AssistantPendingQuestion[];
  runs: AgentRun[];
  tasks: AssistantTask[];
  disabled: boolean;
  refresh: (id: string) => Promise<AssistantPendingQuestion | null>;
}
