import { Alert, Button, Empty, Form, Input, Pagination, Spin, Tabs } from 'antd';
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
  const [filtersOpen, setFiltersOpen] = useState(() => !window.matchMedia('(max-width: 760px)').matches);
  const filterKey = JSON.stringify(Object.fromEntries(['name', 'source_id', 'created_after', 'created_before'].map((key) => [key, params.get(key) ?? ''])));
  useEffect(() => { form.setFieldsValue(JSON.parse(filterKey)); }, [form, filterKey]);
  const rawOffset = Number(params.get('offset') ?? 0);
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0;
  const sourceId = params.get('source_id') || undefined;
  const query: AssetFilters = { media_type: kind, offset, limit: 20, name: params.get('name') || undefined, ...(sourceId ? { source_id: sourceId, source_scene: 'shot_image' } : {}) };
  for (const field of ['created_after', 'created_before'] as const) { const value = params.get(field); if (value) query[field] = !Number.isNaN(new Date(value).getTime()) ? new Date(value).toISOString() : value; }
  const { data, error, loading, refresh } = useRemotePage(query, mediaLibrary.list);
  const selected = params.get('asset');
  function selectAsset(id?: string) { const next = new URLSearchParams(params); if (id) next.set('asset', id); else next.delete('asset'); setParams(next); }
  return <section className="studio-page generation-page" aria-labelledby="media-library-title">
    <div className="studio-page-head"><div><h1 id="media-library-title">资产库</h1><p>已保存的图片与视频。预览、整理名称，或确认采用到真实业务目标。</p></div><Button onClick={() => navigate(`/tasks/${kind}`)}>前往生成任务</Button></div>
    <Tabs activeKey={kind} onChange={(value) => navigate(`/media-library/${value}`)} items={[{ key: 'image', label: '图片资产' }, { key: 'video', label: '视频资产' }]} />
    <details className="generation-filter-panel" open={filtersOpen} onToggle={(event) => setFiltersOpen(event.currentTarget.open)}><summary>筛选资产<span>名称 / 来源 / 时间</span></summary>
    <Form form={form} layout="vertical" className="generation-filters asset-filters" initialValues={JSON.parse(filterKey)} onFinish={(values) => { const next = new URLSearchParams(); for (const field of ['name', 'source_id', 'created_after', 'created_before']) if (values[field]?.trim()) next.set(field, values[field].trim()); setParams(next); }}>
      <Form.Item name="name" label="资产名称"><Input placeholder="搜索名称" allowClear maxLength={255} /></Form.Item>
      <Form.Item name="source_id" label="来源分镜 ID" rules={[{ validator: (_, value?: string) => !value || isServerId(value.trim()) ? Promise.resolve() : Promise.reject(new Error('请输入真实服务端 ID')) }]}><Input placeholder="真实服务端分镜 ID" /></Form.Item>
      <Form.Item name="created_after" label="入库时间起"><Input type="datetime-local" /></Form.Item>
      <Form.Item name="created_before" label="入库时间止"><Input type="datetime-local" /></Form.Item>
      <div className="generation-filter-actions"><Button type="primary" htmlType="submit">筛选</Button><Button onClick={() => { form.setFieldsValue({ name: undefined, source_id: undefined, created_after: undefined, created_before: undefined }); setParams({}); }}>重置</Button></div>
    </Form></details>
    <div className="generation-list-toolbar"><p>{data ? `共 ${data.total} 个资产` : '已保存资产'}<span>生成未全部完成时，已保存结果也会保留</span></p><Button onClick={refresh} loading={loading}>刷新</Button></div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重新加载</Button>} />}
    {loading && !data ? <div className="generation-loading"><Spin tip="加载资产…"><div /></Spin></div> : data?.items.length ? <div className="asset-library-grid">
      {data.items.map((asset) => <article key={asset.asset_id} className="asset-library-item">
        <button className="asset-library-preview" onClick={() => selectAsset(asset.asset_id)} aria-label={`预览${asset.name}`}>
          {kind === 'image' && asset.url ? <img src={asset.url} alt={asset.name} loading="lazy" onError={(event) => { event.currentTarget.style.display = 'none'; }} /> : null}
          <span className="asset-library-placeholder">{kind === 'video' ? '点击预览视频' : '点击查看图片'}</span>
        </button>
        <div className="asset-library-caption"><h2>{asset.name}</h2><p>{dateLabel(asset.created_at)}</p><div><span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : kind === 'video' ? '视频资产' : '图片资产'}</span><Button onClick={() => selectAsset(asset.asset_id)}>查看 / 采用</Button></div></div>
      </article>)}
    </div> : <div className="generation-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '暂时无法加载资产' : '还没有符合条件的资产'} />{!error && <Button onClick={() => navigate(`/tasks/${kind}`)}>创建{kind === 'image' ? '图片' : '视频'}任务</Button>}</div>}
    <Pagination className="generation-pagination" current={Math.floor(offset / 20) + 1} pageSize={20} total={data?.total ?? 0} showSizeChanger={false} onChange={(page) => { const next = new URLSearchParams(params); next.set('offset', String((page - 1) * 20)); setParams(next); }} />
    {selected && <AssetDetail key={selected} id={selected} onClose={() => selectAsset()} onChanged={refresh} />}
  </section>;
}
