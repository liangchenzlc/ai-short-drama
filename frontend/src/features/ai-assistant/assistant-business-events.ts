export const ASSISTANT_BUSINESS_CHANGED = 'assistant-business-changed';
export interface AssistantBusinessChange { projectId: string; kinds: string[] }
export function notifyAssistantBusinessChange(projectId: string, kinds: string[]) {
  window.dispatchEvent(new CustomEvent<AssistantBusinessChange>(ASSISTANT_BUSINESS_CHANGED, { detail: { projectId, kinds } }));
}
