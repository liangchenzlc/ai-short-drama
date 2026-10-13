import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Upload } from 'antd';
import { uploadTaskReference, type ReferenceImage } from '../../api/modules/generation-references';
import { errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';
import type { useCreationDraft } from './useCreationDraft';

export function PrivateReferenceImages({ draft, disabled, onBusy }: {
  draft: ReturnType<typeof useCreationDraft>; disabled: boolean; onBusy?: (value: boolean) => void;
}) {
  const [uploads, setUploads] = useState<ReferenceImage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const urls = new Map([...(draft.saved?.reference_images ?? []), ...uploads].map(item => [item.media_id, item.url]));
  async function upload(file: File) {
    if (disabled || lock.current) return false;
    if (file.size > 20 * 1024 * 1024) { setError('参考图片不能超过 20 MiB。'); return false; }
    lock.current = true; setBusy(true); onBusy?.(true); setError('');
    try {
      const image = await uploadTaskReference(file);
      if (!alive.current) return false;
      setUploads(items => [...items, image]);
      draft.edit({ reference_media_ids: [...draft.session.getSnapshot().value.reference_media_ids, image.media_id] });
    } catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) { setBusy(false); onBusy?.(false); } }
    return false;
  }
  return <section className="standard-private-references" aria-label="个人参考图片">
    <div className="standard-reference-heading"><h4>参考图片 · {draft.value.reference_media_ids.length}/16</h4>
      <Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} beforeUpload={file => upload(file)} disabled={disabled || busy || !draft.loaded || draft.value.reference_media_ids.length >= 16}>
        <Button size="small" loading={busy} disabled={disabled || busy || !draft.loaded || draft.value.reference_media_ids.length >= 16}>添加参考图片</Button>
      </Upload>
    </div>
    {error && <Alert type="error" message={error}/>}
    <div className="reference-image-strip">{draft.value.reference_media_ids.map(id => <figure key={id}>
      {urls.get(id) ? <PreviewImage src={urls.get(id)!} alt="个人生成参考图"/> : <span>预览待刷新</span>}
      <Button size="small" disabled={disabled || busy} aria-label="移除个人参考图片" onClick={() => draft.edit({ reference_media_ids: draft.value.reference_media_ids.filter(item => item !== id) })}>移除</Button>
    </figure>)}</div>
  </section>;
}
