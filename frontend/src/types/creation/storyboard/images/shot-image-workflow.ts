import type { ShotRead } from '@host/types/creation/storyboard/shots';

export interface ImageCapabilities {
  known: boolean;
  reference_images: boolean;
  parameters?: string[];
}

export interface PreparedShot {
  shot: ShotRead;
  release: () => void;
  isCurrent: () => boolean;
}

export interface ShotReferenceSummary {
  linkedCount: number;
  referenceMediaIds: string[];
  unreadyAssetIds: string[];
  missingAssetIds: string[];
}
