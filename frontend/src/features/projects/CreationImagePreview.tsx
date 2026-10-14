import { useEffect, useState, type ReactNode } from 'react';
import { Button, Spin } from 'antd';
import { FullscreenOutlined, MinusOutlined, PictureOutlined, PlusOutlined } from '@ant-design/icons';
import { ImagePreview } from '../../components/ui/ImagePreview';

export function CreationImagePreview({ src, alt, loading = false, empty, onRefresh, onOpen }: {
  src?: string | null; alt: string; loading?: boolean; empty?: ReactNode;
  onRefresh?: () => void; onOpen?: () => void;
}) {
  const [zoom, setZoom] = useState(100);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [previewOpen, setPreviewOpen] = useState(false);
  useEffect(() => { setZoom(100); setFailed(false); setPreviewOpen(false); }, [src]);
  const openPreview = () => { if (onOpen) onOpen(); else setPreviewOpen(true); };
  const refresh = () => { setFailed(false); setAttempt(value => value + 1); onRefresh?.(); };
  const available = !!src && !failed && !loading;
  return <div className="creation-image-preview" aria-label={`${alt}预览区域`}>
    {loading ? <div className="creation-preview-state" role="status"><Spin/><span>正在读取预览…</span></div>
      : failed ? <div className="creation-preview-state" role="alert"><PictureOutlined/><strong>图片预览加载失败</strong><p>请重新读取预览链接后重试。</p>{onRefresh && <Button size="small" onClick={refresh}>刷新图片预览</Button>}</div>
        : src ? onOpen ? <button type="button" className="creation-preview-image-button" aria-label={`预览${alt}`} onClick={onOpen}><img key={attempt} src={src} alt={alt} style={{ transform: `scale(${zoom / 100})` }} onError={() => setFailed(true)}/></button>
          : <img key={attempt} src={src} alt={alt} style={{ transform: `scale(${zoom / 100})` }} onError={() => setFailed(true)}/>
          : <div className="creation-preview-state"><PictureOutlined/>{empty ?? <><strong>还没有采用图片</strong><p>生成或选择图片，核对后明确采用。</p></>}</div>}
    {available && <div className="creation-preview-tools" role="group" aria-label="图片预览工具">
      <Button type="text" aria-label="缩小预览" disabled={zoom <= 50} icon={<MinusOutlined/>} onClick={() => setZoom(value => Math.max(50, value - 10))}/>
      <span aria-live="polite">{zoom}%</span>
      <Button type="text" aria-label="放大预览" disabled={zoom >= 200} icon={<PlusOutlined/>} onClick={() => setZoom(value => Math.min(200, value + 10))}/>
      <Button type="text" aria-label="适合窗口" icon={<PictureOutlined/>} onClick={() => setZoom(100)}/>
      <Button type="text" aria-label="全屏预览" icon={<FullscreenOutlined/>} onClick={openPreview}/>
    </div>}
    {previewOpen && src && <ImagePreview src={src} alt={alt} onClose={() => setPreviewOpen(false)}/>}
  </div>;
}
