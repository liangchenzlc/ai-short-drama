export type ImageTargetKind = 'asset' | 'shot';

export interface ImageMedia {
  media_id: string;
  url: string | null;
  width: number | null;
  height: number | null;
}

export interface ImageGroupItem {
  id: string;
  output_index: number;
  media_id: string | null;
  url: string | null;
  width: number | null;
  height: number | null;
  item_version: string;
  status: 'ready' | 'generating' | 'failed';
  error: string | null;
  prompt: string | null;
  history: ImageMedia[];
}

export interface ImageGroup {
  id: string;
  kind: string;
  status: 'generating' | 'completed' | 'partial' | 'failed';
  target_kind: ImageTargetKind;
  target_id: string;
  target_version: string;
  is_current: boolean;
  expected_count: number;
  primary_index: number | null;
  primary_version: string;
  notice: string | null;
  task_id: string | null;
  created_at: string;
  items: ImageGroupItem[];
}

export type ImageTool =
  | 'crop'
  | 'annotation'
  | 'resize'
  | 'split'
  | 'replace'
  | 'mask'
  | 'emotion'
  | 'texture'
  | 'angle'
  | 'lighting'
  | 'reverse_prompt';

export interface ImageToolParameters {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  output_width?: number;
  output_height?: number;
  interpolation?: 'nearest' | 'bilinear' | 'high';
  rows?: number;
  columns?: number;
  instructions?: string;
  preset?: string;
  horizontal_angle?: number;
  pitch_angle?: number;
  camera_distance?: number;
  wide_angle?: boolean;
  azimuth?: number;
  elevation?: number;
  brightness?: number;
  light_color?: string;
  rim_light?: boolean;
}

export interface ImageOperationRequest {
  item_id: string;
  expected_item_version: string;
  operation_type: ImageTool;
  parameters: ImageToolParameters;
  restore_media_id?: string;
}

export interface ImageOperation {
  id: string;
  item_id: string;
  operation_type: ImageTool;
  status: 'pending' | 'prepared' | 'running' | 'completed' | 'conflict' | 'failed';
  task_id: string | null;
  result_media_id: string | null;
  result_group_id: string | null;
  mask_media_id: string | null;
}
