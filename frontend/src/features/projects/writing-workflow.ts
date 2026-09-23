import type { EpisodeWorkflow } from './episode-workflow';
import type { WritingSnapshot } from './writing-session';

/** Project server writing into the stage view without changing the browser legacy snapshot. */
export function projectWritingWorkflow(local: EpisodeWorkflow, writing: Pick<WritingSnapshot, 'loaded' | 'novel' | 'script' | 'confirmed'>): EpisodeWorkflow {
  const novel = writing.loaded ? writing.novel : '';
  const script = writing.loaded ? writing.script : '';
  const confirmed = writing.loaded && writing.confirmed && !!script.trim();
  return {
    ...local, novel, scriptDraft: script, scriptCandidates: [],
    approvedScript: confirmed ? { text: script, aspect: local.aspect, style: local.style } : null,
    reviews: { ...local.reviews, source: novel.trim() || script.trim() ? 'review' : 'not_started', script: confirmed ? 'confirmed' : script.trim() ? 'review' : 'not_started' },
  };
}

/** Never write projected server text or confirmation back over the legacy browser snapshot. */
export function retainLocalWorkflow(local: EpisodeWorkflow, changed: EpisodeWorkflow): EpisodeWorkflow {
  return {
    ...changed,
    novel: local.novel, scriptDraft: local.scriptDraft, scriptCandidates: local.scriptCandidates,
    approvedScript: local.approvedScript,
    reviews: { ...changed.reviews, source: local.reviews.source, script: local.reviews.script },
  };
}
