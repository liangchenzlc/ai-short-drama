import type { AssistantSettings, AssistantSettingsPatch } from '@host/types/assistant/api';

export interface AssistantSettingsState {
  saved: AssistantSettings | null;
  draft: AssistantSettingsPatch | null;
  revision: number;
}

export interface McpAccessToken {
  id: string;
  name: string;
  token_hint: string;
  expires_at: string | null;
  allowed_ips: string[];
  project_ids: string[];
  tool_names: string[];
  created_at: string;
}

export interface McpTokenCreated extends McpAccessToken { secret: string }
export interface McpTokenCreate {
  name: string;
  expires_at: string | null;
  allowed_ips: string[];
  project_ids: string[];
  tool_names: string[];
}
export interface McpTokenOptions {
  projects: { id: string; name: string }[];
  tools: { name: string; description: string; modes: string[]; mutating: boolean }[];
}
