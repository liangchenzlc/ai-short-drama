import { http } from '@host/api/http';
import type { McpAccessToken, McpTokenCreate, McpTokenCreated, McpTokenOptions } from '@host/types/assistant/settings/editor';
const root = '/account/mcp-tokens';
export const mcpTokensApi = {
  async list(signal?: AbortSignal) { return (await http.get<McpAccessToken[]>(root, { signal })).data; },
  async options(signal?: AbortSignal) { return (await http.get<McpTokenOptions>(`${root}/options`, { signal })).data; },
  async create(body: McpTokenCreate) { return (await http.post<McpTokenCreated>(root, body)).data; },
  async remove(id: string) { await http.delete(`${root}/${encodeURIComponent(id)}`); },
};
