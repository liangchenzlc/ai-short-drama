import type { ImageGenerationRequest, VideoGenerationRequest } from '../../api/types/generations';

export interface PendingShotGeneration {
  format: 1;
  key: string;
  body: ImageGenerationRequest | VideoGenerationRequest;
}

export function isPendingShotGeneration(value: unknown): value is PendingShotGeneration {
  if (!value || typeof value !== 'object') return false;
  const pending = value as Partial<PendingShotGeneration>;
  const source = pending.body?.source;
  return pending.format === 1 && typeof pending.key === 'string'
    && /^[0-9a-f-]{36}$/i.test(pending.key) && !!source
    && (source.scene === 'shot_image' || source.scene === 'shot_video')
    && /^\d+$/.test(source.shot_id) && typeof source.row_version === 'string'
    && /^\d+$/.test(source.row_version) && typeof source.context_hash === 'string'
    && typeof source.draft_id === 'string' && /^\d+$/.test(source.draft_id)
    && typeof source.draft_version === 'string' && /^\d+$/.test(source.draft_version)
    && !!pending.body?.parameters;
}
