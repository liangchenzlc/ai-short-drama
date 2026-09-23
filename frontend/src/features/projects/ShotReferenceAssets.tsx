import { Alert, Button, Tag } from 'antd';
import type { AssetRead } from '../../api/modules/assets';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { summarizeShotReferences } from './shot-image-workflow';

export function ShotReferenceAssets({ assetIds, assets, loading, error, onRefresh }: {
  assetIds: string[]; assets: AssetRead[]; loading: boolean; error: string; onRefresh: () => void;
}) {
  const summary = summarizeShotReferences(assetIds, assets);
  return <section aria-label="分镜参考素材">
    <div className="dialog-actions shot-image-heading"><strong>已关联 {summary.linkedCount} 个素材 · {summary.referenceMediaIds.length} 张确认参考图</strong><Button size="small" loading={loading} onClick={onRefresh}>刷新参考素材</Button></div>
    {error && <Alert type="warning" showIcon message={error}/>}
    <div className="shot-reference-list">{assetIds.map((id) => {
      const asset = assets.find((item) => item.id === id);
      if (!asset) return <p key={id}>素材 {id} 暂不可用，请刷新核对。</p>;
      const ready = asset.state === 'confirmed' && !!asset.media_id;
      return <div className="shot-reference-item" key={id}>
        {ready && asset.image?.url && <PreviewImage triggerClassName="shot-reference-preview" src={asset.image.url} alt={asset.name}/>}
        <div><strong>{asset.name}</strong><p>{({ character: '角色', scene: '场景', prop: '道具' })[asset.kind]} · <Tag color={ready ? 'green' : undefined}>{ready ? '已确认图片' : '仅文字信息'}</Tag></p></div>
      </div>;
    })}</div>
    {!summary.referenceMediaIds.length && <p className="episode-help">本次仅使用文字信息。素材图片需明确采用后，才会作为分镜参考图。</p>}
    {!!summary.unreadyAssetIds.length && <p className="episode-help">未确认或缺图的素材不会作为图片参考。</p>}
  </section>;
}
