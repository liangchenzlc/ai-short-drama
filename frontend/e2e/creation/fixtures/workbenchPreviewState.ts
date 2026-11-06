import type { EpisodeView } from '@host/types/creation/workspace/workbenchView';
import { useRef, useState } from 'react';
import { createPreviewData } from './workbenchPreviewData';

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
  const nextEpisodeId = useRef(Math.max(...initial.episodes.map((episode) => Number(episode.id))) + 1);
  const newEpisode = (draft: OutlineDraft = { title: '', summary: '' }): EpisodeView =>
    ({ id: String(nextEpisodeId.current++), ...draft, script: '' });

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
    outlineDraft, setOutlineDraft, addEpisode, changeEpisode, moveEpisode, deleteEpisode, generateOutlines, applyOutlines };
}