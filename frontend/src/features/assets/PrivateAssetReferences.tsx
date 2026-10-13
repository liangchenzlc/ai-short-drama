import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Skeleton, Upload } from 'antd';
import { Plus, X } from 'lucide-react';
import { generationReferences, uploadTaskReference, type ReferenceImage } from '../../api/modules/generation-references';
import { creationDraftsApi, type UserCreationDraft } from '../../api/modules/creation-drafts';
import { errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';

export function PrivateAssetReferences({ assetId, projectId, episodeId, privateIds, saved, disabled, onChange, onState, onBusyChange }: {
  assetId: string; projectId: string; episodeId: string; privateIds: string[]; saved: UserCreationDraft | null; disabled: boolean;
  onChange: (ids: string[]) => void; onState: (state: { blocked: boolean; sharedIds: string[] }) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [shared, setShared] = useState<ReferenceImage[]>([]);
  const [privateImages, setPrivateImages] = useState<Record<string, { media_id: string; url: string | null }>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const alive = useRef(true);
  const lock = useRef(false);
  const ids = useRef(privateIds); ids.current = privateIds;
  const change = useRef(onChange); change.current = onChange;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const temporary = assetId.startsWith('draft:');
  useEffect(() => {
    if (temporary) { setLoading(false); return; }
    const controller = new AbortController(); setLoading(true);
    generationReferences('asset', assetId).list(controller.signal).then(result => {
      if (!controller.signal.aborted) { setShared(result.items); setError(''); }
    }).catch(cause => { if (!controller.signal.aborted) setError(`读取共享参考图失败：${errorMessage(cause)}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [assetId, revision, temporary]);
  useEffect(() => {
    if (saved?.reference_images) setPrivateImages(current => ({ ...current, ...Object.fromEntries(saved.reference_images!.map(item => [item.media_id, item])) }));
  }, [saved]);
  useEffect(() => { onState({ blocked: loading || busy || !!error, sharedIds: shared.map(item => item.media_id) }); }, [loading, busy, error, shared, onState]);

  const sharedIds = new Set(shared.map(item => item.media_id));
  const privateOnly = [...new Set(privateIds)].filter(id => !sharedIds.has(id));
  const count = new Set([...sharedIds, ...privateIds]).size;
  async function upload(file: File) {
    if (disabled || lock.current || count >= 16) return false;
    if (file.size > 20 * 1024 * 1024) { setError('参考图片不能超过 20 MiB。'); return false; }
    lock.current = true; setBusy(true); onBusyChange?.(true); setError('');
    try {
      const image = await uploadTaskReference(file);
      if (!alive.current) return false;
      setPrivateImages(current => ({ ...current, [image.media_id]: image }));
      change.current([...new Set([...ids.current, image.media_id])]);
    } catch (cause) { if (alive.current) setError(`上传参考图失败：${errorMessage(cause)}`); }
    finally { lock.current = false; if (alive.current) { setBusy(false); onBusyChange?.(false); } }
    return false;
  }
  async function refreshImages() {
    setError(''); setRevision(value => value + 1);
    if (temporary || !privateIds.length) return;
    try {
      const remote = await creationDraftsApi(projectId, episodeId, assetId, 'asset_image').get();
      if (remote?.reference_images) setPrivateImages(current => ({ ...current, ...Object.fromEntries(remote.reference_images!.map(item => [item.media_id, item])) }));
    } catch (cause) { setError(`刷新个人参考图失败：${errorMessage(cause)}`); }
  }
  return <section className="standard-asset-references" aria-label="参考图片">
    <header><span>参考图片 <small>{count}/16</small></span><Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} disabled={disabled || busy || loading || count >= 16} beforeUpload={upload}><Button type="text" size="small" aria-label="添加个人参考图片" title="添加个人参考图片" icon={<Plus size={16}/>} loading={busy} disabled={disabled || busy || loading || count >= 16}/></Upload></header>
    {loading ? <Skeleton active paragraph={{ rows: 1 }}/> : <div className="standard-reference-strip">
      {shared.map(item => <figure key={item.media_id}><PreviewImage src={item.url} alt={item.name || '共享参考图'}/><figcaption>作品参考</figcaption></figure>)}
      {privateOnly.map(id => <figure key={id}>{privateImages[id]?.url ? <PreviewImage src={privateImages[id].url!} alt="个人参考图"/> : <Button size="small" title={`媒体 ${id}`} onClick={() => void refreshImages()}>刷新预览</Button>}<Button className="standard-reference-remove" size="small" type="text" aria-label={`移除个人参考图片 ${id}`} disabled={disabled || busy} icon={<X size={12}/>} onClick={() => onChange(privateIds.filter(value => value !== id))}/></figure>)}
      {!count && <span className="standard-reference-empty">可添加个人参考图片</span>}
    </div>}
    {error && <Alert type="error" message={error} action={<Button size="small" onClick={() => void refreshImages()}>重试</Button>}/>}
  </section>;
}
