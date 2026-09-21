import { http } from '../http';
import type { Page } from '../types/generations';

export type AssetKind = 'character' | 'scene' | 'prop';
export interface AssetImage { media_id: string; url: string; width?: number | null; height?: number | null }
export interface AssetRead { id: string; kind: AssetKind; name: string; label: string; description: string; prompt: string; tags: string[]; scene_time: string; state: 'unconfirmed' | 'confirmed'; row_version: string; media_id: string | null; image: AssetImage | null; reference_count: number; created_at: string | null; updated_at: string | null }
export interface LibraryAssetRead extends AssetRead { link_id: string | null; position: number | null }
export interface AssetCandidate { id: string; media_id: string; url: string; width?: number | null; height?: number | null; created_at: string }
export interface AssetDraft { kind: AssetKind; name: string; label: string; description: string; prompt: string; tags: string[]; scene_time: string }

type Scope = { kind: 'global' } | { kind: 'project'; projectId: string } | { kind: 'episode'; projectId: string; episodeId: string };
const path = (scope: Scope) => scope.kind === 'global' ? '/libraries/global/assets' : scope.kind === 'project' ? `/projects/${encodeURIComponent(scope.projectId)}/assets` : `/projects/${encodeURIComponent(scope.projectId)}/episodes/${encodeURIComponent(scope.episodeId)}/assets`;
export const assetLibraries = {
  async list(scope: Scope, params: { kind?: AssetKind; q?: string; offset?: number; limit?: number }, signal?: AbortSignal) { return (await http.get<Page<LibraryAssetRead>>(path(scope), { params, signal })).data; },
  async create(scope: Scope, body: AssetDraft, key: string) { return (await http.post<LibraryAssetRead>(path(scope), body, { headers: { 'Idempotency-Key': key } })).data; },
  async link(scope: Scope, id: string) { return (await http.put<LibraryAssetRead>(`${path(scope)}/${encodeURIComponent(id)}`, {})).data; },
  async unlink(scope: Scope, id: string, rowVersion: string) { await http.delete(`${path(scope)}/${encodeURIComponent(id)}`, { headers: { 'If-Match': `"${rowVersion}"` } }); },
  async detail(id: string, signal?: AbortSignal) { return (await http.get<AssetRead>(`/assets/${encodeURIComponent(id)}`, { signal })).data; },
  async update(id: string, body: Partial<Omit<AssetDraft, 'kind'>> & { row_version: string; confirm_shared?: boolean }) { return (await http.patch<AssetRead>(`/assets/${encodeURIComponent(id)}`, body)).data; },
  async candidates(id: string, signal?: AbortSignal, offset = 0) { return (await http.get<Page<AssetCandidate>>(`/assets/${encodeURIComponent(id)}/image-candidates`, { params: { offset, limit: 100 }, signal })).data; },
  async addCandidate(id: string, mediaId: string) { return (await http.post<AssetCandidate>(`/assets/${encodeURIComponent(id)}/image-candidates`, { media_id: mediaId })).data; },
  async upload(id: string, file: File) { const body = new FormData(); body.append('file', file); return (await http.post<AssetCandidate>(`/assets/${encodeURIComponent(id)}/image-candidates/upload`, body)).data; },
  async confirm(id: string, body: { row_version: string; media_id: string; expected_media_id: string | null; confirm_shared: boolean }) { return (await http.post<AssetRead>(`/assets/${encodeURIComponent(id)}/confirm`, body)).data; },
};
export type AssetScope = Scope;
