import type { CreationStage } from '@host/types/shared/navigation/paths';
export const creationStages: readonly CreationStage[] = ['source', 'assets', 'storyboard', 'assembly'];
export const aiConfigPath = '/ai_config';
export const skillsPath = '/skills';
export const accountPath = '/account';
export const projectPath = (projectId: string, stage?: CreationStage) =>
  `/projects/${encodeURIComponent(projectId)}${stage ? `/${stage}` : ''}`;
