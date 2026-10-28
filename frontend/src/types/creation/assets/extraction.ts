import type { AssetDraft, AssetKind } from '@host/types/creation/assets/management';

export interface ExtractionMatch {
  asset_id: string;
  name: string;
  scope: 'project';
  row_version: string;
  description: string;
  exact: boolean;
}

export interface ExtractionCandidate {
  candidate_id: string;
  original: AssetDraft & {
    aliases: string[];
    importance?: 'core' | 'continuity';
    story_function?: string;
  };
  draft: AssetDraft;
  matches: ExtractionMatch[];
  duplicate_candidates: string[];
  applied: {
    asset_id: string;
    action: 'create' | 'reuse';
    applied_at: string;
  } | null;
}

export interface ExtractionResult {
  generation_id: string;
  result_version: string;
  content_version: string;
  stale: boolean;
  kinds: AssetKind[];
  items: ExtractionCandidate[];
}

export interface ExtractionApplyRequest {
  result_version: string;
  content_version: string;
  items: {
    candidate_id: string;
    action: 'create' | 'reuse';
    asset_id?: string;
    expected_row_version?: string;
    confirm_duplicate?: boolean;
  }[];
}
