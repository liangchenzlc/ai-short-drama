import { http } from '@host/api/http';
import type { McpAccessToken, McpTokenCreate, McpTokenCreated, McpTokenOptions } from '@host/types/assistant/settings/mcp';
const root = '/account/mcp-tokens';
export const mcpTokensApi = {
  async list(signal?: AbortSignal, createKey?: string) {
    return (await http.get<McpAccessToken[]>(root, { signal, params: createKey ? { create_key: createKey } : undefined })).data;
  },
  async options(signal?: AbortSignal) { return (await http.get<McpTokenOptions>(`${root}/options`, { signal })).data; },
  async create(body: McpTokenCreate, key: string) {
    return (await http.post<McpTokenCreated>(root, body, { headers: { 'Idempotency-Key': key } })).data;
  },
  async remove(id: string) { await http.delete(`${root}/${encodeURIComponent(id)}`); },
};
