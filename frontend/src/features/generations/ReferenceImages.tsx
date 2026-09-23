import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Skeleton, Upload } from 'antd';
import { generationReferences, type ReferenceImage } from '../../api/modules/generation-references';
import { errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';

export function ReferenceImages({ kind, ownerId, version, disabled, beforeChange, onChanged, onBusyChange, onLoaded }: {
  kind: 'asset' | 'shot'; ownerId: string; version: string; disabled: boolean;
  beforeChange: () => Promise<{ row_version: string; release?: () => void } | null>;
  onChanged: (version: string) => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  onLoaded?: (items: ReferenceImage[]) => void;
}) {
  const [items, setItems] = useState<ReferenceImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const lock = useRef(false);
  const callbacks = useRef({ onLoaded }); callbacks.current = { onLoaded };
  const api = generationReferences(kind, ownerId);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    api.list(controller.signal).then(result => { if (!controller.signal.aborted) { setItems(result.items); callbacks.current.onLoaded?.(result.items); setError(''); } })
      .catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [kind, ownerId, version, revision]);
  async function change(file?: File, mediaId?: string) {
    if (disabled || lock.current) return false;
    if (file && file.size > 20 * 1024 * 1024) { setError('参考图片不能超过 20 MiB。'); return false; }
    lock.current = true; setBusy(true); onBusyChange?.(true); setError('');
    let release: (() => void) | undefined;
    try {
      const saved = await beforeChange(); release = saved?.release;
      if (!saved) { setError('请先完成内容保存，再修改参考图片。'); return false; }
      const result = file ? await api.upload(file, saved.row_version) : await api.remove(mediaId!, saved.row_version);
      setItems(result.items); callbacks.current.onLoaded?.(result.items);
      await onChanged(result.row_version);
    } catch (cause) { setError(errorMessage(cause)); }
    finally { release?.(); lock.current = false; setBusy(false); onBusyChange?.(false); }
    return false;
  }
  return <section className="generation-reference-images" aria-label="上传的参考图片">
    <div className="reference-images-heading"><h4>参考图片</h4><Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} disabled={disabled || busy || loading || !!error || items.length >= 16} beforeUpload={file => change(file)}><Button disabled={disabled || busy || loading || !!error || items.length >= 16} loading={busy}>添加参考图片</Button></Upload></div>
    <p className="episode-help">上传图片用于指导生成，保存后可重复使用。支持 PNG、JPEG、WebP，每张最多 20 MB。</p>
    {error && <Alert type="error" message={error} action={<Button disabled={busy} onClick={() => setRevision(value => value + 1)}>重新加载</Button>}/>}
    {loading && !items.length ? <Skeleton active paragraph={{ rows: 1 }}/> : <div className="reference-image-strip">{items.map(item => <figure key={item.media_id}><PreviewImage src={item.url} alt={item.name || '生成参考图'}/><figcaption title={item.name}>{item.name || '参考图片'}</figcaption><Button size="small" disabled={disabled || busy} onClick={() => void change(undefined, item.media_id)}>移除</Button></figure>)}</div>}
  </section>;
}
