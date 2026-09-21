import { AssetLibraryPanel } from '../../../features/assets/AssetLibraryPanel';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';

export function AssetsStage({ value: _value, readOnly, projectId, episodeId }: { value: EpisodeWorkflow; readOnly: boolean; ready: boolean; projectId: string; episodeId: string; onChange: (next: EpisodeWorkflow) => void; onApply?: (change: (current: EpisodeWorkflow) => EpisodeWorkflow) => void }) {
  function downloadLegacy() {
    const content = localStorage.getItem(`avi-episode-workflow-v2-${projectId}-${episodeId}`) ?? '{}';
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `legacy-episode-${episodeId}-assets.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <AssetLibraryPanel scope={{ kind: 'episode', projectId, episodeId }} importFrom={{ kind: 'project', projectId }} shareTo={{ kind: 'project', projectId }} title="本集角色、场景与道具" readOnly={readOnly} legacyDownload={downloadLegacy}/>;
}
