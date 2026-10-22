import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dropdown, Skeleton } from 'antd';
import { StudioFeedback } from '../../components/ui/StudioFeedback';
import { Plus, X } from 'lucide-react';
import { generationReferences, uploadTaskReference, type ReferenceImage } from '../../api/modules/generation-references';
import { creationDraftsApi, type UserCreationDraft } from '../../api/modules/creation-drafts';
import { errorMessage } from '../../api/http';
import { PreviewImage } from '../../components/ui/ImagePreview';
import { ImagePicker } from '../media-library/ImagePicker';
import { getAttemptAccount } from '../generations/attempt';
import { combineAssetPromptReferences, type AssetPromptReference } from './asset-prompt-references';

export type AssetReferenceChange = { before: readonly AssetPromptReference[]; after: readonly AssetPromptReference[] };

export function PrivateAssetReferences({ assetId, projectId, privateIds, saved, disabled, onChange, onState, onBusyChange, onImagesChange }: {
  assetId: string; projectId: string; privateIds: string[]; saved: UserCreationDraft | null; disabled: boolean;
  onChange: (ids: string[], references: AssetReferenceChange) => void; onState: (state: { blocked: boolean; sharedIds: string[] }) => void;
  onBusyChange?: (busy: boolean) => void;
  onImagesChange?: (images: AssetPromptReference[]) => void;
}) {
  const [shared, setShared] = useState<ReferenceImage[]>([]);
  const [privateImages, setPrivateImages] = useState<Record<string, { media_id: string; url: string | null }>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const alive = useRef(true);
  const lock = useRef(false);
  const ids = useRef(privateIds); ids.current = privateIds;
  const change = useRef(onChange); change.current = onChange;
  const loadedSharedFor = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const temporary = assetId.startsWith('draft:');
  useEffect(() => {
    if (temporary) { setLoading(false); return; }
    const controller = new AbortController(); setLoading(true);
    generationReferences('asset', assetId).list(controller.signal).then(result => {
      if (!controller.signal.aborted) {
        const current = currentReferences.current;
        const nextShared = result.items.map(item => ({ media_id: item.media_id, url: item.url, name: item.name || '作品参考' }));
        if (loadedSharedFor.current === assetId && current.shared.map(item => item.media_id).join(',') !== nextShared.map(item => item.media_id).join(',')) {
          const known = new Map(current.images.map(item => [item.media_id, item]));
          const after = combineAssetPromptReferences(nextShared, ids.current.map(id => known.get(id) ?? { media_id: id, url: null, name: '个人参考' }));
          change.current(ids.current, { before: current.images, after });
        }
        loadedSharedFor.current = assetId;
        setShared(result.items); setError('');
      }
    }).catch(cause => { if (!controller.signal.aborted) setError(`读取共享参考图失败：${errorMessage(cause)}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [assetId, revision, temporary]);
  useEffect(() => {
    if (saved?.reference_images) setPrivateImages(current => ({ ...current, ...Object.fromEntries(saved.reference_images!.map(item => [item.media_id, item])) }));
  }, [saved]);
  useEffect(() => { onState({ blocked: loading || busy || !!error, sharedIds: shared.map(item => item.media_id) }); }, [loading, busy, error, shared, onState]);

  const sharedReferences = useMemo(() => shared.map(item => ({ media_id: item.media_id, url: item.url, name: item.name || '作品参考' })), [shared]);
  const images = useMemo(() => combineAssetPromptReferences(sharedReferences, privateIds.map(id => ({ media_id: id, url: privateImages[id]?.url ?? null, name: '个人参考' }))), [sharedReferences, privateIds, privateImages]);
  const currentReferences = useRef({ shared: sharedReferences, images }); currentReferences.current = { shared: sharedReferences, images };
  useEffect(() => { if (!loading) onImagesChange?.(images); }, [images, loading, onImagesChange]);
  const sharedIds = new Set(shared.map(item => item.media_id));
  const count = images.length;
  function updateReferences(nextIds: string[], added?: AssetPromptReference) {
    const current = currentReferences.current;
    const known = new Map(current.images.map(image => [image.media_id, image]));
    if (added) known.set(added.media_id, added);
    const after = combineAssetPromptReferences(current.shared, nextIds.map(id => known.get(id) ?? { media_id: id, url: null, name: '个人参考' }));
    change.current(nextIds, { before: current.images, after });
  }
  async function upload(file: File) {
    if (disabled || lock.current || count >= 16) return false;
    if (file.size > 20 * 1024 * 1024) { setError('参考图片不能超过 20 MiB。'); return false; }
    lock.current = true; setBusy(true); onBusyChange?.(true); setError('');
    const actor = getAttemptAccount();
    try {
      const image = await uploadTaskReference(file);
      if (actor !== getAttemptAccount()) return false;
      if (alive.current) setPrivateImages(current => ({ ...current, [image.media_id]: image }));
      updateReferences([...new Set([...ids.current, image.media_id])], { ...image, name: '个人参考' });
    } catch (cause) { if (alive.current) setError(`上传参考图失败：${errorMessage(cause)}`); }
    finally { lock.current = false; if (alive.current) setBusy(false); onBusyChange?.(false); }
    return false;
  }
  async function refreshImages() {
    setError(''); setRevision(value => value + 1);
    if (temporary || !privateIds.length) return;
    try {
      const remote = await creationDraftsApi(projectId, assetId, 'asset_image').get();
      if (remote?.reference_images) setPrivateImages(current => ({ ...current, ...Object.fromEntries(remote.reference_images!.map(item => [item.media_id, item])) }));
    } catch (cause) { setError(`刷新个人参考图失败：${errorMessage(cause)}`); }
  }
  const canAdd = !disabled && !busy && !loading && count < 16;
  const addReference = <Dropdown overlayClassName="standard-creation-popup" trigger={['click']} menu={{ items: [
    { key: 'upload', label: '上传本地参考图片', onClick: () => fileInput.current?.click() },
    { key: 'library', label: '从资产库选择参考图片', onClick: () => setPickerOpen(true) },
  ] }} disabled={!canAdd}>
    <Button type="text" className="standard-reference-add-tile" aria-label="添加个人参考图片" title="添加参考图片" icon={<Plus size={16}/>} loading={busy} disabled={!canAdd}/>
  </Dropdown>;
  return <section className="standard-asset-references" aria-label="参考图片">
    <header><span>参考图片</span><small>{count} / 16</small></header>
    <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={!canAdd} onChange={event => {
      const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
      if (file) void upload(file);
    }}/>
    {loading ? <div className="standard-reference-loading" role="status" aria-label="正在读取参考图片"><Skeleton active title={false} paragraph={{ rows: 1 }}/></div> : <div className="standard-reference-strip">
      {images.map((item, index) => <figure key={item.media_id} title={`${sharedIds.has(item.media_id) ? '作品参考' : '个人参考'} · 图片${index + 1}`}>
        {item.url ? <PreviewImage presentation="standard-creation" src={item.url} alt={`图片${index + 1} · ${item.name}`}/> : <Button size="small" title={`媒体 ${item.media_id}`} onClick={() => void refreshImages()}>刷新预览</Button>}
        <figcaption>图片{index + 1}</figcaption>
        {!sharedIds.has(item.media_id) && <Button className="standard-reference-remove" size="small" type="text" aria-label={`移除个人参考图片 ${item.media_id}`} disabled={disabled || busy} icon={<X size={12}/>} onClick={() => updateReferences(ids.current.filter(value => value !== item.media_id))}/>}
      </figure>)}
      {addReference}
    </div>}
    {error && <StudioFeedback type="error" title="素材参考图片" noticeKey={`asset-references:${assetId}`} message={error} actions={<Button size="small" onClick={() => void refreshImages()}>重试</Button>}/>}
    {pickerOpen && <ImagePicker purpose="reference" presentation="standard-creation" busy={busy} projectId={projectId || undefined} onClose={() => setPickerOpen(false)} onSelect={async (mediaId, media) => {
      if (!canAdd || lock.current) return false;
      setPrivateImages(current => ({ ...current, [mediaId]: { media_id: mediaId, url: media.url } }));
      updateReferences([...new Set([...ids.current, mediaId])], { media_id: mediaId, url: media.url, name: '个人参考' });
      return true;
    }}/>}
  </section>;
}
