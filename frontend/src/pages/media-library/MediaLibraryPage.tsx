import { Alert, Button, Empty, Form, Input, Pagination, Skeleton, Tabs } from 'antd';
import { Icon } from '../../components/ui/Icon';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { mediaLibrary } from '../../api/modules/media-library';
import { AssetDetail } from '../../features/media-library/AssetDetail';
import { dateLabel } from '../../features/generations/presentation';
import { isServerId } from '../../features/generations/attempt';
import { useRemotePage } from '../../features/generations/useRemotePage';
import type { AssetFilters } from '../../api/types/generations';

export function MediaLibraryPage({ kind }: { kind: 'image' | 'video' }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [form] = Form.useForm();
  const [filtersOpen, setFiltersOpen] = useState(() => ['source_id', 'created_after', 'created_before'].some(key => params.has(key)));
  const [search, setSearch] = useState(params.get('name') ?? '');
  const filtered = ['name', 'source_id', 'created_after', 'created_before'].some(key => !!params.get(key));
  const filterKey = JSON.stringify(Object.fromEntries(['name', 'source_id', 'created_after', 'created_before'].map((key) => [key, params.get(key) ?? ''])));
  useEffect(() => { const values = JSON.parse(filterKey); form.setFieldsValue(values); setSearch(values.name); }, [form, filterKey]);
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const sourceId = params.get('source_id') || undefined;
  const query: AssetFilters = { media_type: kind, offset, limit: 20, name: params.get('name') || undefined, ...(sourceId ? { source_id: sourceId, source_scene: 'shot_image' } : {}) };
  for (const field of ['created_after', 'created_before'] as const) { const value = params.get(field); if (value) query[field] = !Number.isNaN(new Date(value).getTime()) ? new Date(value).toISOString() : value; }
  const { data, error, loading, refresh } = useRemotePage(query, mediaLibrary.list);
  const selected = params.get('asset');
  function selectAsset(id?: string) { const next = new URLSearchParams(params); if (id) next.set('asset', id); else next.delete('asset'); setParams(next); }
  return <section className="studio-page generation-page" aria-labelledby="media-library-title">
    <div className="studio-page-head"><div><h1 id="media-library-title">资产库</h1><p>收藏每一次生成的画面，预览、整理，再用到作品中。</p></div><Button onClick={() => navigate(`/tasks/${kind}`)}>查看生成任务</Button></div>
    <Tabs activeKey={kind} onChange={(value) => navigate(`/media-library/${value}`)} items={[{ key: 'image', label: '图片资产' }, { key: 'video', label: '视频资产' }]} />
    <div className="library-search-bar"><Input.Search value={search} onChange={event => setSearch(event.target.value)} aria-label="搜索资产名称" placeholder={`搜索${kind === 'image' ? '图片' : '视频'}名称`} allowClear maxLength={255} onSearch={name => { const next = new URLSearchParams(params); if (name.trim()) next.set('name', name.trim()); else next.delete('name'); next.delete('offset'); setParams(next); }}/>{filtered && <Button onClick={() => setParams({})}>清除筛选</Button>}</div>
    <details className="generation-filter-panel" open={filtersOpen} onToggle={(event) => setFiltersOpen(event.currentTarget.open)}><summary>更多筛选<span>来源与入库时间</span></summary>
    <Form form={form} layout="vertical" className="generation-filters asset-filters" initialValues={JSON.parse(filterKey)} onFinish={(values) => { const next = new URLSearchParams(); for (const field of ['name', 'source_id', 'created_after', 'created_before']) if (values[field]?.trim()) next.set(field, values[field].trim()); setParams(next); }}>
      <Form.Item name="name" hidden><Input /></Form.Item>
      {kind === 'image' && <Form.Item name="source_id" label="来源分镜编号" rules={[{ validator: (_, value?: string) => !value || isServerId(value.trim()) ? Promise.resolve() : Promise.reject(new Error('请输入有效的分镜编号')) }]}><Input placeholder="输入分镜编号（选填）" /></Form.Item>}
      <Form.Item name="created_after" label="入库时间起"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="created_before" label="入库时间止"><Input type="datetime-local" /></Form.Item>
      <div className="generation-filter-actions"><Button type="primary" htmlType="submit">筛选</Button><Button onClick={() => { form.setFieldsValue({ name: undefined, source_id: undefined, created_after: undefined, created_before: undefined }); setParams({}); }}>重置</Button></div>
    </Form></details>
    <div className="generation-list-toolbar"><p>{data ? `共 ${data.total} 个${kind === 'image' ? '图片' : '视频'}资产` : '已保存资产'}<span>点击画面查看详情</span></p><Button onClick={refresh} loading={loading}>刷新</Button></div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重新加载</Button>} />}
    {loading && !data ? <div className="asset-library-skeleton" role="status" aria-label="正在加载资产">{[0, 1, 2].map(item => <Skeleton key={item} title paragraph={{ rows: 3 }}/>)}</div> : data?.items.length ? <div className="asset-library-grid" aria-busy={loading}>
      {data.items.map((asset) => <article key={asset.asset_id} className="asset-library-item">
        <button className="asset-library-preview" onClick={() => selectAsset(asset.asset_id)} aria-label={`预览${asset.name}`}>
          {kind === 'image' && asset.url ? <img src={asset.url} alt={asset.name} loading="lazy" onError={(event) => { event.currentTarget.style.display = 'none'; }} /> : null}
          <span className="asset-library-placeholder"><Icon name={kind === 'video' ? 'film' : 'scene'} size={32}/>{kind === 'video' ? '预览视频' : '查看图片'}</span>
        </button>
        <div className="asset-library-caption"><h2>{asset.name}</h2><p>{dateLabel(asset.created_at)}</p><div><span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : kind === 'video' ? '视频资产' : '图片资产'}</span><Button onClick={() => selectAsset(asset.asset_id)}>查看详情</Button></div></div>
      </article>)}
    </div> : <div className="generation-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '暂时无法加载资产' : filtered ? '没有找到匹配的资产' : '还没有作品，先完成一次生成'} />{!error && <Button type={filtered ? 'default' : 'primary'} onClick={() => filtered ? setParams({}) : navigate(`/tasks/${kind}`)}>{filtered ? '清除筛选' : `前往${kind === 'image' ? '图片' : '视频'}生成`}</Button>}</div>}
    <Pagination className="generation-pagination" current={Math.floor(offset / 20) + 1} pageSize={20} total={data?.total ?? 0} showSizeChanger={false} hideOnSinglePage onChange={(page) => { const next = new URLSearchParams(params); next.set('offset', String((page - 1) * 20)); setParams(next); }} />
    {selected && <AssetDetail key={selected} id={selected} onClose={() => selectAsset()} onChanged={refresh} />}
  </section>;
}
