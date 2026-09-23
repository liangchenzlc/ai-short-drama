import type { LibraryAssetRead } from '../../api/modules/assets';
import type { GenerationSummary, ImageGenerationRequest } from '../../api/types/generations';

export interface AssetImageOptions {
  configId?: string;
  supplement: string;
  count: number;
  aspect?: ImageGenerationRequest['parameters']['aspect'];
  resolution?: string;
}

export function buildAssetImageRequest(asset: Pick<LibraryAssetRead, 'id' | 'row_version'>, options: AssetImageOptions): ImageGenerationRequest {
  return {
    ...(options.configId ? { config_id: options.configId } : {}),
    source: { scene: 'asset_image', asset_id: asset.id, row_version: asset.row_version },
    input: { prompt: options.supplement, reference_media_ids: [] },
    parameters: {
      count: options.count,
      ...(options.aspect ? { aspect: options.aspect } : {}),
      ...(options.resolution?.trim() ? { resolution: options.resolution.trim() } : {}),
    },
  };
}

export function mergeAssetImageTasks(current: GenerationSummary[], incoming: GenerationSummary[]): GenerationSummary[] {
  const merged = new Map(current.map((task) => [task.generation_id, task]));
  for (const task of incoming) merged.set(task.generation_id, task);
  return [...merged.values()].sort((left, right) => {
    const byTime = Date.parse(right.created_at) - Date.parse(left.created_at);
    if (Number.isFinite(byTime) && byTime !== 0) return byTime;
    try {
      const leftId = BigInt(left.generation_id);
      const rightId = BigInt(right.generation_id);
      return rightId > leftId ? 1 : rightId < leftId ? -1 : 0;
    } catch {
      return right.generation_id.localeCompare(left.generation_id);
    }
  });
}

const ACTIVE_STATUSES = new Set(['queued', 'running']);

export function shouldRefreshAssetCandidates(
  previous: Pick<GenerationSummary, 'generation_id' | 'status'>[],
  incoming: Pick<GenerationSummary, 'generation_id' | 'status'>[],
): boolean {
  const before = new Map(previous.map((task) => [task.generation_id, task.status]));
  return incoming.some((task) => !before.has(task.generation_id) || ACTIVE_STATUSES.has(task.status)
    || (ACTIVE_STATUSES.has(before.get(task.generation_id) ?? '') && !ACTIVE_STATUSES.has(task.status)));
}
export function missingActiveTaskIds(
  previous: Pick<GenerationSummary, 'generation_id' | 'status'>[],
  incoming: Pick<GenerationSummary, 'generation_id'>[],
): string[] {
  const seen = new Set(incoming.map((task) => task.generation_id));
  return previous
    .filter((task) => ACTIVE_STATUSES.has(task.status) && !seen.has(task.generation_id))
    .map((task) => task.generation_id);
}
export function assetImageGenerationBlockReason(
  asset: Pick<LibraryAssetRead, 'name' | 'description' | 'prompt'>,
  configId?: string,
): string | null {
  if (!configId) return '请选择可用的生图模型。';
  if (!asset.name.trim()) return '请先填写素材名称。';
  if (!asset.description.trim() && !asset.prompt.trim()) return '请先填写素材描述或提示词。';
  return null;
}