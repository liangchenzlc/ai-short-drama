import { http } from '@host/api/http';
import type {
  AssetCandidate,
  AssetDraft,
  AssetKind,
  AssetRead,
  LibraryAssetRead,
  Scope,
} from '@host/types/creation/assets/management';
import type { Page } from '@host/types/generations/api';
const path = (scope: Scope) => `/projects/${encodeURIComponent(scope.projectId)}/assets`;
export const projectAssetsApi = {
  async list(
    scope: Scope,
    params: { kind?: AssetKind; q?: string; offset?: number; limit?: number },
    signal?: AbortSignal,
  ) {
    return (await http.get<Page<LibraryAssetRead>>(path(scope), { params, signal })).data;
  },
  async create(scope: Scope, body: AssetDraft, key: string) {
    return (
      await http.post<LibraryAssetRead>(path(scope), body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async unlink(scope: Scope, id: string, rowVersion: string) {
    await http.delete(`${path(scope)}/${encodeURIComponent(id)}`, {
      headers: { 'If-Match': `"${rowVersion}"` },
    });
  },
  async detail(id: string, signal?: AbortSignal) {
    return (await http.get<AssetRead>(`/assets/${encodeURIComponent(id)}`, { signal })).data;
  },
  async update(id: string, body: Partial<AssetDraft> & { row_version: string }) {
    return (await http.patch<AssetRead>(`/assets/${encodeURIComponent(id)}`, body)).data;
  },
  async candidates(id: string, signal?: AbortSignal, offset = 0, limit = 20) {
    return (
      await http.get<Page<AssetCandidate>>(`/assets/${encodeURIComponent(id)}/image-candidates`, {
        params: { offset, limit },
        signal,
      })
    ).data;
  },
  async addCandidate(id: string, mediaId: string) {
    return (
      await http.post<AssetCandidate>(`/assets/${encodeURIComponent(id)}/image-candidates`, { media_id: mediaId })
    ).data;
  },
  async upload(id: string, file: File) {
    const body = new FormData();
    body.append('file', file);
    return (await http.post<AssetCandidate>(`/assets/${encodeURIComponent(id)}/image-candidates/upload`, body)).data;
  },
  async confirm(
    id: string,
    body: {
      row_version: string;
      media_id: string;
      expected_media_id: string | null;
      acknowledge_stale_source?: boolean;
    },
  ) {
    return (await http.post<AssetRead>(`/assets/${encodeURIComponent(id)}/confirm`, body)).data;
  },
};
