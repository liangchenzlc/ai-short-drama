import type { GenerationSummary } from '../../api/types/generations';

type TaskSourceTarget =
  | { kind: 'project'; project_id: string;  stage: 'source' | 'assets' | 'storyboard' | 'assembly' }
  | { kind: 'asset'; asset_id: string };

const sourceStages: Partial<Record<NonNullable<GenerationSummary['source']>['scene'],
  Extract<TaskSourceTarget, { kind: 'project' }>['stage']>> = {
  novel_script: 'source', story_outline: 'source', script_generate: 'source', script_rewrite: 'source',
  script_assets: 'assets', asset_image: 'assets',
  script_shots: 'storyboard', shot_image: 'storyboard', shot_video: 'storyboard',
  dialogue_extract: 'assembly', dialogue_audio: 'assembly',
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
