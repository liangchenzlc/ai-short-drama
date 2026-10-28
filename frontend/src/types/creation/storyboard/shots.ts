import type { Page } from '@host/types/generations/api';

export interface WorkflowShot {
  id: string;
  row_version: string;
  context_hash: string;
  image_settings: {
    layout: 'single' | 'four' | 'five' | 'nine';
    aspect: null | '16:9' | '9:16' | '1:1' | '4:3' | '3:4';
    resolution: '1K' | '2K' | '4K';
  };
  image?: { media_id: string } | null;
}

export interface ScriptCandidate {
  id: string;
  position: number;
  state: 'unconfirmed' | 'confirmed';
  preview: string;
  is_editing: boolean;
  is_confirmed: boolean;
  generation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScriptDetail extends ScriptCandidate {
  content: string;
}

export interface ShotImage {
  media_id: string;
  media_asset_id: string | null;
  url: string;
  width?: number | null;
  height?: number | null;
  layout: string;
  aspect: string;
  resolution: string;
  is_stale: boolean;
}

export interface ShotVideo {
  media_id: string;
  media_asset_id: string | null;
  url: string | null;
  resolution: string;
  duration_ms: number;
  first_frame_media_id: string | null;
  is_stale: boolean;
}

export interface ShotRead extends WorkflowShot {
  position: number;
  script: string;
  duration_ms: number;
  source_excerpt: string;
  asset_ids: string[];
  image: ShotImage | null;
  deleted_at: string | null;
  video_prompt: string;
  video_default_prompt: string;
  video_system_prompt: string;
  video_settings: {
    resolution: '480p' | '720p' | '1080p';
    duration_ms?: number | null;
  };
  video_context_hash: string;
  video: ShotVideo | null;
  native_speech?: {
    mode: 'native';
    reviewed: boolean;
    voices: {
      character_id: string;
      media_id: string | null;
      duration_ms: number | null;
    }[];
  } | null;
}

export interface StoryboardPage {
  storyboard_version: string;
  items: ShotRead[];
  total: number;
  offset: number;
  limit: number;
}

export interface StoryboardResultAsset {
  id: string;
  kind: 'character' | 'prop' | 'scene' | null;
  name: string;
  available: boolean;
  snapshot_missing: boolean;
}

export interface StoryboardResultShot {
  position: number;
  title: string;
  script: string;
  duration_ms: number;
  source_excerpt: string;
  story_beat: string;
  asset_ids: string[];
  assets: StoryboardResultAsset[];
}

export interface StoryboardResultPage extends Page<StoryboardResultShot> {
  generation_id: string;
  total_duration_ms: number;
  applied: {
    mode: 'append' | 'replace';
    shot_ids: string[];
    applied_at: string | null;
    storyboard_version: string | null;
  } | null;
}

export interface ShotMutation {
  shot: ShotRead;
  storyboard_version: string;
}

export type ShotSize = 'full_shot' | 'medium_shot' | 'close_up' | 'extreme_close_up' | 'wide_shot' | 'two_shot';

export interface ShotRead {
  title?: string;
  shot_size?: ShotSize | null;
}

export interface StoryboardApplyResult {
  generation_id: string;
  mode: 'append' | 'replace';
  shot_ids: string[];
  storyboard_version: string;
  already_applied: boolean;
}

/** ????????????????? API ????????? */
export interface StoryboardApi {
  scripts(signal?: AbortSignal, offset?: number): Promise<import('@host/types/generations/api').Page<ScriptCandidate>>;
  script(id: string, signal?: AbortSignal): Promise<ScriptDetail>;
  shots(signal?: AbortSignal, includeArchived?: boolean, offset?: number, limit?: number): Promise<StoryboardPage>;
  shot(id: string, signal?: AbortSignal): Promise<ShotMutation>;
  create(storyboardVersion: string, key: string): Promise<ShotMutation>;
  update(
    id: string,
    body: {
      row_version: string;
      title?: string;
      shot_size?: ShotSize | null;
      script?: string;
      duration_ms?: number;
      asset_ids?: string[];
      image_settings?: ShotRead['image_settings'];
      video_prompt?: string;
      video_settings?: ShotRead['video_settings'];
    },
  ): Promise<ShotMutation>;
  remove(
    id: string,
    body: { row_version: string; storyboard_version: string },
  ): Promise<{ storyboard_version: string; deleted_id: string }>;
  move(id: string, storyboardVersion: string, direction: -1 | 1): Promise<unknown>;
  order(storyboardVersion: string, shotIds: string[]): Promise<{ storyboard_version: string; ordered_ids: string[] }>;
  resultShots(id: string, offset?: number, signal?: AbortSignal): Promise<StoryboardResultPage>;
  apply(
    generationId: string,
    body: {
      mode: 'append' | 'replace';
      content_version: string;
      storyboard_version: string;
      confirm_replace: boolean;
    },
  ): Promise<StoryboardApplyResult>;
}
