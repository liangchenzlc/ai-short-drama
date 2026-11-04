export interface McpAccessToken {
  id: string;
  name: string;
  token_hint: string;
  create_key: string;
  expires_at: string | null;
  allowed_ips: string[];
  tool_names: string[];
  created_at: string;
  deleted_at: string | null;
}

export interface McpTokenCreated extends McpAccessToken { secret: string | null }

export interface McpTokenCreate {
  name: string;
  workbench_enabled: boolean;
  media_generation_enabled: boolean;
  expires_at?: string | null;
  allowed_ips: string[];
}

export interface McpTokenOptions {
  connection_url: string;
  transport: 'streamable-http';
  tools: { name: string; description: string; modes: string[]; mutating: boolean }[];
  capability_groups: { read: string[]; workbench: string[]; media: string[] };
}

export type McpTokenExpiry = '30' | '90' | '180' | 'never';

export interface McpTokenDraft {
  name: string;
  workbench_enabled: boolean;
  media_generation_enabled: boolean;
  expiry: McpTokenExpiry;
  ips: string;
}

export interface McpCreationRequest { key: string; body: McpTokenCreate }

export type McpTokenDialog =
  | { kind: 'create' }
  | { kind: 'details' | 'revoke' | 'lost'; token: McpAccessToken }
  | { kind: 'created'; secret: string }
  | null;

export interface McpTokenProblem {
  text: string;
  recovery?: 'create' | 'retry-create' | 'revoke';
}

export interface McpTokenDialogsProps {
  active: boolean;
  dialog: McpTokenDialog;
  draft: McpTokenDraft;
  options: McpTokenOptions | null;
  busy: boolean;
  pending: McpCreationRequest | null;
  problem: McpTokenProblem | null;
  close: () => void;
  edit: (patch: Partial<McpTokenDraft>) => void;
  create: () => Promise<void>;
  recover: () => Promise<void>;
  remove: () => Promise<void>;
  openCreate: () => void;
  openToken: (kind: 'details' | 'revoke', token: McpAccessToken) => void;
}
