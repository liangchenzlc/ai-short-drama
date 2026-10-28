import type { GenerationSummary } from '@host/types/generations/api';

export type BatchScene = 'asset_image' | 'shot_image' | 'shot_video';

export interface BatchScope {
  library: 'project';
  project_id?: string;
  asset_kind?: 'character' | 'scene' | 'prop';
  search?: string;
}

export interface BatchRequest {
  scene: BatchScene;
  scope: BatchScope;
  config_id: string;
  source_ids?: string[];
  mode: 'missing' | 'regenerate';
  count: number;
  asset_parameters?: { aspect?: string; resolution?: string };
}

export interface BatchPreview {
  preflight_hash: string;
  task_count: number;
  output_count: number;
  concurrency: number;
  items: {
    source_id: string;
    name: string;
    state: string;
    reason: string;
    task_id: string | null;
    parameters?: Record<string, unknown>;
  }[];
}

export interface Batch {
  id: string;
  scene: BatchScene;
  config_id: string;
  scope: BatchRequest;
  status: string;
  can_control?: boolean;
  can_cancel?: boolean;
  initiated_by?: string | null;
  counts: Record<string, number>;
  total: number;
  created_at: string;
}

export interface BatchItem {
  id: string;
  source_id: string;
  name: string;
  task_id: string;
  status: string;
  error: { message: string } | null;
  task: GenerationSummary;
}

export interface BatchDetail extends Batch {
  items: BatchItem[];
  offset: number;
  limit: number;
}
