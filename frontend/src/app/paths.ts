export type CreationStage = 'source' | 'assets' | 'storyboard' | 'assembly';
export const creationStages: readonly CreationStage[] = ['source', 'assets', 'storyboard', 'assembly'];
export const aiConfigPath = '/ai_config';
export const accountPath = '/account';
export const projectPath = (projectId: string, stage?: CreationStage) =>
  `/projects/${encodeURIComponent(projectId)}${stage ? `/${stage}` : ''}`;
