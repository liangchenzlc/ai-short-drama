import { PreviewImage } from '../../components/ui/ImagePreview';
import { useEffect, useState } from 'react';
import { Alert, Button, Input, Pagination, Skeleton } from 'antd';
import { mediaLibrary } from '../../api/modules/media-library';
import { errorMessage } from '../../api/http';
import type { MediaAsset } from '../../api/types/generations';
import { Dialog } from '../../components/ui/Dialog';

export function ImagePicker({ busy, onClose, onSelect }: {
  busy: boolean; onClose: () => void; onSelect: (mediaId: string, asset: MediaAsset) => Promise<boolean>;
}) {
  const [items, setItems] = useState<MediaAsset[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [choosing, setChoosing] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    void mediaLibrary.list({ media_type: 'image', name: query || undefined, offset, limit: 12 }, controller.signal)
      .then(page => { if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, offset, revision]);
  async function choose(item: MediaAsset) {
    if (busy || choosing) return;
    setChoosing(item.asset_id); setError('');
    try { if (await onSelect(item.media_id, item)) onClose(); else setError('图片未能添加，请关闭选择器查看错误详情，或重试。'); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setChoosing(null); }
  }
  return <Dialog title="从图片资产库选择" className="media-picker-dialog" canClose={!busy && !choosing} onClose={onClose}>
    <div className="media-picker-body">
      <p>点击图片查看大图，点击“选择此图片”添加。确认采用后才会更新素材。</p>
      <Input.Search aria-label="搜索图片名称" placeholder="搜索图片名称" allowClear disabled={busy || !!choosing} onSearch={name => { setQuery(name.trim()); setOffset(0); }}/>
      {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setRevision(value => value + 1)}>重试</Button>}/>}
      {loading ? <Skeleton paragraph={{ rows: 4 }}/> : items.length ? <div className="media-picker-grid">{items.map(item => <article className="media-picker-item" key={item.asset_id}>
        {item.url ? <PreviewImage src={item.url} alt={item.name}/> : <span className="media-picker-unavailable">预览暂不可用</span>}<strong>{item.name}</strong>
        <div className="media-picker-actions"><Button block disabled={busy || !!choosing || !item.url} loading={choosing === item.asset_id} onClick={() => void choose(item)}>选择此图片</Button></div>
      </article>)}</div> : !error && <div className="studio-empty"><h3>{query ? '没有找到图片' : '图片资产库还是空的'}</h3><p>{query ? '试试其他名称，或清空搜索。' : '可以关闭此窗口，直接上传本地图片。'}</p></div>}
      <Pagination current={offset / 12 + 1} total={total} pageSize={12} hideOnSinglePage showSizeChanger={false} disabled={busy || !!choosing || loading} onChange={page => setOffset((page - 1) * 12)}/>
    </div>
  </Dialog>;
}
