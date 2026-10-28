import { mediaLibrary } from '@host/api/media/library';
import { useRemotePage } from '@host/features/generations/tasks/useRemotePage';
import { Icon } from '@host/shared/ui/Icon';
import { PageHeader } from '@host/shared/ui/Workspace';
import type { AssetFilters, MediaAsset } from '@host/types/generations/api';
import { Alert, Button, Empty, Input, Pagination, Skeleton, Tabs } from 'antd';
import { Music2 } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import './index.css';
const AssetPreview = lazy(() =>
  import('../../features/media/library/AssetPreview').then((module) => ({
    default: module.AssetPreview,
  })),
);
const pageSize = 24;
const mediaKinds = [
  { key: 'all', label: '全部' },
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
];

function MediaCard({ asset, onSelect }: { asset: MediaAsset; onSelect: (id: string) => void }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const available = asset.url && asset.url !== failedUrl;
  return (
    <button
      type="button"
      className="media-gallery-card"
      onClick={() => onSelect(asset.asset_id)}
      aria-label={`预览${asset.name}`}
      aria-haspopup="dialog"
    >
      {asset.media_type === 'audio' ? (
        <span className="media-gallery-placeholder" aria-hidden="true">
          <Music2 size={30} />
        </span>
      ) : available ? (
        asset.media_type === 'video' ? (
          <video
            src={asset.url!}
            preload="metadata"
            muted
            playsInline
            aria-hidden="true"
            onError={() => setFailedUrl(asset.url)}
          />
        ) : (
          <img
            src={asset.url!}
            alt=""
            loading="lazy"
            decoding="async"
            width={asset.width ?? undefined}
            height={asset.height ?? undefined}
            onError={() => setFailedUrl(asset.url)}
          />
        )
      ) : (
        <span className="media-gallery-placeholder" aria-hidden="true">
          <Icon name={asset.media_type === 'video' ? 'film' : 'scene'} size={30} />
        </span>
      )}
      {asset.media_type !== 'image' && (
        <span className="media-gallery-kind" aria-hidden="true">
          {asset.media_type === 'video' ? '视频' : '音频'}
        </span>
      )}
      <span className="media-gallery-name" title={asset.name}>
        {asset.name}
      </span>
    </button>
  );
}

export function MediaLibraryPage({ kind }: { kind: 'all' | MediaAsset['media_type'] }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const rawMode = params.get('workspace_mode');
  const workspaceMode = rawMode === 'standard' || rawMode === 'infinite_canvas' ? rawMode : 'all';
  const name = (params.get('name') ?? '').trim();
  const [search, setSearch] = useState(name);
  const [composing, setComposing] = useState(false);
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const query: AssetFilters = {
    offset,
    limit: pageSize,
    ...(kind !== 'all' ? { media_type: kind } : {}),
    ...(workspaceMode !== 'all' ? { workspace_mode: workspaceMode } : {}),
    ...(name ? { name } : {}),
  };
  const { data, error, loading, refresh } = useRemotePage(query, mediaLibrary.list);
  const selected = params.get('asset');
  const filtered = kind !== 'all' || workspaceMode !== 'all' || !!name;
  const updateFilter = useCallback(
    (key: string, value: string, replace = false) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (value) next.set(key, value);
          else next.delete(key);
          next.delete('offset');
          next.delete('asset');
          return next;
        },
        { replace },
      );
    },
    [setParams],
  );
  useEffect(() => setSearch(name), [name]);
  useEffect(() => {
    if (composing || search.trim() === name) return;
    const timer = setTimeout(() => updateFilter('name', search.trim(), true), search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [search, name, composing, updateFilter]);
  function selectAsset(id?: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set('asset', id);
      else next.delete('asset');
      return next;
    });
  }
  function changeKind(value: string) {
    const next = new URLSearchParams(params);
    next.delete('offset');
    next.delete('asset');
    navigate({ pathname: `/media-library/${value}`, search: next.toString() });
  }
  return (
    <section className="studio-page generation-page media-gallery-page" aria-labelledby="media-library-title">
      <PageHeader id="media-library-title" title="资产库" />
      <div className="media-gallery-sources">
        <Tabs
          activeKey={workspaceMode}
          aria-label="按生成工作台筛选"
          onChange={(value) => updateFilter('workspace_mode', value === 'all' ? '' : value)}
          items={[
            { key: 'all', label: '生成历史' },
            { key: 'standard', label: '创作模式' },
            { key: 'infinite_canvas', label: '画布模式' },
          ]}
        />
      </div>
      <div className="media-gallery-toolbar">
        <div className="media-gallery-filters" role="group" aria-label="按媒体类型筛选">
          {mediaKinds.map((item) => (
            <Button key={item.key} aria-pressed={kind === item.key} onClick={() => changeKind(item.key)}>
              {item.label}
            </Button>
          ))}
        </div>
        <Input
          type="search"
          className="media-gallery-search"
          aria-label="搜索资产名称"
          placeholder="搜索资产名称"
          allowClear
          maxLength={255}
          prefix={<Icon name="search" size={16} />}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
        />
        <span className="media-gallery-count" role="status">
          {data ? `共 ${data.total} 个资产` : loading ? '正在加载资产…' : '已保存资产'}
        </span>
      </div>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button onClick={refresh} loading={loading}>
              重新加载
            </Button>
          }
        />
      )}
      {loading && !data ? (
        <div className="media-gallery-grid media-gallery-loading" role="status" aria-label="正在加载资产">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton.Node key={index} active />
          ))}
        </div>
      ) : data?.items.length ? (
        <div className="media-gallery-grid" aria-busy={loading}>
          {data.items.map((asset) => (
            <MediaCard key={asset.asset_id} asset={asset} onSelect={selectAsset} />
          ))}
        </div>
      ) : (
        <div className="generation-empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              error
                ? '暂时无法加载资产'
                : filtered
                  ? '没有符合条件的资产'
                  : '还没有资产，在工作台生成图片、视频或音频后会显示在这里'
            }
          />
          {filtered && !error && <Button onClick={() => navigate('/media-library/all')}>清除筛选</Button>}
        </div>
      )}
      <Pagination
        className="generation-pagination"
        current={Math.floor(offset / pageSize) + 1}
        pageSize={pageSize}
        total={data?.total ?? 0}
        showSizeChanger={false}
        hideOnSinglePage
        onChange={(page) => {
          const next = new URLSearchParams(params);
          next.set('offset', String((page - 1) * pageSize));
          next.delete('asset');
          setParams(next);
        }}
      />
      {selected && (
        <Suspense
          fallback={
            <p role="status" className="generation-hint">
              正在加载资产预览…
            </p>
          }
        >
          <AssetPreview key={selected} id={selected} onClose={() => selectAsset()} />
        </Suspense>
      )}
    </section>
  );
}
