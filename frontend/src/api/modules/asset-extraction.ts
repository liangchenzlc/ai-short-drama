import { http } from '../http';
import type { AssetDraft, AssetKind } from './assets';

export interface ExtractionMatch {
  asset_id: string; name: string; scope: 'episode' | 'project'; row_version: string;
  description: string; exact: boolean;
}
export interface ExtractionCandidate {
  candidate_id: string;
  original: AssetDraft & {
    aliases: string[]; evidence: string;
    importance?: 'core' | 'continuity'; story_function?: string;
  };
  draft: AssetDraft;
  matches: ExtractionMatch[];
  duplicate_candidates: string[];
  applied: { asset_id: string; action: 'create' | 'reuse'; applied_at: string } | null;
}
export interface ExtractionResult {
  generation_id: string; result_version: string; content_version: string; stale: boolean;
  kinds: AssetKind[]; items: ExtractionCandidate[];
}
export interface ExtractionApplyRequest {
  result_version: string; content_version: string;
  items: { candidate_id: string; action: 'create' | 'reuse'; asset_id?: string; expected_row_version?: string; confirm_duplicate?: boolean }[];
}
export const assetExtractionApi = (projectId: string, episodeId: string) => {
  const root = `/projects/${projectId}/episodes/${episodeId}/asset-extraction-results`;
  return {
    async get(id: string, signal?: AbortSignal) { return (await http.get<ExtractionResult>(`${root}/${id}`, { signal })).data; },
    async save(id: string, body: { result_version: string; items: { candidate_id: string; draft: AssetDraft }[] }) {
      return (await http.patch<ExtractionResult>(`${root}/${id}`, body)).data;
    },
    async apply(id: string, body: ExtractionApplyRequest, key: string) {
      return (await http.post<{ created: number; reused: number; already_applied: boolean }>(`${root}/${id}/apply`, body, { headers: { 'Idempotency-Key': key } })).data;
    },
  };
};
