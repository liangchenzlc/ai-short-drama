import { http } from '@host/api/http';
import type { Batch, BatchDetail, BatchPreview, BatchRequest } from '@host/types/generations/batches/requests';
const root = '/ai/generation-batches';
export const generationBatches = {
  capabilities: async (signal?: AbortSignal) =>
    (await http.get<{ enabled: boolean }>(`${root}/capabilities`, { signal })).data,
  preflight: async (body: BatchRequest, signal?: AbortSignal) =>
    (await http.post<BatchPreview>(`${root}/preflight`, body, { signal })).data,
  create: async (body: BatchRequest & { preflight_hash: string; accepted_ids: string[] }, key: string) =>
    (
      await http.post<Batch>(root, body, {
        headers: { 'Idempotency-Key': key },
      })
    ).data,
  list: async (offset = 0, signal?: AbortSignal) =>
    (
      await http.get<{ items: Batch[]; total: number }>(root, {
        params: { offset, limit: 20 },
        signal,
      })
    ).data,
  detail: async (id: string, offset = 0, signal?: AbortSignal) =>
    (
      await http.get<BatchDetail>(`${root}/${id}`, {
        params: { offset, limit: 20 },
        signal,
      })
    ).data,
  control: async (id: string, action: 'pause' | 'resume' | 'cancel') =>
    (await http.post<Batch>(`${root}/${id}/${action}`)).data,
  retry: async (id: string, item_ids: string[], key: string) =>
    (await http.post<Batch>(`${root}/${id}/retry-failed`, { item_ids }, { headers: { 'Idempotency-Key': key } })).data,
};
