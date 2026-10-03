import { http } from '../http';
import type { AgentArtifactAdopt, AgentArtifactDetail, AgentArtifactKind, AgentArtifactPage, AgentArtifactStatus } from '../types/agent-artifacts';
export function agentArtifactsApi(projectId: string, episodeId: string) {
  const root = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}/agent-artifacts`;
  return {
    async list(params: { offset: number; kind?: AgentArtifactKind; status?: AgentArtifactStatus }, signal?: AbortSignal) {
      return (await http.get<AgentArtifactPage>(root, { params: { ...params, limit: 20 }, signal })).data;
    },
    async detail(id: string, signal?: AbortSignal) { return (await http.get<AgentArtifactDetail>(`${root}/${encodeURIComponent(id)}`, { signal })).data; },
    async adopt(id: string, body: AgentArtifactAdopt) { return (await http.post<AgentArtifactDetail>(`${root}/${encodeURIComponent(id)}/adopt`, body)).data; },
  };
}
