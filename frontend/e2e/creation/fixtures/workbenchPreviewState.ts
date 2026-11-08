import { remapAssetPromptReferences } from '@host/features/creation/assets/images/asset-prompt-references';
import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { AssetView, BatchItemView, ShotView, VideoReference } from '@host/types/creation/workspace/workbenchView';
import type { ImageGroup, ImageMedia } from '@host/types/media/image-groups';
import type { BatchScene } from '@host/types/generations/batches/requests';
import { useEffect, useRef, useState } from 'react';
import { createPreviewData, group, media } from './workbenchPreviewData';

type PreviewBatch = { id: string; scene: BatchScene; items: BatchItemView[] };

export function useWorkbenchPreviewState() {
  const [initial] = useState(() => createPreviewData(new URLSearchParams(window.location.search).get('scenario') ?? 'single'));
  const [story, setStory] = useState(initial.story);
  const [script, setScript] = useState(initial.script);
  const [assets, setAssets] = useState(initial.assets);
  const assetsRef = useRef(assets);
  useEffect(() => { assetsRef.current = assets; }, [assets]);
  const [shots, setShots] = useState(initial.shots);
  const [assetId, setAssetId] = useState(initial.assets[0].id);
  const [shotId, setShotId] = useState(initial.shots[0].id);
  const [previewByAsset, setPreviewByAsset] = useState<Record<string, string>>({});
  const [assetReferences, setAssetReferences] = useState<Record<string, AssetPromptReference[]>>((): Record<string, AssetPromptReference[]> => {
    const scene = initial.assets.find((asset) => asset.name === '咖啡馆');
    return scene?.adoptedImage ? { '1': [{ ...scene.adoptedImage, name: scene.name }] } : {};
  });
  const nextMediaId = useRef(1000);
  const [previewByShot, setPreviewByShot] = useState<Record<string, string>>({});
  const [imageStatuses, setImageStatuses] = useState<Record<string, string>>({});
  const [videoStatuses, setVideoStatuses] = useState<Record<string, string>>({});
  const [videoCandidates, setVideoCandidates] = useState<Record<string, NonNullable<ShotView['video']>[]>>({});
  const [batches, setBatches] = useState<PreviewBatch[]>([]);
  const nextAssetId = useRef(Math.max(...initial.assets.map((asset) => Number(asset.id))) + 1);
  const nextShotId = useRef(Math.max(...initial.shots.map((shot) => Number(shot.id))) + 1);

  function addAsset() {
    const id = String(nextAssetId.current++);
    const asset: AssetView = { id, kind: 'character', name: '', description: '', profile: {},
      imageGroups: [], activeGroupId: null, adoptedImage: null, imagePrompt: '' };
    setAssets((current) => [...current, asset]);
    setAssetId(id);
  }
  function removeAsset(id: string) {
    const removed = assets.find((asset) => asset.id === id)!;
    const remainingAssets = assets.filter((asset) => asset.id !== id);
    const mediaIds = new Set(removed.imageGroups.flatMap((imageGroup) => imageGroup.items.flatMap((item) => item.media_id ? [item.media_id] : [])));
    if (removed.adoptedImage) mediaIds.add(removed.adoptedImage.media_id);
    const remainingReferences = Object.fromEntries(Object.entries(assetReferences)
      .filter(([assetId]) => assetId !== id)
      .map(([assetId, references]) => [assetId, references.filter((reference) => !mediaIds.has(reference.media_id))]));
    setAssets((current) => current.filter((asset) => asset.id !== id).map((asset) => ({ ...asset,
      imagePrompt: remapAssetPromptReferences(asset.imagePrompt, assetReferences[asset.id] ?? [], remainingReferences[asset.id] ?? []),
    })));
    setAssetReferences(remainingReferences);
    setShots((current) => current.map((shot) => ({ ...shot, assetIds: shot.assetIds.filter((assetId) => assetId !== id),
      imagePrompt: remapAssetPromptReferences(shot.imagePrompt, shotPromptReferences(assets, shot.assetIds),
        shotPromptReferences(remainingAssets, shot.assetIds)),
      videoReferences: shot.videoReferences.filter((reference) => reference.type !== 'asset' || reference.assetId !== id) })));
  }
  function addShot() {
    const id = String(nextShotId.current++);
    const shot: ShotView = { id, position: shots.length + 1,
      title: '', script: '', shotSize: null, durationMs: 5000, cameraAngle: '', cameraMovement: '', dialogue: '', sound: '', assetIds: [], imageGroups: [],
      activeGroupId: null, adoptedImage: null, imagePrompt: '', videoPrompt: '', videoReferences: [], video: null };
    setShots((current) => [...current, shot]);
    setShotId(id);
  }
  function removeShot(id: string) {
    setShots((current) => {
      return current.filter((shot) => shot.id !== id).map((shot, index) => ({ ...shot, position: index + 1 }));
    });
  }
  function changeShotAssets(id: string, assetIds: string[]) {
    const shot = shots.find((item) => item.id === id);
    if (!shot) return;
    const added: VideoReference[] = assetIds.filter((assetId) => !shot.assetIds.includes(assetId) &&
      !shot.videoReferences.some((reference) => reference.type === 'asset' && reference.assetId === assetId) &&
      assets.some((asset) => asset.id === assetId && asset.kind !== 'prop'))
      .map((assetId) => ({ type: 'asset', assetId }));
    changeShot(id, { assetIds, videoReferences: [...shot.videoReferences, ...added],
      imagePrompt: remapAssetPromptReferences(shot.imagePrompt, shotPromptReferences(assets, shot.assetIds), shotPromptReferences(assets, assetIds)) });
  }
  function changeAsset(id: string, patch: Partial<AssetView>) {
    if (patch.adoptedImage !== undefined) {
      remapShotImagePrompts(assets, assets.map((asset) => asset.id === id ? { ...asset, ...patch } : asset));
    }
    setAssets((current) => current.map((asset) => asset.id === id ? { ...asset, ...patch } : asset));
  }
  function remapShotImagePrompts(before: AssetView[], after: AssetView[]) {
    setShots((current) => current.map((shot) => ({ ...shot,
      imagePrompt: remapAssetPromptReferences(shot.imagePrompt, shotPromptReferences(before, shot.assetIds),
        shotPromptReferences(after, shot.assetIds)),
    })));
  }
  function changeAssetReferences(id: string, references: AssetPromptReference[]) {
    const asset = assets.find((item) => item.id === id);
    if (asset) changeAsset(id, { imagePrompt: remapAssetPromptReferences(asset.imagePrompt, assetReferences[id] ?? [], references) });
    setAssetReferences((current) => ({ ...current, [id]: references }));
  }
  function selectAssetGroup(id: string, groupId: string) {
    const asset = assets.find((item) => item.id === id);
    const selected = asset?.imageGroups.find((item) => item.id === groupId);
    changeAsset(id, { activeGroupId: groupId });
    const first = selected?.items.find((item) => item.media_id)?.media_id;
    if (first) setPreviewByAsset((current) => ({ ...current, [id]: first }));
  }
  function adoptAssetImage(id: string, mediaId: string) {
    const item = assets.find((asset) => asset.id === id)?.imageGroups.flatMap((imageGroup) => imageGroup.items)
      .find((image) => image.media_id === mediaId);
    if (item?.media_id) changeAsset(id, { adoptedImage: {
      media_id: item.media_id, url: item.url, width: item.width, height: item.height,
    } });
  }
  function generateAssetImage(id: string) {
    const asset = assets.find((item) => item.id === id);
    if (!asset) return;
    const images = ['woman.jpg', 'camera.jpg', 'street.jpg', 'woman.jpg'].map((file) => media(String(nextMediaId.current++), file));
    const generated = group(String(nextMediaId.current++), 'asset', id, images);
    changeAsset(id, { imageGroups: [generated, ...asset.imageGroups], activeGroupId: generated.id,
      adoptedImage: asset.adoptedImage ?? images[0] });
    setPreviewByAsset((current) => ({ ...current, [id]: images[0].media_id }));
  }

  function changeShot(id: string, patch: Partial<ShotView>) {
    setShots((current) => current.map((shot) => shot.id === id ? { ...shot, ...patch } : shot));
  }
  function changeVideoReference(id: string, reference: VideoReference, selected: boolean) {
    const shot = shots.find((item) => item.id === id);
    if (!shot) return;
    const same = (item: VideoReference) => item.type === reference.type &&
      (item.type === 'asset' && reference.type === 'asset' ? item.assetId === reference.assetId :
        item.type === 'shotImage' && reference.type === 'shotImage' && item.mediaId === reference.mediaId);
    const remaining = shot.videoReferences.filter((item) => !same(item) &&
      !(selected && reference.type === 'shotImage' && item.type === 'shotImage'));
    changeShot(id, { videoReferences: selected ? [...remaining, reference] : remaining });
  }
  function selectShotGroup(id: string, groupId: string) {
    const selected = shots.find((item) => item.id === id)?.imageGroups.find((item) => item.id === groupId);
    changeShot(id, { activeGroupId: groupId });
    const first = selected?.items.find((item) => item.media_id)?.media_id;
    if (first) setPreviewByShot((current) => ({ ...current, [id]: first }));
  }
  function adoptShotImage(id: string, mediaId: string) {
    const item = shots.find((shot) => shot.id === id)?.imageGroups.flatMap((imageGroup) => imageGroup.items)
      .find((image) => image.media_id === mediaId);
    if (item?.media_id) changeShot(id, { adoptedImage: {
      media_id: item.media_id, url: item.url, width: item.width, height: item.height,
    } });
  }
  function generateShotImage(id: string) {
    const shot = shots.find((item) => item.id === id);
    if (!shot) return;
    const images = ['street.jpg', 'camera.jpg'].map((file) => media(String(nextMediaId.current++), file));
    const generated = group(String(nextMediaId.current++), 'shot', id, images);
    changeShot(id, { imageGroups: [generated, ...shot.imageGroups], activeGroupId: generated.id,
      adoptedImage: shot.adoptedImage ?? images[0] });
    setPreviewByShot((current) => ({ ...current, [id]: images[0].media_id }));
    setImageStatuses((current) => ({ ...current, [id]: '已完成' }));
  }
  function generateShotVideo(id: string) {
    const shot = shots.find((item) => item.id === id);
    if (!shot) return;
    const video = { mediaId: String(nextMediaId.current++), url: '/.runtime/timeline-fixture.mp4' };
    if (shot.video) setVideoCandidates((current) => ({ ...current, [id]: [...(current[id] ?? []), video] }));
    else changeShot(id, { video });
    setVideoStatuses((current) => ({ ...current, [id]: '已完成' }));
  }
  function adoptShotVideo(id: string, mediaId: string) {
    const shot = shots.find((item) => item.id === id);
    const candidate = videoCandidates[id]?.find((item) => item.mediaId === mediaId);
    if (!shot || !candidate) return;
    setVideoCandidates((current) => ({ ...current, [id]: [
      ...(current[id] ?? []).filter((item) => item.mediaId !== mediaId), ...(shot.video ? [shot.video] : []),
    ] }));
    changeShot(id, { video: candidate });
    setVideoStatuses((current) => ({ ...current, [id]: '已采用' }));
  }

  function retryImageItem(kind: 'asset' | 'shot', targetId: string, itemId: string) {
    const image = media(String(nextMediaId.current++), kind === 'asset' ? 'camera.jpg' : 'street.jpg');
    function update(status: 'generating' | 'ready', result?: ImageMedia) {
      const updateGroups = (groups: ImageGroup[]) => groups.map((imageGroup) => {
        if (!imageGroup.items.some((item) => item.id === itemId)) return imageGroup;
        const items = imageGroup.items.map((item) => item.id === itemId ?
          { ...item, ...result, status, error: null } : item);
        return { ...imageGroup, items, status: status === 'generating' ? 'generating' as const :
          items.some((item) => item.status === 'failed') ? 'partial' as const : 'completed' as const };
      });
      if (kind === 'asset') setAssets((current) => current.map((item) =>
        item.id === targetId ? { ...item, imageGroups: updateGroups(item.imageGroups) } : item));
      else setShots((current) => current.map((item) =>
        item.id === targetId ? { ...item, imageGroups: updateGroups(item.imageGroups) } : item));
    }
    update('generating');
    setTimeout(() => update('ready', image), 300);
  }

  function applyBatchResults(scene: BatchScene, ids: string[]) {
    if (!ids.length) return;
    if (scene === 'asset_image') {
      const results = new Map(ids.map((id) => {
        const image = media(String(nextMediaId.current++), 'woman.jpg');
        return [id, { image, group: group(String(nextMediaId.current++), 'asset', id, [image]) }] as const;
      }));
      const currentAssets = assetsRef.current;
      const nextAssets = currentAssets.map((item) => {
        const result = results.get(item.id);
        return result ? { ...item, adoptedImage: item.adoptedImage ?? result.image } : item;
      });
      remapShotImagePrompts(currentAssets, nextAssets);
      assetsRef.current = nextAssets;
      setAssets((current) => current.map((item) => {
        const result = results.get(item.id);
        return result ? { ...item, imageGroups: [result.group, ...item.imageGroups],
          activeGroupId: result.group.id, adoptedImage: item.adoptedImage ?? result.image } : item;
      }));
      return;
    }
    if (scene === 'shot_image') {
      const results = new Map(ids.map((id) => {
        const image = media(String(nextMediaId.current++), 'street.jpg');
        return [id, { image, group: group(String(nextMediaId.current++), 'shot', id, [image]) }] as const;
      }));
      setShots((current) => current.map((item) => {
        const result = results.get(item.id);
        return result ? { ...item, imageGroups: [result.group, ...item.imageGroups],
          activeGroupId: result.group.id, adoptedImage: item.adoptedImage ?? result.image } : item;
      }));
      return;
    }
    const results = new Map(ids.map((id) => [id, {
      mediaId: String(nextMediaId.current++), url: '/.runtime/timeline-fixture.mp4',
    }] as const));
    setShots((current) => current.map((item) => {
      const result = results.get(item.id);
      return result && !item.video ? { ...item, video: result } : item;
    }));
    const existing = shots.filter((item) => results.has(item.id) && item.video);
    if (existing.length) setVideoCandidates((current) => {
      const next = { ...current };
      for (const item of existing) next[item.id] = [...(next[item.id] ?? []), results.get(item.id)!];
      return next;
    });
  }
  function scheduleBatch(batch: PreviewBatch, targets: string[], failLast: boolean) {
    const chunks = Array.from({ length: Math.ceil(targets.length / 100) }, (_, index) =>
      targets.slice(index * 100, index * 100 + 100));
    chunks.forEach((chunk, index) => {
      const chosen = new Set(chunk);
      setTimeout(() => setBatches((current) => current.map((item) => item.id !== batch.id ? item : {
        ...item, items: item.items.map((entry) => chosen.has(entry.targetId) ? { ...entry, status: 'running' } : entry),
      })), 100 + index * 80);
      setTimeout(() => {
        const failedId = failLast && index === chunks.length - 1 ? chunk[chunk.length - 1] : null;
        setBatches((current) => current.map((item) => item.id !== batch.id ? item : {
          ...item, items: item.items.map((entry) => !chosen.has(entry.targetId) ? entry : {
            ...entry, status: entry.targetId === failedId ? 'failed' : 'succeeded',
            error: entry.targetId === failedId ? '模拟生成失败，请重试' : '',
          }),
        }));
        applyBatchResults(batch.scene, chunk.filter((id) => id !== failedId));
      }, 350 + index * 150);
    });
  }
  function startBatch(scene: BatchScene, targets: string[]) {
    const batchId = String(nextMediaId.current++);
    const batch: PreviewBatch = { id: batchId, scene, items: targets.map((targetId) => ({
      id: String(nextMediaId.current++), targetId, scene, status: 'queued', error: '',
    })) };
    setBatches((current) => [batch, ...current]);
    scheduleBatch(batch, targets, targets.length > 100);
  }
  function retryBatch(batchId: string) {
    const batch = batches.find((item) => item.id === batchId);
    if (!batch) return;
    const targets = batch.items.filter((item) => item.status === 'failed').map((item) => item.targetId);
    const chosen = new Set(targets);
    setBatches((current) => current.map((item) => item.id !== batchId ? item : {
      ...item, items: item.items.map((entry) => chosen.has(entry.targetId) ? { ...entry, status: 'queued', error: '' } : entry),
    }));
    scheduleBatch(batch, targets, false);
  }

  return { story, setStory, script, setScript, assets, shots,
    assetId, setAssetId, shotId, setShotId,
    previewByAsset, setPreviewByAsset, assetReferences, changeAsset, changeAssetReferences,
    selectAssetGroup, adoptAssetImage, generateAssetImage, previewByShot, setPreviewByShot, imageStatuses, videoStatuses,
    videoCandidates, changeShot, changeShotAssets, changeVideoReference, selectShotGroup, adoptShotImage, generateShotImage, generateShotVideo, adoptShotVideo, batches, startBatch, retryBatch, retryImageItem, addAsset, removeAsset, addShot, removeShot };
}

function shotPromptReferences(assets: AssetView[], assetIds: string[]): AssetPromptReference[] {
  return assets.filter((asset) => assetIds.includes(asset.id) && asset.adoptedImage)
    .map((asset) => ({ ...asset.adoptedImage!, media_id: asset.id, name: asset.name }));
}
