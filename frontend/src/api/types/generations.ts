import type { ServiceTypeDto } from './ai-model-configs';
export type GenerationKind = ServiceTypeDto;
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface GenerationSource { scene: 'shot_image'; shot_id: string; layout: 'single' | 'four' | 'five' | 'nine' }
interface CreateBase { config_id?: string }
export interface TextGenerationRequest extends CreateBase {
  input: { messages: { role: 'system' | 'user' | 'assistant'; content: string }[] };
  parameters: { temperature?: number; max_output_tokens?: number };
}
export interface ImageGenerationRequest extends CreateBase {
  source?: GenerationSource;
  input: { prompt: string; reference_media_ids: string[] };
  parameters: { aspect?: string; resolution?: string; count: number };
}
export interface VideoGenerationRequest extends CreateBase {
  input: { prompt: string; first_frame_media_id?: string; last_frame_media_id?: string };
  parameters: { aspect?: string; resolution?: string; duration_ms?: number };
}
export interface GenerationReceipt { generation_id: string; service_type: GenerationKind; status: TaskStatus }
export interface SafeTaskError { code: string; message: string; http_status?: number }
export interface GenerationSummary extends GenerationReceipt {
  config?: { id: string; name: string; model_key: string; provider: string } | null;
  created_at: string;
  updated_at?: string | null;
  next_action?: 'submit' | 'poll' | 'save' | null;
  source?: GenerationSource | null;
  error?: SafeTaskError | null;
  can_cancel: boolean;
  can_retry: boolean;
  can_resume: boolean;
}
export interface MediaAsset {
  asset_id: string; record_id: string; generation_id: string; media_id: string;
  media_type: 'image' | 'video'; name: string; row_version: string; url: string | null;
  source?: GenerationSource | null;
  width?: number | null; height?: number | null; duration_ms?: number | null;
  byte_size?: string | null; created_at?: string; updated_at?: string | null;
}
export interface GenerationDetail extends GenerationSummary {
  input: Record<string, unknown>; parameters: Record<string, unknown>;
  started_at?: string | null; finished_at?: string | null;
  result: { text: { record_id: string; content: string; finish_reason: string | null } | null; assets: MediaAsset[]; partial: boolean };
}
export interface GenerationRecord {
  record_id: string; call_no: number; status: string; adapter?: string | null;
  provider_task_id?: string | null; created_at?: string; started_at?: string | null; finished_at?: string | null;
  error?: SafeTaskError | null; usage?: Record<string, unknown> | null; finish_reason?: string | null;
}
export interface GenerationFilters {
  service_type: GenerationKind; status?: string; config_id?: string; source_scene?: string;
  source_id?: string; created_after?: string; created_before?: string; offset: number; limit: number;
}
export interface AssetFilters { media_type: 'image' | 'video'; name?: string; source_scene?: string; source_id?: string; created_after?: string; created_before?: string; offset: number; limit: number }
export interface ApplyAssetRequest {
  target: { type: 'shot_image' | 'shot_video' | 'asset_image'; id: string };
  expected_media_id: string | null;
  parameters?: { layout?: GenerationSource['layout']; aspect?: string; resolution?: string; duration?: number };
  confirm_shared?: boolean;
}
