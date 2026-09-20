import { http } from '../http';
import type { ApplyAssetRequest, AssetFilters, MediaAsset, Page } from '../types/generations';
const root = '/media-library/items';
export const mediaLibrary = {
  async list(params: AssetFilters, signal?: AbortSignal) { return (await http.get<Page<MediaAsset>>(root, { params, signal })).data; },
  async detail(id: string, signal?: AbortSignal) { return (await http.get<MediaAsset>(`${root}/${id}`, { signal })).data; },
  async rename(id: string, name: string, rowVersion: string) { return (await http.patch<MediaAsset>(`${root}/${id}`, { name, row_version: rowVersion })).data; },
  async apply(id: string, body: ApplyAssetRequest) { return (await http.post(`${root}/${id}/apply`, body)).data; },
};
