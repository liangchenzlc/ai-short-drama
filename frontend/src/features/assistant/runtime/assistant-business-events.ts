import type { AssistantBusinessChange } from '@host/types/assistant/runtime/assistant-business-events';
export const ASSISTANT_BUSINESS_CHANGED = 'assistant-business-changed';
export function notifyAssistantBusinessChange(projectId: string, kinds: string[]) {
  window.dispatchEvent(
    new CustomEvent<AssistantBusinessChange>(ASSISTANT_BUSINESS_CHANGED, {
      detail: { projectId, kinds },
    }),
  );
}
