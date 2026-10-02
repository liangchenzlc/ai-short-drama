import { http } from '../http';
import type { GenerationSummary } from '../types/generations';

export type BatchScene = 'asset_image' | 'shot_image' | 'shot_video';
export interface BatchScope { library: 'global' | 'project' | 'episode'; project_id?: string; episode_id?: string; asset_kind?: 'character' | 'scene' | 'prop'; search?: string }
export interface BatchRequest { scene: BatchScene; scope: BatchScope; config_id: string; source_ids?: string[]; mode: 'missing' | 'regenerate'; count: number; asset_parameters?: { aspect?: string; resolution?: string } }
export interface BatchPreview { preflight_hash: string; task_count: number; output_count: number; concurrency: number; items: { source_id: string; name: string; state: string; reason: string; task_id: string | null; parameters?: Record<string, unknown> }[] }
export interface Batch { id: string; scene: BatchScene; config_id: string; scope: BatchRequest; status: string; can_control?: boolean; can_cancel?: boolean; initiated_by?: string | null; counts: Record<string, number>; total: number; created_at: string }
export interface BatchItem { id: string; source_id: string; name: string; task_id: string; status: string; error: { message: string } | null; task: GenerationSummary }
export interface BatchDetail extends Batch { items: BatchItem[]; offset: number; limit: number }
const root = '/ai/generation-batches';
export const generationBatches = {
  capabilities: async (signal?: AbortSignal) => (await http.get<{ enabled: boolean }>(`${root}/capabilities`, { signal })).data,
  preflight: async (body: BatchRequest, signal?: AbortSignal) => (await http.post<BatchPreview>(`${root}/preflight`, body, { signal })).data,
  create: async (body: BatchRequest & { preflight_hash: string; accepted_ids: string[] }, key: string) => (await http.post<Batch>(root, body, { headers: { 'Idempotency-Key': key } })).data,
  list: async (offset = 0, signal?: AbortSignal) => (await http.get<{ items: Batch[]; total: number }>(root, { params: { offset, limit: 20 }, signal })).data,
  detail: async (id: string, offset = 0, signal?: AbortSignal) => (await http.get<BatchDetail>(`${root}/${id}`, { params: { offset, limit: 20 }, signal })).data,
  control: async (id: string, action: 'pause' | 'resume' | 'cancel') => (await http.post<Batch>(`${root}/${id}/${action}`)).data,
  retry: async (id: string, item_ids: string[], key: string) => (await http.post<Batch>(`${root}/${id}/retry-failed`, { item_ids }, { headers: { 'Idempotency-Key': key } })).data,
};
