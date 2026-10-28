import type {
  AssistantControlInput,
  AssistantDeletionInput,
  AssistantSubjectReference,
  AssistantTaskSendInput,
  TaskExecutionTarget,
  TaskMediaLimits,
} from '@host/types/assistant/api';

export interface AssistantDraft {
  version: 1;
  text: string;
  modelId?: string;
  skills: { id: string; content_version: string; name?: string }[];
  skillsDirty?: boolean;
  taskId?: string;
  newTask?: boolean;
  executionTarget?: TaskExecutionTarget | null;
  memoryProjectIds?: string[];
  mediaLimits?: TaskMediaLimits;
  settingsDirty?: boolean;
  materials?: (AssistantSubjectReference & { name: string })[];
  materialsInvalid?: boolean;
}

export type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>;

export interface AssistantPendingSend {
  version: 1;
  body: AssistantTaskSendInput;
  key: string;
  draft: string;
}

export type PendingStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export type AssistantPendingAction = {
  version: 1;
  targetId: string;
  key: string;
} & ({ kind: 'control'; body: AssistantControlInput } | { kind: 'delete'; body: AssistantDeletionInput });
