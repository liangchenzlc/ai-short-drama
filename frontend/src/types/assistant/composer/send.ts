import type { AssistantTask, TaskExecutionTarget } from '@host/types/assistant/api';
import type { AgentContextController } from '@host/types/assistant/attachments/AgentContextComposer';
import type { AssistantContextProps } from '@host/types/assistant/conversations/AssistantConversationBody';
import type { AssistantPendingSend } from '@host/types/assistant/recovery/assistant-draft';
import type { MessageMaterial } from '@host/types/assistant/references/assistant-materials';
import type { AssistantSkillChoice } from '@host/types/assistant/skills/AssistantSkillPicker';
export interface PrepareAssistantSendInput {
  retry?: AssistantPendingSend;
  inputContext: AgentContextController | null;
  context: { materials: MessageMaterial[]; prepareContext: AssistantContextProps['prepareContext'] };
  task: AssistantTask | null;
  submitted: string;
  layout?: 'standard';
  modelId?: string;
  projectId: string;
  executionTarget?: TaskExecutionTarget;
  skillsDirty: boolean;
  skills: AssistantSkillChoice[];
  attemptScope: string;
  matches: () => boolean;
}
