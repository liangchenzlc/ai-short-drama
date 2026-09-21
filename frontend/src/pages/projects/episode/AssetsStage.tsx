import { AssetLibraryPanel } from '../../../features/assets/AssetLibraryPanel';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { useState } from 'react';
import { ScriptAssetExtraction } from '../../../features/projects/ScriptAssetExtraction';
import type { WritingSession } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';

export function AssetsStage({ value, readOnly, projectId, episodeId, onChange, writingSession, onConfirmScript, registerBarrier }: { value: EpisodeWorkflow; readOnly: boolean; ready: boolean; projectId: string; episodeId: string; onChange: (next: EpisodeWorkflow) => void; onApply?: (change: (current: EpisodeWorkflow) => EpisodeWorkflow) => void; writingSession: WritingSession; onConfirmScript: () => void; registerBarrier: (barrier: NavigationBarrier | null) => void }) {
  const [revision, setRevision] = useState(0);
  function downloadLegacy() {
    const content = localStorage.getItem(`avi-episode-workflow-v2-${projectId}-${episodeId}`) ?? '{}';
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `legacy-episode-${episodeId}-assets.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <AssetLibraryPanel scope={{ kind: 'episode', projectId, episodeId }} importFrom={{ kind: 'project', projectId }} shareTo={{ kind: 'project', projectId }} title="本集角色、场景与道具" readOnly={readOnly} legacyDownload={downloadLegacy} refreshToken={revision}
    toolbar={<ScriptAssetExtraction projectId={projectId} episodeId={episodeId} session={writingSession} readOnly={readOnly} modelId={value.models.analysis} onModelChange={id => onChange({ ...value, models: { ...value.models, analysis: id } })} onApplied={() => setRevision(n => n + 1)} onConfirmScript={onConfirmScript} registerBarrier={registerBarrier}/>}/>;
}
