import { useAuth } from '../../../features/auth/AuthSession';
import { CreationSlot } from '../../../features/projects/EpisodeCreationWorkspace';
import { AssetLibraryPanel } from '../../../features/assets/AssetLibraryPanel';
import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ScriptAssetExtraction } from '../../../features/projects/ScriptAssetExtraction';
import type { WritingSession } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';
import type { AgentArtifactDetail } from '../../../api/types/agent-artifacts';

export function AssetsStage({ value, readOnly, projectId, episodeId, onChange, writingSession, onConfirmScript, registerBarrier, refreshToken = 0, externalReview }: { value: EpisodeWorkflow; readOnly: boolean; ready: boolean; projectId: string; episodeId: string; onChange: (next: EpisodeWorkflow) => void; onApply?: (change: (current: EpisodeWorkflow) => EpisodeWorkflow) => void; writingSession: WritingSession; onConfirmScript: () => void; registerBarrier: (barrier: NavigationBarrier | null) => void; refreshToken?: number; externalReview?: { artifact: AgentArtifactDetail; nonce: number } | null }) {
  const auth = useAuth();
  const [revision, setRevision] = useState(0);
  const barriers = useRef<{ library: NavigationBarrier | null; extraction: NavigationBarrier | null }>({ library: null, extraction: null });
  const registerLibrary = useCallback((barrier: NavigationBarrier | null) => { barriers.current.library = barrier; }, []);
  const registerExtraction = useCallback((barrier: NavigationBarrier | null) => { barriers.current.extraction = barrier; }, []);
  useEffect(() => {
    registerBarrier({ hasUnsettled: () => Object.values(barriers.current).some(barrier => barrier?.hasUnsettled()), flush: async () => {
      for (const barrier of Object.values(barriers.current)) if (barrier?.hasUnsettled() && !await barrier.flush()) return false;
      return true;
    } });
    return () => registerBarrier(null);
  }, [registerBarrier]);
  function downloadLegacy() {
    const content = localStorage.getItem(`avi-episode-workflow-v2-${projectId}-${episodeId}`) ?? '{}';
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `legacy-episode-${episodeId}-assets.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <AssetLibraryPanel scope={{ kind: 'episode', projectId, episodeId }} importFrom={{ kind: 'project', projectId }} shareTo={{ kind: 'project', projectId }} title="本集角色、场景与道具" readOnly={readOnly} legacyDownload={auth.enabled ? undefined : downloadLegacy} refreshToken={revision + refreshToken} registerBarrier={registerLibrary}
    toolbar={<CreationSlot stage="assets"><ScriptAssetExtraction embedded projectId={projectId} episodeId={episodeId} session={writingSession} readOnly={readOnly} modelId={value.models.analysis} onModelChange={id => onChange({ ...value, models: { ...value.models, analysis: id } })} onApplied={() => setRevision(n => n + 1)} onConfirmScript={onConfirmScript} registerBarrier={registerExtraction} externalReview={externalReview}/></CreationSlot>}/>;
}
