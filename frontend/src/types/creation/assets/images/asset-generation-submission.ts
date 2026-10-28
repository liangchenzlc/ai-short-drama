import type { ImageGenerationRequest } from '@host/types/generations/api';

export interface PendingAssetGeneration {
  key: string;
  body: ImageGenerationRequest;
}
