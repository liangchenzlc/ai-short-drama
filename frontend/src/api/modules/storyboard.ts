import { http } from '../http';
import type { Page } from '../types/generations';
import type { WorkflowShot } from '../../features/projects/workflow-contract';

export interface ScriptCandidate { id: string; position: number; state: 'unconfirmed' | 'confirmed'; preview: string; is_editing: boolean; is_confirmed: boolean; generation_id: string | null; created_at: string; updated_at: string }
export interface ScriptDetail extends ScriptCandidate { content: string }
export interface ShotImage { media_id: string; media_asset_id: string | null; url: string; width?: number | null; height?: number | null; layout: string; aspect: string; resolution: string; is_stale: boolean }
export interface ShotRead extends WorkflowShot { position: number; script: string; duration_ms: number; source_excerpt: string; asset_ids: string[]; image: ShotImage | null; deleted_at: string | null }
export interface StoryboardPage { episode_id: string; storyboard_version: string; items: ShotRead[]; total: number; offset: number; limit: number }
export interface ShotMutation { shot: ShotRead; storyboard_version: string }
export interface StoryboardApplyResult { generation_id: string; mode: 'append' | 'replace'; shot_ids: string[]; storyboard_version: string; already_applied: boolean }

export function storyboardApi(projectId: string, episodeId: string) {
  const root = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}`;
  return {
    async scripts(signal?: AbortSignal, offset = 0) { return (await http.get<Page<ScriptCandidate>>(`${root}/scripts`, { params: { offset, limit: 100 }, signal })).data; },
    async script(id: string, signal?: AbortSignal) { return (await http.get<ScriptDetail>(`${root}/scripts/${encodeURIComponent(id)}`, { signal })).data; },
    async shots(signal?: AbortSignal, includeArchived = false, offset = 0) { return (await http.get<StoryboardPage>(`${root}/shots`, { params: { offset, limit: 100, include_archived: includeArchived }, signal })).data; },
    async shot(id: string, signal?: AbortSignal) { return (await http.get<ShotMutation>(`${root}/shots/${encodeURIComponent(id)}`, { signal })).data; },
    async create(storyboardVersion: string, key: string) { return (await http.post<ShotMutation>(`${root}/shots`, { storyboard_version: storyboardVersion, script: '', asset_ids: [], image_settings: { resolution: '2K', aspect: 'inherit', layout: 'single' } }, { headers: { 'Idempotency-Key': key } })).data; },
    async update(id: string, body: { row_version: string; script?: string; duration_ms?: number; asset_ids?: string[]; image_settings?: ShotRead['image_settings'] }) { return (await http.patch<ShotMutation>(`${root}/shots/${encodeURIComponent(id)}`, body)).data; },
    async remove(id: string, rowVersion: string) { await http.delete(`${root}/shots/${encodeURIComponent(id)}`, { headers: { 'If-Match': `"${rowVersion}"` } }); },
    async order(storyboardVersion: string, shotIds: string[]) { return (await http.put<{ storyboard_version: string; ordered_ids: string[] }>(`${root}/shots/order`, { storyboard_version: storyboardVersion, shot_ids: shotIds })).data; },
    async apply(generationId: string, body: { mode: 'append' | 'replace'; content_version: string; storyboard_version: string; confirm_replace: boolean }) { return (await http.post<StoryboardApplyResult>(`${root}/storyboard-results/${encodeURIComponent(generationId)}/apply`, body)).data; },
  };
}
