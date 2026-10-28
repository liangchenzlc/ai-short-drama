import type { EpisodeWorkflow } from '../../../features/projects/episode-workflow';
import { NovelScriptGeneration } from '../../../features/projects/NovelScriptGeneration';
import type { WritingSession, WritingSnapshot } from '../../../features/projects/writing-session';
import type { NavigationBarrier } from '../../../features/projects/writing-navigation';

export function SourceStage({ value, onChange, writing, readOnly, projectId, writingSession, accountId, registerBarrier }: {
  value: EpisodeWorkflow; writing: WritingSnapshot; readOnly: boolean;
  onChange: (next: EpisodeWorkflow) => void;
  projectId: string; writingSession: WritingSession;
  accountId?: string;
  registerBarrier?: (barrier: NavigationBarrier | null) => void;
}) {
  return <NovelScriptGeneration projectId={projectId} accountId={accountId} writing={writing} session={writingSession}
    readOnly={readOnly} registerBarrier={registerBarrier}
    model={/^\d+$/.test(value.models.script) ? value.models.script : ''}
    onModel={model => onChange({ ...value, models: { ...value.models, script: model } })}/>;
}
