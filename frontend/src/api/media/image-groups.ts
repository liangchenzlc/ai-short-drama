import { http } from '@host/api/http';
import type { Page } from '@host/types/generations/api';
import type {
  ImageGroup,
  ImageGroupItem,
  ImageOperation,
  ImageOperationRequest,
  ImageTargetKind,
} from '@host/types/media/image-groups';
export const imageGroups = {
  async current(kind: ImageTargetKind, id: string, signal?: AbortSignal) {
    return (
      await http.get<ImageGroup | null>(`/image-groups/current/${kind}/${id}`, {
        signal,
      })
    ).data;
  },
  async history(kind: ImageTargetKind, id: string, offset = 0, signal?: AbortSignal) {
    return (
      await http.get<Page<ImageGroup>>(`/image-groups/history/${kind}/${id}`, {
        params: { offset, limit: 20 },
        signal,
      })
    ).data;
  },
  async detail(id: string, signal?: AbortSignal) {
    return (await http.get<ImageGroup>(`/image-groups/${id}`, { signal })).data;
  },
  async primary(group: ImageGroup, item: ImageGroupItem) {
    return (
      await http.put<ImageGroup>(`/image-groups/${group.id}/primary`, {
        item_id: item.id,
        expected_item_version: item.item_version,
        expected_primary_version: group.primary_version,
        expected_target_version: group.target_version,
      })
    ).data;
  },
  async content(id: string, signal?: AbortSignal) {
    return (
      await http.get<Blob>(`/image-groups/media/${id}/content`, {
        responseType: 'blob',
        signal,
        timeout: 60_000,
      })
    ).data;
  },
  async create(body: ImageOperationRequest, key: string) {
    return (
      await http.post<ImageOperation>('/image-operations', body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data;
  },
  async operation(id: string, signal?: AbortSignal) {
    return (await http.get<ImageOperation>(`/image-operations/${id}`, { signal })).data;
  },
  async execute(id: string, file?: Blob) {
    const form = new FormData();
    if (file) form.append('file', file, 'image.png');
    return (
      await http.post<ImageOperation>(`/image-operations/${id}/execute`, form, {
        timeout: 120_000,
      })
    ).data;
  },
};
