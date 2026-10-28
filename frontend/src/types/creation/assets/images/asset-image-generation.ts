import type { AssetScope } from '@host/types/creation/assets/management';
import type { ImageGenerationRequest } from '@host/types/generations/api';

export interface AssetImageOptions {
  configId?: string;
  scope?: AssetScope;
  supplement: string;
  count: number;
  aspect?: ImageGenerationRequest['parameters']['aspect'];
  resolution?: string;
  draft?: { id: string; draft_version: string };
}
