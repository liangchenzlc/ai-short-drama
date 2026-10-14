import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Spin, Upload } from 'antd';
import { Plus, X } from 'lucide-react';
import { uploadTaskReference, type ReferenceImage } from '../../api/modules/generation-references';
import { errorMessage } from '../../api/http';
import { Dialog } from '../../components/ui/Dialog';
import { PreviewImage } from '../../components/ui/ImagePreview';
import type { useCreationDraft } from './useCreationDraft';

export function PrivateReferenceImages({ draft, disabled, onBusy, readonlyReferences = [], availableReferences = [], availableLoading = false, availableError = '', onRefreshAvailable }: {
  draft: ReturnType<typeof useCreationDraft>; disabled: boolean; onBusy?: (value: boolean) => void;
  readonlyReferences?: ReferenceImage[]; availableReferences?: ReferenceImage[]; availableLoading?: boolean;
  availableError?: string; onRefreshAvailable?: () => void;
}) {
  const [uploads, setUploads] = useState<ReferenceImage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const readonly = [...new Map(readonlyReferences.map(item => [item.media_id, item])).values()];
  const readonlyIds = new Set(readonly.map(item => item.media_id));
  const privateIds = [...new Set(draft.value.reference_media_ids)].filter(id => !readonlyIds.has(id));
  const count = new Set([...readonlyIds, ...draft.value.reference_media_ids]).size;
  const urls = new Map([...availableReferences, ...(draft.saved?.reference_images ?? []), ...uploads].map(item => [item.media_id, item.url]));
  const options = [...new Map(availableReferences.map(item => [item.media_id, item])).values()];
  const unavailable = disabled || busy || !draft.loaded || count >= 16;
  function openPicker() {
    if (unavailable) return;
    setPicked([...draft.value.reference_media_ids]); setPickerOpen(true);
  }
  async function upload(file: File) {
    if (disabled || lock.current || count >= 16) return false;
    if (file.size > 20 * 1024 * 1024) { setError('参考图片不能超过 20 MiB。'); return false; }
    lock.current = true; setBusy(true); onBusy?.(true); setError('');
    try {
      const image = await uploadTaskReference(file);
      if (!alive.current) return false;
      const next = [...new Set([...draft.session.getSnapshot().value.reference_media_ids, image.media_id])];
      if (new Set([...readonlyIds, ...next]).size > 16) { setError('最多使用 16 张参考图片，请先移除一张个人参考图。'); return false; }
      setUploads(items => [...items, image]);
      draft.edit({ reference_media_ids: next });
      setPickerOpen(false);
    } catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) { setBusy(false); onBusy?.(false); } }
    return false;
  }
  return <section className="standard-private-references" aria-label="个人参考图片">
    <div className="standard-reference-heading"><h4>参考图片</h4><small className="standard-reference-count">{count} / 16</small></div>
    {error && <Alert type="error" message={error}/>}
    <div className="standard-reference-strip">
      {readonly.map(item => <figure key={item.media_id} title={`${item.name} · 作品参考`}>
        <PreviewImage src={item.url} alt={item.name || '作品参考图'}/>
      </figure>)}
      {privateIds.map(id => <figure key={id}>
        {urls.get(id) ? <PreviewImage src={urls.get(id)!} alt="个人生成参考图"/> : <span className="standard-reference-unavailable">预览待刷新</span>}
        <Button className="standard-reference-remove" type="text" size="small" disabled={disabled || busy} aria-label="移除个人参考图片" title="移除个人参考图片" icon={<X size={12}/>} onClick={() => draft.edit({ reference_media_ids: draft.value.reference_media_ids.filter(item => item !== id) })}/>
      </figure>)}
      <Button className={count ? 'standard-reference-add-tile' : 'standard-reference-empty'} type="text" aria-label="添加参考图片" title="添加参考图片" disabled={unavailable} icon={<Plus size={16}/>} onClick={openPicker}>{!count && '添加参考图片'}</Button>
    </div>
    {pickerOpen && <Dialog title="选择已采用的参考素材" className="standard-shot-reference-dialog" canClose={!busy} onClose={() => setPickerOpen(false)}>
      <div className="standard-shot-reference-picker">
        {availableLoading ? <div role="status" className="standard-reference-picker-loading"><Spin/><span>正在载入已采用素材…</span></div> : availableError ? <Alert type="error" message={availableError} action={onRefreshAvailable && <Button loading={availableLoading} onClick={onRefreshAvailable}>重新加载素材</Button>}/> : options.length ? <div className="standard-reference-picker-grid">{options.map(item => {
          const selected = readonlyIds.has(item.media_id) || picked.includes(item.media_id);
          const full = new Set([...readonlyIds, ...picked]).size >= 16;
          return <article key={item.media_id}><PreviewImage src={item.url} alt={item.name}/><Checkbox aria-label={item.name} checked={selected} disabled={busy || readonlyIds.has(item.media_id) || !selected && full}
            onChange={event => setPicked(ids => event.target.checked ? [...new Set([...ids, item.media_id])] : ids.filter(id => id !== item.media_id))}>{item.name}</Checkbox></article>;
        })}</div> : <div className="studio-empty"><p>还没有已采用的素材图片。</p></div>}
        <p className="episode-help">最多 16 张；选择仅保存在你的生成设置中。</p>
        <Upload accept="image/png,image/jpeg,image/webp" showUploadList={false} beforeUpload={file => upload(file)} disabled={unavailable}>
          <Button loading={busy} disabled={unavailable} aria-label="上传本地参考图片">上传本地图片</Button>
        </Upload>
      </div>
      <div className="production-dialog-footer"><Button disabled={busy} onClick={() => setPickerOpen(false)}>取消</Button><Button type="primary" disabled={disabled || busy || availableLoading || !!availableError || !draft.loaded || new Set([...readonlyIds, ...picked]).size > 16} onClick={() => { draft.edit({ reference_media_ids: picked }); setPickerOpen(false); }}>确认选择</Button></div>
    </Dialog>}
  </section>;
}
