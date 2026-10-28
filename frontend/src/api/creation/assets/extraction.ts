import { http } from '@host/api/http';
import type { ExtractionApplyRequest, ExtractionResult } from '@host/types/creation/assets/extraction';
import type { AssetDraft } from '@host/types/creation/assets/management';
export const assetExtractionApi = (projectId: string) => {
  const root = `/projects/${projectId}/asset-extraction-results`;
  return {
    async get(id: string, signal?: AbortSignal) {
      return (await http.get<ExtractionResult>(`${root}/${id}`, { signal })).data;
    },
    async save(
      id: string,
      body: {
        result_version: string;
        items: { candidate_id: string; draft: AssetDraft }[];
      },
    ) {
      return (await http.patch<ExtractionResult>(`${root}/${id}`, body)).data;
    },
    async apply(id: string, body: ExtractionApplyRequest, key: string) {
      return (
        await http.post<{
          created: number;
          reused: number;
          already_applied: boolean;
        }>(`${root}/${id}/apply`, body, { headers: { 'Idempotency-Key': key } })
      ).data;
    },
  };
};
