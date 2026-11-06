import type { AssetKind } from '@host/types/creation/assets/management';
import type { ShotSize } from '@host/types/creation/storyboard/shots';
import type { ImageGroup, ImageMedia } from '@host/types/media/image-groups';

export type WorkbenchStatus = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed';

export interface StoryView {
  genre: string;
  core: string;
  outline: string;
}

export interface EpisodeView {
  id: string;
  title: string;
  summary: string;
  script: string;
}

export interface CharacterProfile {
  role: '' | '主角' | '配角' | '群演';
  gender: string;
  age: string;
  identity: string;
  appearance: string;
  personality: string;
}

export interface AssetView {
  id: string;
  kind: AssetKind;
  name: string;
  description: string;
  tags: string[];
  profile: CharacterProfile | null;
  imageGroups: ImageGroup[];
  activeGroupId: string | null;
  adoptedImage: ImageMedia | null;
  imagePrompt: string;
}

export type VideoReference =
  | { type: 'asset'; assetId: string }
  | { type: 'shotImage'; mediaId: string };

export interface ShotView {
  id: string;
  episodeId: string;
  position: number;
  title: string;
  script: string;
  shotSize: ShotSize | null;
  durationMs: number;
  assetIds: string[];
  imageGroups: ImageGroup[];
  activeGroupId: string | null;
  adoptedImage: ImageMedia | null;
  imagePrompt: string;
  videoPrompt: string;
  videoReferences: VideoReference[];
  video: { mediaId: string; url: string } | null;
}

export interface BatchItemView {
  id: string;
  targetId: string;
  scene: 'asset_image' | 'shot_image' | 'shot_video';
  episodeId: string | null;
  status: WorkbenchStatus;
  error: string;
}