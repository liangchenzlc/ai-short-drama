export type AssetKind = 'character' | 'scene' | 'prop';

export interface AssetImage {
  media_id: string;
  url: string;
  width?: number | null;
  height?: number | null;
}

export interface AssetRead {
  id: string;
  kind: AssetKind;
  name: string;
  label: string;
  description: string;
  prompt: string;
  tags: string[];
  scene_time: string;
  state: 'unconfirmed' | 'confirmed';
  row_version: string;
  media_id: string | null;
  image: AssetImage | null;
  reference_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface LibraryAssetRead extends AssetRead {
  link_id: string | null;
  position: number | null;
}

export interface AssetCandidateGeneration {
  generation_id: string;
  record_id: string;
  source_asset_id: string;
  source_row_version: string | null;
  source_content_hash: string | null;
  is_stale: boolean;
  stale_reason: 'content_changed' | 'source_mismatch' | 'snapshot_missing' | null;
}

export interface AssetCandidate {
  id: string;
  media_id: string;
  url: string;
  width?: number | null;
  height?: number | null;
  created_at: string;
  generation: AssetCandidateGeneration | null;
}

export interface AssetDraft {
  kind: AssetKind;
  name: string;
  label: string;
  description: string;
  prompt: string;
  tags: string[];
  scene_time: string;
}

export type Scope = { kind: 'project'; projectId: string };

export type AssetScope = Scope;
