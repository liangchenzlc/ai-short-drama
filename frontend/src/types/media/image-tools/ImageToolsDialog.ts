import type { ImageGenerationRequest, TextGenerationRequest } from '@host/types/generations/api';
import type { ImageOperationRequest } from '@host/types/media/image-groups';

export interface PendingTool {
  format: 1;
  body: ImageOperationRequest;
  key: string;
  operationId?: string;
  generation?: {
    kind: 'text' | 'image';
    key: string;
    taskId?: string;
    body: ImageGenerationRequest | TextGenerationRequest;
  };
}
