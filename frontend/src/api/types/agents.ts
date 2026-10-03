export interface AgentAvailability {
  enabled: boolean;
  schema_ready: boolean;
}

export type AgentRunStatus = 'queued' | 'running' | 'waiting_generation' | 'waiting_review' | 'succeeded' | 'failed' | 'cancelled';

export interface AgentConversation {
  id: string;
  project_id: string;
  episode_id: string;
  title: string;
  row_version: number;
  archived: boolean;
  created_at: string;
  updated_at: string;
  last_run_status: AgentRunStatus | null;
}

export interface AgentConversationPage {
  items: AgentConversation[];
  total: number;
  offset: number;
  limit: number;
}

export interface CreateAgentConversation {
  project_id: string;
  episode_id: string;
  title?: string;
}

export interface UpdateAgentConversation {
  row_version: number;
  title?: string;
  archived?: boolean;
}

export interface AgentModel {
  id: string; name: string; model_key: string; row_version: number; protocol: string | null;
  tool_calling: boolean; tool_result_continuation: boolean; streaming: 'verified' | 'not_tested'; verified: boolean; preferred: boolean;
}
export interface AgentModelPage { items: AgentModel[]; preferred_id: string | null }
export type AgentMessageMode = 'discuss' | 'generate';
export interface AgentMessage {
  id: string; seq: number; role: string; content: string;
  references: Record<string, unknown>[]; artifacts: Record<string, unknown>[]; created_at: string;
}
export interface AgentMessagePage { items: AgentMessage[]; total: number; offset: number; limit: number }
export type AgentTaskKind = 'novel' | 'script' | 'extract' | 'storyboard' | 'asset_patch' | 'shot_patch' | 'image' | 'video';
export interface AgentReviewStep {
  id: string; kind: AgentTaskKind; target_id: string | null; target_kind?: 'episode' | 'asset' | 'shot'; target_label?: string | null; instructions: string; count: number;
  model_config_id: string | null; model_name: string | null; source: Record<string, unknown>; parameters: Record<string, unknown>;
}
export interface AgentReview {
  tool_call_id: string; review_version: number; review_hash: string;
  title: string; summary: string; steps: AgentReviewStep[];
}
export interface AgentRun {
  id: string; conversation_id: string; status: AgentRunStatus; phase: string; row_version: number;
  mode: 'discuss' | 'single' | 'workflow'; model_config_id: string; model_name: string; error: { code: string } | null;
  usage: Record<string, unknown>; budget: Record<string, unknown>; review: AgentReview | null;
  awaiting_artifact_ids: string[];
  created_at: string; updated_at: string; finished_at: string | null;
}
export interface AgentRunPage { items: AgentRun[]; total: number; offset: number; limit: number }
export interface AgentTaskSpec { kind: 'image' | 'video'; target_id: string; instructions: string; model_config_id: string; count: number; parameters: Record<string, unknown> }
export interface AgentSendInput { content: string; mode: AgentMessageMode; model_config_id?: string; task?: AgentTaskSpec }
export interface AgentSendResult { message: AgentMessage; run: AgentRun; cursor: number }
export interface AgentEvent {
  seq: number; event_type: string; run_id: string | null; payload: Record<string, unknown>; created_at: string;
}
