import type { AssetRead } from '../../api/modules/assets';
import type { ShotRead } from '../../api/modules/storyboard';

export interface ImageCapabilities { known: boolean; reference_images: boolean; parameters?: string[] }
export interface PreparedShot { shot: ShotRead; release: () => void; isCurrent: () => boolean }
export interface ShotReferenceSummary { linkedCount: number; referenceMediaIds: string[]; unreadyAssetIds: string[]; missingAssetIds: string[] }

export function summarizeShotReferences(assetIds: readonly string[], assets: readonly Pick<AssetRead, 'id' | 'state' | 'media_id'>[]): ShotReferenceSummary {
  const byId = new Map(assets.map((asset) => [asset.id, asset]));
  const referenceMediaIds = new Set<string>();
  const unreadyAssetIds: string[] = [];
  const missingAssetIds: string[] = [];
  for (const id of new Set(assetIds)) {
    const asset = byId.get(id);
    if (!asset) missingAssetIds.push(id);
    else if (asset.state === 'confirmed' && asset.media_id) referenceMediaIds.add(asset.media_id);
    else unreadyAssetIds.push(id);
  }
  return { linkedCount: new Set(assetIds).size, referenceMediaIds: [...referenceMediaIds], unreadyAssetIds, missingAssetIds };
}

export function shotPreparationChanged(local: ShotRead, remote: ShotRead): boolean {
  return local.id !== remote.id || local.row_version !== remote.row_version || local.context_hash !== remote.context_hash
    || !!remote.deleted_at || (local.image?.media_id ?? null) !== (remote.image?.media_id ?? null)
    || local.image_settings.layout !== remote.image_settings.layout || local.image_settings.aspect !== remote.image_settings.aspect
    || local.image_settings.resolution !== remote.image_settings.resolution;
}

export async function prepareShotOperation(options: {
  save: () => Promise<boolean>; local: () => ShotRead | undefined; read: () => Promise<ShotRead>;
  valid: () => boolean; accept: (shot: ShotRead) => void; changed: () => void; release: () => void;
}): Promise<PreparedShot | null> {
  let retained = false;
  let released = false;
  const release = () => { if (!released) { released = true; options.release(); } };
  try {
    if (!await options.save() || !options.valid()) return null;
    const local = options.local();
    if (!local) return null;
    const remote = await options.read();
    if (!options.valid()) return null;
    options.accept(remote);
    if (shotPreparationChanged(local, remote)) { options.changed(); return null; }
    retained = true;
    return { shot: remote, release, isCurrent: () => !released && options.valid() };
  } finally { if (!retained) release(); }
}

export function imageGenerationBlockReason(modelId: string | undefined, capabilities: ImageCapabilities | null, referenceCount: number, loading: boolean): string {
  if (!modelId) return '请先选择一个已启用的分镜生图模型。';
  if (loading) return '正在核对模型能力…';
  if (!capabilities?.known) return '模型能力尚未确认，请刷新能力或更换模型。';
  if (referenceCount && !capabilities.reference_images) return '当前模型接入方式暂不支持参考图，请更换已支持参考图的模型配置；不会自动丢弃关联图片。';
  return '';
}
