import type { EpisodeNavigationStage as StageId } from '../features/projects/episode-workflow';

export const aiConfigPath = '/ai_config';
export const accountPath = '/account';
export const projectPath = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
export const episodePath = (projectId: string, episodeId: string, stage?: StageId) =>
  `${projectPath(projectId)}/episodes/${encodeURIComponent(episodeId)}${stage ? `/${stage}` : ''}`;
