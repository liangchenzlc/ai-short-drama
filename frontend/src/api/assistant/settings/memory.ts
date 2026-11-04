import { http } from '@host/api/http';
import type { MemoryItem, MemoryListParams, MemoryPage, MemoryPatch } from '@host/types/assistant/settings/memory';

const path = '/assistant/memory';
export const memoryApi = {
  async list(params: MemoryListParams) {
    return (await http.get<MemoryPage>(path, { params })).data;
  },
  async get(id: string) {
    return (await http.get<MemoryItem>(`${path}/${id}`)).data;
  },
  async patch(id: string, body: MemoryPatch) {
    return (await http.patch<MemoryItem>(`${path}/${id}`, body)).data;
  },
  async remove(id: string, content_version: string) {
    return (await http.delete<MemoryItem>(`${path}/${id}`, { data: { content_version } })).data;
  },
};
