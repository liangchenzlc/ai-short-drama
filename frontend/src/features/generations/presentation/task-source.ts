import type { GenerationSummary } from '@host/types/generations/api';
import type { TaskSourceTarget } from '@host/types/generations/presentation/task-source';
const sourceStages: Partial<
  Record<NonNullable<GenerationSummary['source']>['scene'], Extract<TaskSourceTarget, { kind: 'project' }>['stage']>
> = {
  script_rewrite: 'source',
  script_assets: 'assets',
  asset_image: 'assets',
  script_shots: 'storyboard',
  shot_image: 'storyboard',
  shot_video: 'storyboard',
  dialogue_extract: 'assembly',
  dialogue_audio: 'assembly',
};

/** Read the server's frozen source identifiers, never infer identity from display names. */
export function taskSourceTarget(task: Pick<GenerationSummary, 'source'>): TaskSourceTarget | null {
  const source = task.source;
  if (!source) return null;
  const stage = sourceStages[source.scene];
  if (stage && source.project_id) {
    return { kind: 'project', project_id: source.project_id, stage };
  }
  if ((source.scene === 'asset_image' || source.scene === 'character_voice_design') && source.project_id) {
    return { kind: 'project', project_id: source.project_id, stage: 'assets' };
  }
  if (source.scene === 'asset_image') return { kind: 'asset', asset_id: source.asset_id };
  return null;
}
