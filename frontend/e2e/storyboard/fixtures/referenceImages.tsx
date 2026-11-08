import '@host/app/styles/index.css';
import '@host/features/creation/storyboard/list/index.css';
import '@host/features/creation/workspace/layout/overlays.css';
import '@ant-design/v5-patch-for-react-19';
import { PrivateReferenceImages } from '@host/features/creation/shared/references/PrivateReferenceImages';
import { CreationDraftSession } from '@host/features/creation/shared/sessions/creation-draft-session';
import type { AssetRead } from '@host/types/creation/assets/management';
import type { UserCreationDraft } from '@host/types/creation/drafts/requests';
import { ConfigProvider } from 'antd';
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';

const image = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="40" height="40"%3E%3Crect width="40" height="40" fill="%23796b63"/%3E%3C/svg%3E';
const asset = (id: string, name: string, mediaId: string | null, state: AssetRead['state'] = 'confirmed'): AssetRead => ({
  id, name, kind: 'character', label: '', description: '', profile: {}, prompt: '', tags: [], scene_time: '', state,
  row_version: '1', media_id: mediaId, image: mediaId ? { media_id: mediaId, url: image } : null,
  reference_count: 0, created_at: null, updated_at: null,
});
const assets = [asset('501', '林晚', '9901'), asset('502', '雨夜车站', '9902'),
  asset('503', '未确认道具', '9904', 'unconfirmed'), asset('504', '同图角色', '9901'), asset('505', '无图布景', null)];
const available = [
  { media_id: '9901', name: '林晚', url: image },
  { media_id: '9903', name: '私人参考', url: image },
  { media_id: '9905', name: '可添加图片', url: image },
];
const initial: UserCreationDraft = {
  id: '95001', project_id: '10', object_id: '101', purpose: 'storyboard_image', model_config_id: '77',
  input_text: '关联 @图片1 共享 @图片2 个人 @图片3', image_settings: { aspect: '16:9', resolution: '2K', count: 1 },
  video_settings: null, reference_media_ids: ['9901', '9903'], reference_images: available.filter(item => item.media_id !== '9905'),
  row_version: '1', draft_version: '1', source_version: '1',
};
const parameters = new URLSearchParams(window.location.search);

function Fixture() {
  const [selectedIds, setSelectedIds] = useState(['501', '503']);
  const [calls, setCalls] = useState<string[]>([]);
  const [session] = useState(() => {
    let saved = parameters.has('full') ? {
      ...initial, reference_media_ids: Array.from({ length: 14 }, (_, index) => String(2000 + index)),
    } : initial;
    return new CreationDraftSession({
      get: async () => saved,
      create: async value => (saved = { ...saved, ...value }),
      update: async (_version, value) => (saved = { ...saved, ...value }),
    }, { read: () => null, save: () => {}, clear: () => {} }, initial);
  });
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => { void session.load(); }, [session]);
  const draft = { ...state, session, edit: session.edit, flush: session.flush, retry: session.retry, reload: () => session.load(true) };
  const readonlyReferences = useMemo(() => [
    ...assets.filter(item => selectedIds.includes(item.id) && item.state === 'confirmed' && item.media_id)
      .sort((a, b) => a.id.localeCompare(b.id))
      .map(item => ({ media_id: item.media_id!, name: item.name, url: item.image?.url ?? null })),
    { media_id: '9801', name: '共享示意图', url: image },
    ...(parameters.has('duplicateShared') ? [{ media_id: '9901', name: '共享的林晚', url: image }] : []),
  ], [selectedIds]);
  const [images, setImages] = useState<{ media_id: string }[]>([]);
  return <main className="web-episode"><div className="standard-shot-media" style={{ width: 550, padding: 20 }}>
    <PrivateReferenceImages draft={draft} disabled={false} readonlyReferences={readonlyReferences}
      availableReferences={available} onImagesChange={setImages}
      linkedAssets={{ assets, selectedIds, onChange: (id, selected) => {
        setCalls(current => [...current, `${id}:${selected}`]);
        setSelectedIds(current => selected ? [...current, id] : current.filter(value => value !== id));
      } }} />
    <output aria-label="关联素材">{selectedIds.join(',')}</output>
    <output aria-label="关联回调">{calls.join(',')}</output>
    <output aria-label="个人参考">{state.value.reference_media_ids.join(',')}</output>
    <output aria-label="提示词">{state.value.input_text}</output>
    <output aria-label="图片顺序">{images.map(item => item.media_id).join(',')}</output>
  </div></main>;
}
createRoot(document.getElementById('root')!).render(<ConfigProvider button={{ autoInsertSpace: false }}><Fixture /></ConfigProvider>);
