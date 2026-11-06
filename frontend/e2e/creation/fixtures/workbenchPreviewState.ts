import { remapAssetPromptReferences } from '@host/features/creation/assets/images/asset-prompt-references';
import type { AssetPromptReference } from '@host/types/creation/assets/images/asset-prompt-references';
import type { AssetView, EpisodeView } from '@host/types/creation/workspace/workbenchView';
import { useRef, useState } from 'react';
import { createPreviewData, group, media } from './workbenchPreviewData';

type OutlineDraft = Pick<EpisodeView, 'title' | 'summary'>;

export function useWorkbenchPreviewState() {
  const [initial] = useState(() => createPreviewData(new URLSearchParams(window.location.search).get('scenario') ?? 'single'));
  const [story, setStory] = useState(initial.story);
  const [episodes, setEpisodes] = useState(initial.episodes);
  const [assets, setAssets] = useState(initial.assets);
  const [shots, setShots] = useState(initial.shots);
  const [episodeId, setEpisodeId] = useState(initial.episodes[0].id);
  const [assetId, setAssetId] = useState(initial.assets[0].id);
  const [shotId, setShotId] = useState(initial.shots[0].id);
  const [outlineDraft, setOutlineDraft] = useState<OutlineDraft[] | null>(null);
  const [previewByAsset, setPreviewByAsset] = useState<Record<string, string>>({});
  const [assetReferences, setAssetReferences] = useState<Record<string, AssetPromptReference[]>>(() => {
    const scene = initial.assets.find((asset) => asset.name === '咖啡馆');
    return scene?.adoptedImage ? { '1': [{ ...scene.adoptedImage, name: scene.name }] } : {};
  });
  const nextMediaId = useRef(1000);
  const nextEpisodeId = useRef(Math.max(...initial.episodes.map((episode) => Number(episode.id))) + 1);
  const newEpisode = (draft: OutlineDraft = { title: '', summary: '' }): EpisodeView =>
    ({ id: String(nextEpisodeId.current++), ...draft, script: '' });

  function changeAsset(id: string, patch: Partial<AssetView>) {
    setAssets((current) => current.map((asset) => asset.id === id ? { ...asset, ...patch } : asset));
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

  function addEpisode() {
    setEpisodes((current) => [...current, newEpisode()]);
  }
  function changeEpisode(id: string, patch: Partial<EpisodeView>) {
    setEpisodes((current) => current.map((episode) => episode.id === id ? { ...episode, ...patch } : episode));
  }
  function moveEpisode(id: string, direction: -1 | 1) {
    setEpisodes((current) => {
      const index = current.findIndex((episode) => episode.id === id);
      const next = [...current];
      [next[index], next[index + direction]] = [next[index + direction], next[index]];
      return next;
    });
  }
  function deleteEpisode(id: string) {
    const remaining = episodes.filter((episode) => episode.id !== id);
    if (!remaining.length) return;
    setEpisodes(remaining);
    setShots((current) => current.filter((shot) => shot.episodeId !== id));
    if (episodeId === id) setEpisodeId(remaining[0].id);
  }
  function generateOutlines(count: number) {
    setOutlineDraft(Array.from({ length: count }, (_, index) => ({
      title: '', summary: '第 ' + (index + 1) + ' 集：故事线索逐步展开。'
    })));
  }
  function applyOutlines() {
    if (!outlineDraft) return;
    const fillsFirst = !episodes[0].summary.trim();
    const drafts = fillsFirst ? outlineDraft.slice(1) : outlineDraft;
    const first = fillsFirst ? { ...episodes[0], ...outlineDraft[0] } : episodes[0];
    setEpisodes([first, ...episodes.slice(1), ...drafts.map((draft) => newEpisode(draft))]);
    setOutlineDraft(null);
  }

  return { story, setStory, episodes, setEpisodes, assets, setAssets, shots, setShots,
    episodeId, setEpisodeId, assetId, setAssetId, shotId, setShotId,
    outlineDraft, setOutlineDraft, addEpisode, changeEpisode, moveEpisode, deleteEpisode, generateOutlines, applyOutlines,
    previewByAsset, setPreviewByAsset, assetReferences, changeAsset, changeAssetReferences,
    selectAssetGroup, adoptAssetImage, generateAssetImage };
}