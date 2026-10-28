import { http } from '@host/api/http';
import type {
  AgentArtifactAdopt,
  AgentArtifactDetail,
  AgentArtifactKind,
  AgentArtifactPage,
  AgentArtifactStatus,
} from '@host/types/assistant/artifacts';
import type { ConversationScope } from '@host/types/assistant/engine';
export function agentArtifactsApi(projectId: string, scope?: ConversationScope) {
  const root = `/projects/${encodeURIComponent(projectId)}/agent-artifacts`;
  return {
    async list(
      params: {
        offset: number;
        kind?: AgentArtifactKind;
        status?: AgentArtifactStatus;
      },
      signal?: AbortSignal,
    ) {
      return (
        await http.get<AgentArtifactPage>(root, {
          params: { ...params, ...scope, limit: 20 },
          signal,
        })
      ).data;
    },
    async detail(id: string, signal?: AbortSignal) {
      return (await http.get<AgentArtifactDetail>(`${root}/${encodeURIComponent(id)}`, { params: scope, signal })).data;
    },
    async adopt(id: string, body: AgentArtifactAdopt) {
      return (await http.post<AgentArtifactDetail>(`${root}/${encodeURIComponent(id)}/adopt`, body, { params: scope }))
        .data;
    },
  };
}
