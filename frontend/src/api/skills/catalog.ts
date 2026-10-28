import { http } from '@host/api/http';
import type {
  AgentSkill,
  AgentSkillContent,
  AgentSkillDetail,
  AgentSkillFilters,
  AgentSkillImportPreview,
  AgentSkillResourceContent,
} from '@host/types/assistant/engine';
import type { Page } from '@host/types/generations/api';
const root = '/agent';
export const skillsApi = {
  async skills(offset = 0, signal?: AbortSignal, filters?: AgentSkillFilters) {
    return (
      await http.get<Page<AgentSkill>>(`${root}/skills`, {
        params: { offset, limit: 50, ...filters },
        signal,
      })
    ).data;
  },
  async skillCount(source: 'builtin' | 'personal', signal?: AbortSignal) {
    return (
      await http.get<Page<AgentSkill>>(`${root}/skills`, {
        params: { source, limit: 1 },
        signal,
      })
    ).data.total;
  },
  async skillRequest(key: string) {
    return (await http.get<AgentSkillDetail>(`${root}/skills/requests/${encodeURIComponent(key)}`)).data;
  },
  async skill(id: string, signal?: AbortSignal) {
    return (await http.get<AgentSkillDetail>(`${root}/skills/${encodeURIComponent(id)}`, { signal })).data;
  },
  async createSkill(body: AgentSkillContent, key: string) {
    return (
      await http.post<AgentSkillDetail>(`${root}/skills`, body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async previewSkillImport(file: File, signal?: AbortSignal) {
    const body = new FormData();
    body.append('file', file);
    return (await http.post<AgentSkillImportPreview>(`${root}/skills/imports/preview`, body, { signal })).data;
  },
  async copySkill(id: string, content_version: string, key: string, name?: string) {
    return (
      await http.post<AgentSkillDetail>(
        `${root}/skills/${encodeURIComponent(id)}/copy`,
        { content_version, ...(name ? { name } : {}) },
        { headers: { 'Idempotency-Key': key } },
      )
    ).data;
  },
  async updateSkillSettings(id: string, body: { row_version: string; enabled: boolean }) {
    return (await http.patch<AgentSkillDetail>(`${root}/skills/${encodeURIComponent(id)}/settings`, body)).data;
  },
  async skillResource(id: string, version: string, path: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<AgentSkillResourceContent>(
        `${root}/skills/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}/resources`,
        { params: { path, offset, limit: 6000 }, signal },
      )
    ).data;
  },
  async exportSkill(id: string, content_version: string) {
    return (
      await http.get<Blob>(`${root}/skills/${encodeURIComponent(id)}/export`, {
        params: { content_version },
        responseType: 'blob',
      })
    ).data;
  },
  async uploadSkill(file: File) {
    const body = new FormData();
    body.append('file', file);
    return (await http.post<AgentSkillDetail>(`${root}/skills/uploads`, body)).data;
  },
  async updateSkill(id: string, body: { row_version: string } & Partial<AgentSkillContent>) {
    return (await http.patch<AgentSkillDetail>(`${root}/skills/${encodeURIComponent(id)}`, body)).data;
  },
  async deleteSkill(id: string, row_version: string) {
    await http.delete(`${root}/skills/${encodeURIComponent(id)}`, {
      data: { row_version },
    });
  },
};
