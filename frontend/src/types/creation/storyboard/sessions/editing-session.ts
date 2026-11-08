import type { ShotRead } from '@host/types/creation/storyboard/shots';

export type ShotDraftValue = Pick<
  ShotRead,
  'title' | 'shot_size' | 'script' | 'details' | 'duration_ms' | 'asset_ids' | 'image_settings' | 'video_prompt' | 'video_settings'
>;

export type Resource = { shot: ShotRead; storyboard_version: string };
