import { http } from '@host/api/http';
import type { AgentModel, AgentModelPage } from '@host/types/assistant/engine';
const root = '/agent';
export const assistantModelsApi = {
  async updateModelInputs(
    id: string,
    body: {
      row_version: string;
      image: boolean;
      audio: boolean;
      context_window_tokens?: number | null;
    },
  ) {
    return (await http.patch<AgentModel>(`${root}/models/${encodeURIComponent(id)}/inputs`, body)).data;
  },
  async models(signal?: AbortSignal) {
    return (await http.get<AgentModelPage>(`${root}/models`, { signal })).data;
  },
  async verifyModel(id: string, row_version: string) {
    return (
      await http.post<AgentModel>(
        `${root}/models/${encodeURIComponent(id)}/verify`,
        { row_version },
        { timeout: 120_000 },
      )
    ).data;
  },
};
