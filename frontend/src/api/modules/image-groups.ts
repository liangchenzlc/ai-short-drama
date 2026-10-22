import { http } from '../http';
import type { Page } from '../types/generations';

export type ImageTargetKind = 'asset' | 'shot';
export interface ImageMedia { media_id: string; url: string | null; width: number | null; height: number | null }
export interface ImageGroupItem {
  id: string; output_index: number; media_id: string | null; url: string | null;
  width: number | null; height: number | null; item_version: string;
  status: 'ready' | 'generating' | 'failed'; error: string | null;
  prompt: string | null; history: ImageMedia[];
}
export interface ImageGroup {
  id: string; kind: string; status: 'generating' | 'completed' | 'partial' | 'failed';
  target_kind: ImageTargetKind; target_id: string; target_version: string; is_current: boolean;
  expected_count: number; primary_index: number | null; primary_version: string;
  notice: string | null; task_id: string | null; created_at: string; items: ImageGroupItem[];
}
export type ImageTool = 'crop' | 'annotation' | 'resize' | 'split' | 'replace' | 'mask' | 'emotion' | 'texture' | 'angle' | 'lighting' | 'reverse_prompt';
export interface ImageToolParameters {
  x?: number; y?: number; width?: number; height?: number;
  output_width?: number; output_height?: number; interpolation?: 'nearest' | 'bilinear' | 'high';
  rows?: number; columns?: number; instructions?: string; preset?: string;
  horizontal_angle?: number; pitch_angle?: number; camera_distance?: number; wide_angle?: boolean;
  azimuth?: number; elevation?: number; brightness?: number; light_color?: string; rim_light?: boolean;
}
export interface ImageOperationRequest {
  item_id: string; expected_item_version: string; operation_type: ImageTool;
  parameters: ImageToolParameters; restore_media_id?: string;
}
export interface ImageOperation {
  id: string; item_id: string; operation_type: ImageTool;
  status: 'pending' | 'prepared' | 'running' | 'completed' | 'conflict' | 'failed';
  task_id: string | null; result_media_id: string | null; result_group_id: string | null; mask_media_id: string | null;
}
export const imageGroups = {
  async current(kind: ImageTargetKind, id: string, signal?: AbortSignal) { return (await http.get<ImageGroup | null>(`/image-groups/current/${kind}/${id}`, { signal })).data; },
  async history(kind: ImageTargetKind, id: string, offset = 0, signal?: AbortSignal) { return (await http.get<Page<ImageGroup>>(`/image-groups/history/${kind}/${id}`, { params: { offset, limit: 20 }, signal })).data; },
  async detail(id: string, signal?: AbortSignal) { return (await http.get<ImageGroup>(`/image-groups/${id}`, { signal })).data; },
  async primary(group: ImageGroup, item: ImageGroupItem) { return (await http.put<ImageGroup>(`/image-groups/${group.id}/primary`, { item_id: item.id, expected_item_version: item.item_version, expected_primary_version: group.primary_version, expected_target_version: group.target_version })).data; },
  async content(id: string, signal?: AbortSignal) { return (await http.get<Blob>(`/image-groups/media/${id}/content`, { responseType: 'blob', signal, timeout: 60_000 })).data; },
  async create(body: ImageOperationRequest, key: string) { return (await http.post<ImageOperation>('/image-operations', body, { headers: { 'Idempotency-Key': key } })).data; },
  async operation(id: string, signal?: AbortSignal) { return (await http.get<ImageOperation>(`/image-operations/${id}`, { signal })).data; },
  async execute(id: string, file?: Blob) {
    const form = new FormData(); if (file) form.append('file', file, 'image.png');
    return (await http.post<ImageOperation>(`/image-operations/${id}/execute`, form, { timeout: 120_000 })).data;
  },
};
