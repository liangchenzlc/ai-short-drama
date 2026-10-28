import type { AssistantMentionReference, AssistantTask, TaskExecutionTarget } from '@host/types/assistant/api';
import type { AgentContextController } from '@host/types/assistant/attachments/AgentContextComposer';
import type { AssistantSkillChoice } from '@host/types/assistant/skills/AssistantSkillPicker';
import type { Ref, RefObject } from 'react';
export interface AssistantComposerToolsController {
  openSkills: () => void;
}
export interface AssistantComposerToolsProps {
  ref?: Ref<AssistantComposerToolsController>;
  layout?: 'standard';
  task: AssistantTask | null;
  executionTarget?: TaskExecutionTarget;
  toolsDisabled: boolean;
  contextBlockReason: string;
  inputContext: RefObject<AgentContextController | null>;
  mentionReferences?: readonly AssistantMentionReference[];
  addMaterials: (selected: readonly AssistantMentionReference[]) => Promise<boolean>;
  setActionError: (message: string) => void;
  selectedSkills: AssistantSkillChoice[];
  changeSkills: (skills: AssistantSkillChoice[]) => void;
}
