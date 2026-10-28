import type { PendingAssetGeneration } from '@host/types/creation/assets/images/asset-generation-submission';
export function isPendingAssetGeneration(value: unknown): value is PendingAssetGeneration {
  if (!value || typeof value !== 'object') return false;
  const pending = value as Partial<PendingAssetGeneration>;
  return (
    typeof pending.key === 'string' &&
    !!pending.key &&
    pending.body?.source?.scene === 'asset_image' &&
    typeof pending.body.source.asset_id === 'string' &&
    typeof pending.body.source.row_version === 'string' &&
    !!pending.body.input &&
    typeof pending.body.input.prompt === 'string' &&
    Array.isArray(pending.body.input.reference_media_ids) &&
    typeof pending.body.parameters?.count === 'number'
  );
}
export function assetGenerationAcceptanceUnknown(cause: unknown) {
  const error = cause as { status?: number; code?: string };
  return error.status === undefined || error.status >= 500 || error.status === 408;
}
