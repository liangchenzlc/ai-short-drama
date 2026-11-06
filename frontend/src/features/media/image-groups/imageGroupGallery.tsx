import { Dialog } from '@host/shared/ui/Dialog';
import { ImagePreview } from '@host/shared/ui/ImagePreview';
import type { ImageGroup, ImageMedia } from '@host/types/media/image-groups';
import { Button } from 'antd';
import { Expand, Image as ImageIcon } from 'lucide-react';
import { useState } from 'react';
import './imageGroupGallery.css';

export function ImageGroupGallery({ group, adoptedImage, previewMediaId, onPreview, onAdopt, onRetryItem }: {
  group: ImageGroup | null;
  adoptedImage: ImageMedia | null;
  previewMediaId: string | null;
  onPreview: (mediaId: string) => void;
  onAdopt: (mediaId: string) => void;
  onRetryItem?: (itemId: string) => void;
}) {
  const [zoom, setZoom] = useState(false);
  const [compare, setCompare] = useState(false);
  const items = group?.items ?? [];
  const selected = items.find((item) => item.media_id === previewMediaId)
    ?? (adoptedImage?.media_id === previewMediaId ? adoptedImage : null)
    ?? items.find((item) => item.media_id === adoptedImage?.media_id)
    ?? items.find((item) => item.status === 'ready')
    ?? adoptedImage;
  const selectedIndex = items.findIndex((item) => item.media_id === selected?.media_id);
  const isPrimary = (id: string | null) => id !== null && id === adoptedImage?.media_id;
  const separatePrimary = !!adoptedImage && !items.some((item) => item.media_id === adoptedImage.media_id);

  return (
    <div className="imageGroupGallery">
      <div className="imageGalleryCanvas">
        {selected?.url ? <>
          <img key={selected.media_id ?? selected.url} src={selected.url} alt={'当前查看图片 ' + (selectedIndex + 1 || 1)} />
          {items.length === 1 && isPrimary(selected.media_id) && <span className="imageGalleryPrimary">主图</span>}
          <Button className="imageGalleryZoom" type="text" aria-label="放大图片" icon={<Expand size={15} />}
            onClick={() => setZoom(true)} />
        </> : <div className="imageGalleryEmpty"><ImageIcon size={25} /><span>暂无图片</span></div>}
      </div>
      <div className="imageGalleryControls">
        <div className="imageGalleryThumbs">
          {(items.length > 1 || separatePrimary) && items.map((item, index) => <button type="button" key={item.id}
            className={item.media_id === selected?.media_id ? 'isSelected' : ''}
            disabled={item.status === 'generating' || (item.status === 'failed' ? !onRetryItem : !item.media_id)}
            title={item.error ?? undefined}
            aria-label={item.status === 'failed' && onRetryItem ? '重试图片 ' + (index + 1) :
              item.status === 'generating' ? '图片 ' + (index + 1) + '，生成中' :
                isPrimary(item.media_id) ? '图片 ' + (index + 1) + '，主图' : '查看图片 ' + (index + 1)}
            onClick={() => item.status === 'failed' ? onRetryItem?.(item.id) : item.media_id && onPreview(item.media_id)}>
            {item.url ? <img src={item.url} alt="" /> : <span>{item.status === 'failed' ? '失败 · 重试' : '生成中'}</span>}
            {isPrimary(item.media_id) && <i>主图</i>}
          </button>)}
          {adoptedImage && separatePrimary &&
            <button type="button" aria-label="查看当前主图" onClick={() => onPreview(adoptedImage.media_id)}>
              {adoptedImage.url && <img src={adoptedImage.url} alt="" />}<i>主图</i>
            </button>}
        </div>
        <div className="imageGalleryActions">
          {items.length > 1 && <Button type="text" size="small" onClick={() => setCompare(true)}>查看全部</Button>}
          {selected?.media_id && !isPrimary(selected.media_id) &&
            <Button size="small" onClick={() => onAdopt(selected.media_id!)}>设为主图</Button>}
        </div>
      </div>
      {zoom && selected?.url && <ImagePreview presentation="standard-creation" src={selected.url}
        alt="图片预览" onClose={() => setZoom(false)} />}
      {compare && <Dialog title="全部图片" onClose={() => setCompare(false)}>
        <div className="imageGalleryCompare">{items.map((item, index) =>
          <button type="button" key={item.id} disabled={!item.media_id || !item.url}
            onClick={() => { if (item.media_id) onPreview(item.media_id); setCompare(false); }}>
            {item.url ? <img src={item.url} alt={'图片 ' + (index + 1)} /> : <span>{item.status === 'failed' ? '生成失败' : '生成中'}</span>}
            <span>图片 {index + 1}{isPrimary(item.media_id) ? ' · 主图' : ''}</span>
          </button>)}</div>
      </Dialog>}
    </div>
  );
}