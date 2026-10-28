import type { ServiceTypeDto } from '@host/types/ai-config/api';
export type GenerationKind = ServiceTypeDto;
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export type ImageLayout = 'single' | 'four' | 'five' | 'nine';
interface DraftSourceReference {
  draft_id?: string;
  draft_version?: string;
}
export type GenerationSource = (
  | { scene: 'image_operation'; operation_id: string }
  | {
      scene: 'character_voice_design';
      project_id: string;
      asset_id: string;
      voice_prompt: string;
      preview_text: string;
    }
  | { scene: 'dialogue_extract'; project_id: string }
  | {
      scene: 'dialogue_audio';
      project_id: string;
      row_version: string;
      line_id: string;
    }
  | {
      scene: 'shot_video';
      shot_id: string;
      row_version: string;
      context_hash: string;
      reference_media_id: string;
    }
  | {
      scene: 'shot_image';
      shot_id: string;
      layout: ImageLayout;
      context_mode?: 'saved';
      row_version?: string;
      context_hash?: string;
    }
  | {
      scene: 'asset_image';
      asset_id: string;
      row_version: string;
      project_id?: string;
    }
  | {
      scene: 'script_rewrite';
      project_id: string;
      content_version: string;
      script_id: string;
      start: number;
      end: number;
      selected_text: string;
    }
  | {
      scene: 'script_shots' | 'script_assets';
      project_id: string;
      script_id: string;
      content_version: string;
    }
) &
  DraftSourceReference;
interface CreateBase {
  config_id?: string;
}
export interface TextGenerationRequest extends CreateBase {
  input?: {
    messages: { role: 'system' | 'user' | 'assistant'; content: string }[];
  };
  source?: Extract<
    GenerationSource,
    {
      scene: 'script_rewrite' | 'script_shots' | 'script_assets' | 'dialogue_extract' | 'image_operation';
    }
  >;
  extraction?: { kinds: ('character' | 'scene' | 'prop')[] };
  storyboard?: { average_shot_duration_ms: number };
  instructions?: string;
  parameters: { temperature?: number; max_output_tokens?: number };
}
export interface ImageGenerationRequest extends CreateBase {
  source?: Extract<GenerationSource, { scene: 'shot_image' | 'asset_image' | 'image_operation' }>;
  input: { prompt: string; reference_media_ids: string[] };
  parameters: { aspect?: string; resolution?: string; count: number };
}
export interface VideoGenerationRequest extends CreateBase {
  input?: {
    prompt: string;
    reference_media_ids?: string[];
    first_frame_media_id?: string;
    last_frame_media_id?: string;
  };
  source?: Extract<GenerationSource, { scene: 'shot_video' }>;
  parameters: { aspect?: string; resolution?: string; duration_ms?: number };
}
export interface GenerationReceipt {
  generation_id: string;
  service_type: GenerationKind;
  status: TaskStatus;
  image_group_id?: string | null;
}
export interface AudioGenerationRequest extends CreateBase {
  input?: { text: string };
  parameters?: { voice: string };
  source?: Extract<GenerationSource, { scene: 'dialogue_audio' }>;
}
export interface SafeTaskError {
  code: string;
  message: string;
  http_status?: number;
}
export interface GenerationSummary extends GenerationReceipt {
  batch_id?: string | null;
  display_context?: {
    project?: string | null;
    episode?: string | null;
    subject: string;
    scope: string;
  };
  config?: {
    id: string;
    name: string;
    model_key: string;
    provider: string;
  } | null;
  created_at: string;
  updated_at?: string | null;
  next_action?: 'submit' | 'poll' | 'save' | null;
  source?: (GenerationSource & { project_id?: string }) | null;
  error?: SafeTaskError | null;
  can_cancel: boolean;
  can_retry: boolean;
  can_resume: boolean;
}
export interface MediaAsset {
  project_id?: string | null;
  scope_user_id?: string | null;
  native_quality?: {
    technical_pass: boolean;
    issue: string | null;
    voice_fidelity: string;
  } | null;
  asset_id: string;
  record_id: string;
  generation_id: string;
  media_id: string;
  media_type: 'image' | 'video' | 'audio';
  name: string;
  row_version: string;
  url: string | null;
  source?: GenerationSource | null;
  width?: number | null;
  height?: number | null;
  duration_ms?: number | null;
  byte_size?: string | null;
  created_at?: string;
  updated_at?: string | null;
}
export interface GenerationDetail extends GenerationSummary {
  input: Record<string, unknown>;
  parameters: Record<string, unknown>;
  resolved_parameters?: Record<string, unknown>;
  started_at?: string | null;
  finished_at?: string | null;
  result: {
    text: {
      record_id: string;
      content: string;
      finish_reason: string | null;
    } | null;
    assets: MediaAsset[];
    partial: boolean;
    warnings?: {
      code: string;
      message: string;
      output_index?: number | null;
    }[];
    business?:
      | {
          kind: 'script_rewrite';
          schema_version: 1;
          document_id: string;
          content_version: string;
        }
      | {
          kind: 'script_shots';
          schema_version: 1;
          shots: {
            title?: string;
            source_excerpt?: string;
            story_beat?: string;
            script: string;
            duration_ms?: number;
            asset_ids: string[];
          }[];
          applied: {
            mode: 'append' | 'replace';
            shot_ids: string[];
            applied_at: string;
            storyboard_version: string;
          } | null;
        }
      | {
          kind: 'script_assets';
          schema_version: 1;
          items: {
            candidate_id: string;
            applied: { asset_id: string } | null;
          }[];
        }
      | null;
  };
  source_snapshot?: Record<string, unknown> | null;
  effective_prompt?: string | null;
}
export interface GenerationRecord {
  record_id: string;
  call_no: number;
  status: string;
  adapter?: string | null;
  provider_task_id?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: SafeTaskError | null;
  usage?: Record<string, unknown> | null;
  finish_reason?: string | null;
}
export interface GenerationFilters {
  service_type: GenerationKind;
  status?: string;
  config_id?: string;
  source_scene?: string;
  project_id?: string;
  source_id?: string;
  created_after?: string;
  created_before?: string;
  offset: number;
  limit: number;
}
export interface AssetFilters {
  media_type?: 'image' | 'video' | 'audio';
  workspace_mode?: 'standard' | 'infinite_canvas';
  resource_scope?: 'all' | 'personal' | 'project';
  project_id?: string;
  name?: string;
  source_scene?: string;
  source_id?: string;
  created_after?: string;
  created_before?: string;
  offset: number;
  limit: number;
}
export interface ApplyAssetRequest {
  target: { type: 'shot_image' | 'shot_video' | 'asset_image'; id: string };
  expected_media_id: string | null;
  expected_row_version?: string;
  expected_context_hash?: string;
  acknowledge_stale_source?: boolean;
  parameters?: {
    layout?: ImageLayout;
    aspect?: string;
    resolution?: string;
    duration?: number;
  };
}
