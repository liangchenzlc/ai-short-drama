import { ScriptAssetExtraction } from '@host/features/creation/assets/extraction/ScriptAssetExtraction';
import { ProjectAssetsPanel } from '@host/features/creation/assets/list/ProjectAssetsPanel';
import type { WritingSession } from '@host/features/creation/writing/sessions/writing-session';
import type { AgentArtifactDetail } from '@host/types/assistant/artifacts';
import type { CreationPreferences } from '@host/types/creation/workspace/model';
import type { NavigationBarrier } from '@host/types/creation/writing/sessions/writing-navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import './index.css';
export function AssetsStage({
  value,
  readOnly,
  projectId,
  onChange,
  writingSession,
  onConfirmScript,
  registerBarrier,
  refreshToken = 0,
  externalReview,
}: {
  value: CreationPreferences;
  readOnly: boolean;
  projectId: string;
  onChange: (next: CreationPreferences) => void;
  onApply?: (change: (current: CreationPreferences) => CreationPreferences) => void;
  writingSession: WritingSession;
  onConfirmScript: () => void;
  registerBarrier: (barrier: NavigationBarrier | null) => void;
  refreshToken?: number;
  externalReview?: { artifact: AgentArtifactDetail; nonce: number } | null;
}) {
  const [revision, setRevision] = useState(0);
  const barriers = useRef<{
    library: NavigationBarrier | null;
    extraction: NavigationBarrier | null;
  }>({ library: null, extraction: null });
  const registerLibrary = useCallback((barrier: NavigationBarrier | null) => {
    barriers.current.library = barrier;
  }, []);
  const registerExtraction = useCallback((barrier: NavigationBarrier | null) => {
    barriers.current.extraction = barrier;
  }, []);
  useEffect(() => {
    registerBarrier({
      hasUnsettled: () => Object.values(barriers.current).some((barrier) => barrier?.hasUnsettled()),
      flush: async () => {
        for (const barrier of Object.values(barriers.current))
          if (barrier?.hasUnsettled() && !(await barrier.flush())) return false;
        return true;
      },
    });
    return () => registerBarrier(null);
  }, [registerBarrier]);
  return (
    <ProjectAssetsPanel
      variant="standard"
      scope={{ kind: 'project', projectId }}
      title="素材"
      readOnly={readOnly}
      refreshToken={revision + refreshToken}
      registerBarrier={registerLibrary}
      toolbar={
        <ScriptAssetExtraction
          presentation="standard-creation"
          projectId={projectId}
          session={writingSession}
          readOnly={readOnly}
          modelId={value.models.analysis}
          onModelChange={(id) => onChange({ ...value, models: { ...value.models, analysis: id } })}
          onApplied={() => setRevision((n) => n + 1)}
          onConfirmScript={onConfirmScript}
          registerBarrier={registerExtraction}
          externalReview={externalReview}
        />
      }
    />
  );
}
