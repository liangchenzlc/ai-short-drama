import { useEffect, useState } from 'react';
import { Alert, Button, Input, Segmented, Skeleton } from 'antd';
import { Dialog } from '../../components/ui/Dialog';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { assetLibraries, type LibraryAssetRead } from '../../api/modules/assets';
import { mediaLibrary } from '../../api/modules/media-library';
import { errorMessage } from '../../api/http';
import type { MediaAsset } from '../../api/types/generations';

type Kind = 'asset' | 'image' | 'video' | 'audio';
export function AgentAssetPicker({ projectId, disabled, onSelect, onClose, dialogClassName }: {
  projectId: string; disabled: boolean; dialogClassName?: string;
  onSelect: (type: 'media' | 'asset', id: string) => Promise<void>; onClose: () => void;
}) {
  const [kind, setKind] = useState<Kind>('asset');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<(MediaAsset | LibraryAssetRead)[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const request = kind === 'asset'
      ? assetLibraries.list({ kind: 'project', projectId }, { q: search, offset, limit: 20 }, controller.signal)
      : mediaLibrary.list({ media_type: kind, name: search || undefined, offset, limit: 20 }, controller.signal);
    request.then(page => {
      if (!controller.signal.aborted) { setItems(page.items); setTotal(page.total); }
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, kind, search, offset, revision]);
  return <Dialog title="添加资产上下文" className={["agent-context-dialog", dialogClassName].filter(Boolean).join(" ")} canClose={!disabled} onClose={onClose}>
    <div className="agent-context-dialog-body">
      <Segmented aria-label="资产类型" value={kind} disabled={disabled}
        options={[{ value: 'asset', label: '项目素材' }, { value: 'image', label: '图片' }, { value: 'video', label: '视频' }, { value: 'audio', label: '音频' }]}
        onChange={value => { setKind(value as Kind); setOffset(0); setItems([]); }}/>
      <form className="agent-context-search" onSubmit={event => { event.preventDefault(); setSearch(query.trim()); setOffset(0); }}>
        <label className="sr-only" htmlFor="agent-asset-search">搜索资产名称</label>
        <Input id="agent-asset-search" value={query} disabled={disabled} placeholder="搜索名称" onChange={event => setQuery(event.target.value)}/>
        <Button htmlType="submit" disabled={disabled}>搜索</Button>
      </form>
      <p className="agent-context-help">添加为本次对话资料，素材与媒体的原有归属保持不变。</p>
      {error ? <Alert type="error" message={error} action={<Button size="small" onClick={() => setRevision(value => value + 1)}>重试</Button>}/>
        : loading ? <Skeleton active paragraph={{ rows: 4 }}/>
          : items.length ? <div className="agent-asset-options">{items.map(item => {
            const asset = 'id' in item ? item : null;
            const media = 'asset_id' in item ? item : null;
            const url = asset?.image?.url ?? media?.url;
            return <article key={asset?.id ?? media?.asset_id}>
              {url && (asset || media?.media_type === 'image') ? <PreviewImage src={url} alt={item.name}/>
                : url && media?.media_type === 'video' ? <video src={url} controls preload="metadata" aria-label={`${item.name}预览`}/>
                  : url && media?.media_type === 'audio' ? <audio src={url} controls preload="metadata" aria-label={`${item.name}试听`}/> : null}
              <div><strong>{item.name}</strong>{asset && <p>{asset.description || '暂无描述'}</p>}</div>
              <Button disabled={disabled} onClick={() => void onSelect(asset ? 'asset' : 'media', asset?.id ?? media!.media_id)}>添加</Button>
            </article>;
          })}</div> : <p className="agent-context-empty">没有可添加的资产，先创建素材或生成媒体。</p>}
      {total > 20 && <div className="agent-context-pagination"><Button disabled={disabled || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</Button><span>{offset + 1}-{Math.min(offset + 20, total)} / {total}</span><Button disabled={disabled || loading || offset + 20 >= total} onClick={() => setOffset(value => value + 20)}>下一页</Button></div>}
    </div>
  </Dialog>;
}
