import { useState } from 'react';
import { createPreviewData } from './workbenchPreviewData';

export function useWorkbenchPreviewState() {
  const [initial] = useState(() => createPreviewData(new URLSearchParams(window.location.search).get('scenario') ?? 'single'));
  const [story, setStory] = useState(initial.story);
  const [episodes, setEpisodes] = useState(initial.episodes);
  const [assets, setAssets] = useState(initial.assets);
  const [shots, setShots] = useState(initial.shots);
  const [episodeId, setEpisodeId] = useState(initial.episodes[0].id);
  const [assetId, setAssetId] = useState(initial.assets[0].id);
  const [shotId, setShotId] = useState(initial.shots[0].id);
  return { story, setStory, episodes, setEpisodes, assets, setAssets, shots, setShots,
    episodeId, setEpisodeId, assetId, setAssetId, shotId, setShotId };
}