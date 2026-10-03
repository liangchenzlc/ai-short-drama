import type { GenerationSummary } from '../../api/types/generations';

type TaskSourceTarget =
  | { kind: 'episode'; project_id: string; episode_id: string; stage: 'source' | 'assets' | 'storyboard' | 'assembly' }
  | { kind: 'project'; project_id: string }
  | { kind: 'asset'; asset_id: string };

const sourceStages: Partial<Record<NonNullable<GenerationSummary['source']>['scene'],
  Extract<TaskSourceTarget, { kind: 'episode' }>['stage']>> = {
  novel_script: 'source', script_assets: 'assets', asset_image: 'assets',
  script_shots: 'storyboard', shot_image: 'storyboard', shot_video: 'storyboard',
  dialogue_extract: 'assembly', dialogue_audio: 'assembly',
};

/** Read the server's frozen source identifiers, never infer identity from display names. */
export function taskSourceTarget(task: Pick<GenerationSummary, 'source'>): TaskSourceTarget | null {
  const source = task.source;
  if (!source) return null;
  const stage = sourceStages[source.scene];
  if (stage && source.project_id && source.episode_id) {
    return { kind: 'episode', project_id: source.project_id, episode_id: source.episode_id, stage };
  }
  if ((source.scene === 'asset_image' || source.scene === 'character_voice_design') && source.project_id) {
    return { kind: 'project', project_id: source.project_id };
  }
  if (source.scene === 'asset_image') return { kind: 'asset', asset_id: source.asset_id };
  return null;
}
