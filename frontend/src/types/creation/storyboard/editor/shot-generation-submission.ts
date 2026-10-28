import type { ImageGenerationRequest, VideoGenerationRequest } from '@host/types/generations/api';

export interface PendingShotGeneration {
  format: 1;
  key: string;
  body: ImageGenerationRequest | VideoGenerationRequest;
}
