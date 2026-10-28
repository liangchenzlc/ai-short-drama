import { http } from '@host/api/http';
import type { AssistantScriptProposal } from '@host/types/assistant/api';
export const assistantScriptApi = {
  async preview(operationId: string, signal?: AbortSignal) {
    return (
      await http.get<AssistantScriptProposal>(
        `/assistant/operations/${encodeURIComponent(operationId)}/script-proposal`,
        { signal },
      )
    ).data;
  },
  async apply(operationId: string, contentVersion: string) {
    return (
      await http.post<{ project_id: string; content_version: string }>(
        `/assistant/operations/${encodeURIComponent(operationId)}/apply-script`,
        { content_version: contentVersion },
      )
    ).data;
  },
};
