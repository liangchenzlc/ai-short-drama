import type { StageId } from '../features/projects/episode-workflow';

export const projectPath = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`;
export const episodePath = (projectId: string, episodeId: string, stage?: StageId) =>
  `${projectPath(projectId)}/episodes/${encodeURIComponent(episodeId)}${stage ? `/${stage}` : ''}`;
