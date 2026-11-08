import { uploadTaskReference } from '@host/api/generations/references/requests';
import { errorMessage } from '@host/api/http';
import {
  combineAssetPromptReferences,
  remapAssetPromptReferences,
} from '@host/features/creation/assets/images/asset-prompt-references';
import type { useCreationDraft } from '@host/features/creation/shared/sessions/useCreationDraft';
import { Dialog } from '@host/shared/ui/Dialog';
import { PreviewImage } from '@host/shared/ui/ImagePreview';
import { StudioFeedback } from '@host/shared/ui/StudioFeedback';
import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { AssetRead } from '@host/types/creation/assets/management';
import type { ReferenceImage } from '@host/types/generations/references/requests';
import { Alert, Button, Checkbox, Popover, Spin, Upload } from 'antd';
import { Plus, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
export function PrivateReferenceImages({
  draft,
  disabled,
  onBusy,
  readonlyReferences = [],
  referencesReady = true,
  availableReferences = [],
  availableLoading = false,
  availableError = '',
  onRefreshAvailable,
  onImagesChange,
  linkedAssets,
}: {
  draft: ReturnType<typeof useCreationDraft>;
  disabled: boolean;
  onBusy?: (value: boolean) => void;
  readonlyReferences?: AssetPromptReference[];
  referencesReady?: boolean;
  availableReferences?: ReferenceImage[];
  availableLoading?: boolean;
  availableError?: string;
  onRefreshAvailable?: () => void;
  onImagesChange?: (images: AssetPromptReference[]) => void;
  linkedAssets: {
    assets: AssetRead[];
    selectedIds: string[];
    onChange: (id: string, selected: boolean) => void;
  };
}) {
  const [uploads, setUploads] = useState<ReferenceImage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [sourceMenu, setSourceMenu] = useState<string | null>(null);
  const [assetPickerOpen, setAssetPickerOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const readonly = useMemo(() => combineAssetPromptReferences(readonlyReferences, []), [readonlyReferences]);
  const readonlyIds = useMemo(() => new Set(readonly.map((item) => item.media_id)), [readonly]);
  const known = useMemo(
    () =>
      new Map(
        [
          ...availableReferences,
          ...(draft.saved?.reference_images ?? []).map((item) => ({
            ...item,
            name: '个人参考',
          })),
          ...uploads,
        ].map((item) => [item.media_id, item]),
      ),
    [availableReferences, draft.saved?.reference_images, uploads],
  );
  const images = useMemo(
    () =>
      combineAssetPromptReferences(
        readonly,
        draft.value.reference_media_ids.map((id) => ({
          media_id: id,
          url: known.get(id)?.url ?? null,
          name: known.get(id)?.name || '个人参考',
        })),
      ),
    [readonly, draft.value.reference_media_ids, known],
  );
  const count = images.length;
  const current = useRef({ draft, readonly, known, referencesReady });
  current.current = { draft, readonly, known, referencesReady };
  const loadedReadonly = useRef<{
    session: typeof draft.session;
    references: AssetPromptReference[];
  } | null>(null);
  useEffect(() => {
    if (!draft.loaded || !referencesReady) return;
    const previous = loadedReadonly.current;
    if (
      previous?.session === draft.session &&
      previous.references.map((item) => item.media_id).join(',') !== readonly.map((item) => item.media_id).join(',')
    ) {
      const value = draft.session.getSnapshot().value;
      const privateImages = value.reference_media_ids.map((id) => ({
        media_id: id,
        url: known.get(id)?.url ?? null,
        name: known.get(id)?.name || '个人参考',
      }));
      draft.edit({
        reference_media_ids: value.reference_media_ids,
        input_text: remapAssetPromptReferences(
          value.input_text,
          combineAssetPromptReferences(previous.references, privateImages),
          combineAssetPromptReferences(readonly, privateImages),
        ),
      });
    }
    loadedReadonly.current = { session: draft.session, references: readonly };
  }, [draft.loaded, draft.session, referencesReady, readonly, known]);
  useEffect(() => {
    if (referencesReady) onImagesChange?.(images);
  }, [images, referencesReady, onImagesChange]);
  const options = [...new Map(availableReferences.map((item) => [item.media_id, item])).values()];
  const unavailable = disabled || busy || !draft.loaded || !referencesReady;
  const uploadUnavailable = unavailable || count >= 16;
  function updateReferences(nextIds: string[], added?: ReferenceImage) {
    const latest = current.current;
    const value = latest.draft.session.getSnapshot().value;
    const metadata = new Map(latest.known);
    if (added) metadata.set(added.media_id, added);
    const resolve = (ids: string[]) =>
      combineAssetPromptReferences(
        latest.readonly,
        ids.map((id) => ({
          media_id: id,
          url: metadata.get(id)?.url ?? null,
          name: metadata.get(id)?.name || '个人参考',
        })),
      );
    latest.draft.edit({
      reference_media_ids: nextIds,
      input_text: remapAssetPromptReferences(value.input_text, resolve(value.reference_media_ids), resolve(nextIds)),
    });
  }
  function openPicker() {
    if (unavailable) return;
    setPicked([...draft.session.getSnapshot().value.reference_media_ids]);
    setPickerOpen(true);
  }
  async function upload(file: File) {
    if (uploadUnavailable || lock.current) return false;
    if (file.size > 20 * 1024 * 1024) {
      setError('参考图片不能超过 20 MiB。');
      return false;
    }
    lock.current = true;
    setBusy(true);
    onBusy?.(true);
    setError('');
    const session = draft.session;
    try {
      const image = await uploadTaskReference(file);
      if (!alive.current || session !== current.current.draft.session) return false;
      const next = [...new Set([...session.getSnapshot().value.reference_media_ids, image.media_id])];
      if (new Set([...current.current.readonly.map((item) => item.media_id), ...next]).size > 16) {
        setError('最多使用 16 张参考图片，请先移除一张个人参考图。');
        return false;
      }
      setUploads((items) => [...items, image]);
      updateReferences(next, image);
      setPickerOpen(false);
    } catch (cause) {
      if (alive.current) setError(errorMessage(cause));
    } finally {
      lock.current = false;
      if (alive.current) {
        setBusy(false);
        onBusy?.(false);
      }
    }
    return false;
  }
  return (
    <section className="standard-private-references" aria-label="参考图片">
      <div className="standard-reference-heading">
        <h4>参考图片</h4>
        <small className="standard-reference-count">{count} / 16</small>
      </div>
      {error && <StudioFeedback type="error" message={error} />}
      <div className="standard-reference-strip">
        {images.map((item, index) => {
          const linked = linkedAssets.assets.filter((asset) =>
            linkedAssets.selectedIds.includes(asset.id) && asset.state === 'confirmed' && asset.media_id === item.media_id,
          );
          const personal = draft.value.reference_media_ids.includes(item.media_id);
          const actions = [
            ...linked.map((asset) => ({
              key: `asset:${asset.id}`,
              label: `移除分镜图参考：${asset.name}`,
              remove: () => linkedAssets.onChange(asset.id, false),
            })),
            ...(personal ? [{
              key: 'personal',
              label: `移除个人参考图片 ${item.media_id}`,
              remove: () => updateReferences(
                draft.session.getSnapshot().value.reference_media_ids.filter((id) => id !== item.media_id),
              ),
            }] : []),
          ];
          const removeButton = (label: string, onClick: () => void) => (
            <Button className="standard-reference-remove" type="text" size="small" disabled={unavailable}
              aria-label={label} title={label} icon={<X size={12} />} onClick={onClick} />
          );
          return (
            <figure
              key={item.media_id}
              title={`${readonlyIds.has(item.media_id) ? '作品参考' : '个人参考'} · 图片${index + 1}`}
            >
              {item.url ? (
                <PreviewImage presentation="standard-creation" src={item.url} alt={`图片${index + 1} · ${item.name}`} />
              ) : (
                <span className="standard-reference-unavailable">预览待刷新</span>
              )}
              <figcaption>图片{index + 1}</figcaption>
              {actions.length === 1 && removeButton(actions[0].label, actions[0].remove)}
              {actions.length > 1 && (
                <Popover
                  trigger="click"
                  placement="bottomLeft"
                  open={sourceMenu === item.media_id}
                  onOpenChange={(open) => setSourceMenu(open ? item.media_id : null)}
                  content={<div className="shotReferenceMenu">
                    {actions.map((action) => (
                      <button key={action.key} type="button" onClick={() => {
                        setSourceMenu(null);
                        action.remove();
                      }}>{action.label}</button>
                    ))}
                  </div>}
                >
                  {removeButton(`参考图片 ${item.media_id} 的来源`, () => {})}
                </Popover>
              )}
            </figure>
          );
        })}
        {linkedAssets.assets.filter((asset) => linkedAssets.selectedIds.includes(asset.id) &&
          (asset.state !== 'confirmed' || !asset.media_id)).map((asset) => (
          <figure key={`asset:${asset.id}`} title={`${asset.name} · 缺图`}>
            <span className="standard-reference-unavailable">缺图</span>
            <Button className="standard-reference-remove" type="text" size="small" disabled={unavailable}
              aria-label={`移除分镜图参考：${asset.name}`} title={`移除分镜图参考：${asset.name}`}
              icon={<X size={12} />} onClick={() => linkedAssets.onChange(asset.id, false)} />
          </figure>
        ))}
        <Popover trigger="click" placement="bottomLeft" open={addMenuOpen} onOpenChange={setAddMenuOpen}
          content={<div className="shotReferenceMenu">
            <button type="button" onClick={() => { setAddMenuOpen(false); setAssetPickerOpen(true); }}>关联镜头素材</button>
            <button type="button" onClick={() => { setAddMenuOpen(false); openPicker(); }}>添加个人参考图片</button>
          </div>}>
          <Button className="standard-reference-add-tile" type="text" aria-label="添加参考图片" title="添加参考图片"
            disabled={unavailable} icon={<Plus size={16} />} />
        </Popover>
      </div>
      {assetPickerOpen && (
        <Dialog title="关联镜头素材" className="standard-creation-dialog" onClose={() => setAssetPickerOpen(false)}>
          <div className="shotAssetPicker">
            {linkedAssets.assets.map((asset) => {
              const selected = linkedAssets.selectedIds.includes(asset.id);
              const mediaId = asset.state === 'confirmed' ? asset.media_id : null;
              const full = count >= 16 && !!mediaId && !images.some((item) => item.media_id === mediaId);
              return <Checkbox key={asset.id} aria-label={`分镜图参考：${asset.name}`} checked={selected}
                disabled={unavailable || (!selected && full)} onChange={(event) => linkedAssets.onChange(asset.id, event.target.checked)}>
                {asset.name}{mediaId ? '' : ' · 缺图'}
              </Checkbox>;
            })}
          </div>
          <div className="production-dialog-footer"><Button onClick={() => setAssetPickerOpen(false)}>完成</Button></div>
        </Dialog>
      )}
      {pickerOpen && (
        <Dialog
          title="选择素材主图作为参考"
          className="standard-shot-reference-dialog storyboard-reference-dialog standard-creation-dialog"
          canClose={!busy}
          onClose={() => setPickerOpen(false)}
        >
          <div className="standard-shot-reference-picker">
            {availableLoading ? (
              <div role="status" className="standard-reference-picker-loading">
                <Spin />
                <span>正在载入素材主图…</span>
              </div>
            ) : availableError ? (
              <Alert
                type="error"
                message={availableError}
                action={
                  onRefreshAvailable && (
                    <Button loading={availableLoading} onClick={onRefreshAvailable}>
                      重新加载素材
                    </Button>
                  )
                }
              />
            ) : options.length ? (
              <div className="standard-reference-picker-grid">
                {options.map((item) => {
                  const selected = picked.includes(item.media_id);
                  const full = new Set([...readonlyIds, ...picked]).size >= 16;
                  return (
                    <article key={item.media_id}>
                      <PreviewImage presentation="standard-creation" src={item.url} alt={item.name} />
                      <Checkbox
                        aria-label={item.name}
                        checked={selected}
                        disabled={busy || (!selected && full && !readonlyIds.has(item.media_id))}
                        onChange={(event) =>
                          setPicked((ids) =>
                            event.target.checked
                              ? [...new Set([...ids, item.media_id])]
                              : ids.filter((id) => id !== item.media_id),
                          )
                        }
                      >
                        {item.name}
                      </Checkbox>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="studio-empty">
                <p>还没有素材主图。</p>
              </div>
            )}
            <p className="creation-help">最多 16 张；选择仅保存在你的生成设置中。</p>
            <Upload
              accept="image/png,image/jpeg,image/webp"
              showUploadList={false}
              beforeUpload={(file) => upload(file)}
              disabled={uploadUnavailable}
            >
              <Button loading={busy} disabled={uploadUnavailable} aria-label="上传本地参考图片">
                上传本地图片
              </Button>
            </Upload>
          </div>
          <div className="production-dialog-footer">
            <Button disabled={busy} onClick={() => setPickerOpen(false)}>
              取消
            </Button>
            <Button
              type="primary"
              disabled={
                disabled ||
                busy ||
                availableLoading ||
                !!availableError ||
                !draft.loaded ||
                !referencesReady ||
                new Set([...readonlyIds, ...picked]).size > 16
              }
              onClick={() => {
                updateReferences(picked);
                setPickerOpen(false);
              }}
            >
              确认选择
            </Button>
          </div>
        </Dialog>
      )}
    </section>
  );
}
