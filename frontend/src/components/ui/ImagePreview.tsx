import { useEffect, useId, useRef, useState, type ImgHTMLAttributes, type PointerEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Button, Select, Spin } from 'antd';
import { Dialog } from './Dialog';

export interface ImagePreviewProps {
  src: string;
  alt: string;
  title?: string;
  onClose: () => void;
  canClose?: boolean;
  footer?: ReactNode;
}

/** Mount while open. Native dialog keeps previews above drawers and other dialogs. */
export function ImagePreview(props: ImagePreviewProps) {
  return createPortal(
    <div onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
      <ImagePreviewContent key={props.src} {...props}/>
    </div>,
    document.body,
  );
}

function ImagePreviewContent({ src, alt, title = '图片预览', onClose, canClose = true, footer }: ImagePreviewProps) {
  const [status, setStatus] = useState<'loading' | 'loaded' | 'error'>('loading');
  const [originalSize, setOriginalSize] = useState(false);
  const [dimensions, setDimensions] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [magnifying, setMagnifying] = useState(false);
  const [zoom, setZoom] = useState(2);
  const [lens, setLens] = useState<{
    left: number; top: number; size: number;
    imageLeft: number; imageTop: number; imageWidth: number; imageHeight: number;
  } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const magnifierHintId = useId();

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(() => setLens(null));
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  function moveMagnifier(event: PointerEvent<HTMLDivElement>) {
    const image = imageRef.current;
    const stage = stageRef.current;
    if (!magnifying || status !== 'loaded' || !image || !stage
      || (event.pointerType === 'touch' && event.buttons === 0)) return;

    // object-fit: contain adds empty bands; sample only the actual image pixels.
    const bounds = image.getBoundingClientRect();
    const scale = Math.min(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    const x = event.clientX - bounds.left - (bounds.width - width) / 2;
    const y = event.clientY - bounds.top - (bounds.height - height) / 2;
    if (!width || !height || x < 0 || y < 0 || x > width || y > height) {
      setLens(null);
      return;
    }

    const viewport = stage.getBoundingClientRect();
    const size = Math.min(180, stage.clientWidth - 16, stage.clientHeight - 16);
    if (size <= 0) return;
    // Keep the lens inside the visible stage, even beside an edge or after scrolling.
    const left = Math.max(8, Math.min(event.clientX - viewport.left - size / 2, stage.clientWidth - size - 8));
    const top = Math.max(8, Math.min(event.clientY - viewport.top - size / 2, stage.clientHeight - size - 8));
    setLens({
      left: left + stage.scrollLeft, top: top + stage.scrollTop, size,
      imageLeft: size / 2 - x * zoom, imageTop: size / 2 - y * zoom,
      imageWidth: width * zoom, imageHeight: height * zoom,
    });
  }

  return <Dialog title={title} className="media-preview-dialog image-preview-dialog" canClose={canClose} onClose={onClose}>
    <div className="image-preview-toolbar">
      <span className="image-preview-caption" title={alt}>{alt}</span>
      <div className="image-preview-controls">
        {dimensions && <small>{dimensions}</small>}
        <Button size="small" type={magnifying ? 'primary' : 'default'} disabled={status !== 'loaded'} aria-pressed={magnifying}
          onClick={() => { setMagnifying((value) => !value); setLens(null); }}>
          放大镜
        </Button>
        {magnifying && <Select size="small" aria-label="放大镜倍率" value={zoom} className="image-magnifier-zoom"
          options={[{ value: 2, label: '2 倍' }, { value: 3, label: '3 倍' }, { value: 4, label: '4 倍' }]}
          onChange={(value) => { setZoom(value); setLens(null); }}/>}
        <Button size="small" disabled={status !== 'loaded'} aria-pressed={originalSize} onClick={() => { setOriginalSize((value) => !value); setLens(null); }}>
          {originalSize ? '适应窗口' : '原始尺寸'}
        </Button>
      </div>
    </div>
    {magnifying && <p id={magnifierHintId} className="image-magnifier-hint">移动鼠标查看局部细节；触屏可按住图片拖动。关闭放大镜后可滚动原图。</p>}
    <div ref={stageRef} className={`image-preview-stage${originalSize ? ' is-original' : ''}${magnifying ? ' is-magnifying' : ''}`}
      aria-busy={status === 'loading'} tabIndex={0} aria-label="图片预览区域，可滚动查看原图" aria-describedby={magnifying ? magnifierHintId : undefined}
      onPointerMove={moveMagnifier} onPointerLeave={() => setLens(null)} onPointerCancel={() => setLens(null)}
      onPointerDown={(event) => {
        if (!magnifying || status !== 'loaded') return;
        if (event.pointerType === 'touch') event.currentTarget.setPointerCapture(event.pointerId);
        moveMagnifier(event);
      }}
      onPointerUp={(event) => { if (event.pointerType === 'touch') setLens(null); }}
      onScroll={() => setLens(null)} onBlur={() => setLens(null)}>
      {status === 'loading' && <div className="image-preview-status" role="status"><Spin/><span>正在加载图片…</span></div>}
      {status === 'error' ? <div className="image-preview-status" role="alert">
        <p>图片暂时无法加载，链接可能已过期。</p>
        <Button onClick={() => { setStatus('loading'); setAttempt((value) => value + 1); }}>重新加载</Button>
        <small>仍无法查看时，请关闭预览并刷新所在页面。</small>
      </div> : <img ref={imageRef} key={attempt} className="image-preview-full" src={src} alt={alt} decoding="async" draggable={false}
        onLoad={(event) => {
          setStatus('loaded');
          setDimensions(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`);
        }}
        onError={() => { setStatus('error'); setDimensions(''); setLens(null); }}/>
      }
      {magnifying && lens && status === 'loaded' && <div className="image-magnifier-lens" aria-hidden="true"
        style={{ left: lens.left, top: lens.top, width: lens.size, height: lens.size }}>
        <img src={src} alt="" className="image-magnifier-detail" draggable={false}
          style={{ left: lens.imageLeft, top: lens.imageTop, width: lens.imageWidth, height: lens.imageHeight }}/>
        <span className="image-magnifier-crosshair"/>
        <span className="image-magnifier-badge">{zoom}×</span>
      </div>}
    </div>
    {footer && <div className="dialog-actions image-preview-actions">{footer}</div>}
  </Dialog>;
}

interface PreviewImageProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt' | 'onClick'> {
  src: string;
  alt: string;
  /** Class for the thumbnail button; className and style apply to the image. */
  triggerClassName?: string;
  previewTitle?: string;
}

/** Drop-in thumbnail with keyboard-accessible preview; do not nest in a button/link. */
export function PreviewImage({ src, alt, triggerClassName, previewTitle, loading = 'lazy', onError, onLoad, ...imageProps }: PreviewImageProps) {
  const [openSrc, setOpenSrc] = useState<string | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  return <>
    <button type="button" className={['image-preview-trigger', triggerClassName].filter(Boolean).join(' ')}
      aria-label={`预览${alt || '图片'}`} aria-haspopup="dialog" title="点击查看大图" disabled={!src}
      onClick={(event) => { event.stopPropagation(); setOpenSrc(src); }}>
      {failedSrc === src ? <span className="image-preview-unavailable">图片暂不可用，点击重试</span> : <img {...imageProps} src={src} alt={alt} loading={loading}
        onLoad={(event) => { setFailedSrc(null); onLoad?.(event); }}
        onError={(event) => { setFailedSrc(src); onError?.(event); }}/>}
    </button>
    {openSrc === src && !!src && <ImagePreview src={src} alt={alt} title={previewTitle} onClose={() => setOpenSrc(null)}/>}
  </>;
}
