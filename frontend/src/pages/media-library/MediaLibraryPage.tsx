import { Alert, Button, Empty, Pagination, Skeleton, Tabs } from 'antd';
import { lazy, Suspense, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { mediaLibrary } from '../../api/modules/media-library';
import type { AssetFilters, MediaAsset } from '../../api/types/generations';
import { Icon } from '../../components/ui/Icon';
import { ListToolbar, PageHeader } from '../../components/ui/Workspace';
import { useRemotePage } from '../../features/generations/useRemotePage';
import './media-library.css';

const AssetDetail = lazy(() => import('../../features/media-library/AssetDetail').then(module => ({ default: module.AssetDetail })));

function MediaCard({ asset, onSelect }: { asset: MediaAsset; onSelect: (id: string) => void }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const available = asset.url && asset.url !== failedUrl;
  return <button type="button" className="media-gallery-card" onClick={() => onSelect(asset.asset_id)}
    aria-label={`查看${asset.name}`} aria-haspopup="dialog">
    {available ? asset.media_type === 'video'
      ? <video src={asset.url!} preload="metadata" muted playsInline aria-hidden="true"
        onError={() => setFailedUrl(asset.url)}/>
      : <img src={asset.url!} alt="" loading="lazy" decoding="async"
        width={asset.width ?? undefined} height={asset.height ?? undefined}
        onError={() => setFailedUrl(asset.url)}/>
      : <span className="media-gallery-placeholder" aria-hidden="true">
        <Icon name={asset.media_type === 'video' ? 'film' : 'scene'} size={32}/>
      </span>}
    <span className="media-gallery-name">{asset.name}</span>
  </button>;
}

export function MediaLibraryPage({ kind }: { kind: 'image' | 'video' }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const query: AssetFilters = { media_type: kind, offset, limit: 20 };
  const { data, error, loading, refresh } = useRemotePage(query, mediaLibrary.list);
  const selected = params.get('asset');
  function selectAsset(id?: string) {
    const next = new URLSearchParams(params);
    if (id) next.set('asset', id); else next.delete('asset');
    setParams(next);
  }
  return <section className="studio-page generation-page media-gallery-page" aria-labelledby="media-library-title">
    <PageHeader id="media-library-title" title="资产库" />
    <Tabs activeKey={kind} onChange={value => navigate(`/media-library/${value}`)}
      items={[{ key: 'image', label: '图片资产' }, { key: 'video', label: '视频资产' }]} />
    <ListToolbar count={data ? `共 ${data.total} 个${kind === 'image' ? '图片' : '视频'}资产` : '已保存资产'}
      hint="点击画面预览与整理" actions={null} />
    {error && <Alert type="error" showIcon message={error}
      action={<Button onClick={refresh} loading={loading}>重新加载</Button>} />}
    {loading && !data ? <div className="media-gallery-grid media-gallery-loading" role="status" aria-label="正在加载资产">
      {[0, 1, 2, 3].map(item => <Skeleton.Node key={item} active />)}
    </div> : data?.items.length ? <div className="media-gallery-grid" aria-busy={loading}>
      {data.items.map(asset => <MediaCard key={asset.asset_id} asset={asset} onSelect={selectAsset}/>)}
    </div> : <div className="generation-empty">
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={error ? '暂时无法加载资产' : '还没有资产，完成图片或视频生成后会显示在这里'} />
    </div>}
    <Pagination className="generation-pagination" current={Math.floor(offset / 20) + 1} pageSize={20}
      total={data?.total ?? 0} showSizeChanger={false} hideOnSinglePage onChange={page => {
        const next = new URLSearchParams(params);
        next.set('offset', String((page - 1) * 20)); setParams(next);
      }} />
    {selected && <Suspense fallback={<p role="status" className="generation-hint">正在加载资产…</p>}>
      <AssetDetail key={selected} id={selected} onClose={() => selectAsset()} onChanged={refresh} />
    </Suspense>}
  </section>;
}
