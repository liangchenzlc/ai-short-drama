import { http } from '@host/api/http';
import type { StorySettings, WritingResponse, WritingTransport } from '@host/types/creation/writing/documents';
export function storySettingsApi(projectId: string) {
  const path = `/projects/${encodeURIComponent(projectId)}/story-settings`;
  return {
    async get() {
      return (await http.get<StorySettings>(path)).data;
    },
    async save(body: StorySettings) {
      return (await http.put<StorySettings>(path, body)).data;
    },
  };
}

export function projectWritingApi(projectId: string): WritingTransport {
  const root = `/projects/${encodeURIComponent(projectId)}`;
  return {
    async get() {
      return (await http.get<WritingResponse>(`${root}/writing`)).data;
    },
    async script(body) {
      return (await http.put(`${root}/script`, body)).data;
    },
  };
}
