export type AgentArtifactKind = 'novel_proposal' | 'text_proposal' | 'script_candidate' | 'asset_patch' | 'shot_patch' | 'extraction_candidate' | 'storyboard_candidate' | 'image_candidate' | 'video_candidate';
export type AgentArtifactStatus = 'ready' | 'applied' | 'rejected' | 'archived';
export interface AgentArtifactSource {
  episode_id: string; content_version: string; storyboard_version: string; episode_row_version: string;
  target_kind: 'episode' | 'asset' | 'shot'; target_id: string | null; target_row_version: string | null;
  model_name: string | null; generated_at: string | null;
}
export interface AgentArtifact {
  id: string; project_id: string; episode_id: string; kind: AgentArtifactKind; status: AgentArtifactStatus; row_version: string;
  preview: string; source_snapshot: AgentArtifactSource;
  script_id: string | null; parent_script_id: string | null; generation_task_id: string | null;
  media_asset_id: string | null; media_id: string | null; target_asset_id: string | null; target_shot_id: string | null;
  created_by: string; created_at: string; updated_at: string; applied_by: string | null; applied_at: string | null; apply_receipt: Record<string, unknown> | null;
}
export interface AgentArtifactDetail extends AgentArtifact { content: string | null; content_origin: 'snapshot' | 'current_script_legacy' | null; patch: Record<string, unknown> | null; diff: { field: string; before: unknown; after: unknown }[] }
export interface AgentArtifactPage { items: AgentArtifact[]; total: number; offset: number; limit: number }
export interface AgentArtifactAdopt { row_version: string; content_version: string; storyboard_version?: string; target_row_version?: string; confirm_shared?: boolean; native_review?: Record<string, unknown> }
/** A private UI intent; never part of shared artifact API data. */
export interface AgentArtifactOpenRequest { id: string; runId?: string; conversationId?: string; nonce: number; scope?: import('./agents').ConversationScope }
